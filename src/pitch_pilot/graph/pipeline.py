"""The deterministic outer pipeline for pitch-pilot (built in P2).

pitch-pilot uses a **hybrid** architecture:

    * a **deterministic outer graph** wires the fixed business steps in a known
      order — ``research → qualify → (gate) → draft → verify → log`` — so the
      control flow is auditable and reproducible; and
    * an **agentic research sub-loop** lives *inside* the research node, where the
      model is free to choose and refine search queries until it has gathered
      enough grounded facts.

This module assembles that outer graph with LangGraph on top of the typed
`PipelineState` contract. Two conditional gates shape the flow:

    * after **qualify**, a disqualified company skips drafting and goes straight to
      **log**; a qualified one proceeds to **draft**;
    * after **verify**, the single **log** node decides the outcome from the
      verification verdict — a passing draft is marked *ready*, a failing one is
      enqueued for *review*. Nothing is ever sent automatically.

`build_pipeline` accepts injectable clients/store so the whole graph can be run on
mocked dependencies with no network (used by the unit tests); by default it builds
the configured real clients.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from pitch_pilot.clients.llm import LLMClient, get_llm_client
from pitch_pilot.clients.search import SearchClient, get_search_client
from pitch_pilot.config import Settings, get_settings
from pitch_pilot.graph.state import PipelineState
from pitch_pilot.nodes.draft import draft_node
from pitch_pilot.nodes.log import log_node
from pitch_pilot.nodes.qualify import qualify_node
from pitch_pilot.nodes.research import research_node
from pitch_pilot.nodes.verify import verify_node
from pitch_pilot.storage.store import JsonStore, Store


def _route_after_qualify(state: PipelineState) -> str:
    """Route a qualified company to drafting, a disqualified one straight to log."""
    qualification = state.qualification
    return "draft" if (qualification is not None and qualification.qualified) else "log"


def build_pipeline(
    *,
    llm: LLMClient | None = None,
    search: SearchClient | None = None,
    store: Store | None = None,
    settings: Settings | None = None,
) -> Any:
    """Build and compile the deterministic LangGraph pipeline.

    Wires the five nodes over `PipelineState` with the two conditional gates
    described in the module docstring. Each node is bound to the supplied (or
    default) dependencies, so the compiled graph carries everything it needs.

    Args:
        llm: LLM client for research/qualify/draft; built from settings when omitted.
        search: Search client for research; built from settings when omitted.
        store: Store for the log node; a local `JsonStore` when omitted.
        settings: Settings; loaded via `get_settings` when omitted.

    Returns:
        A compiled LangGraph application. Invoke it with a `PipelineState` (or an
        equivalent mapping); it returns the final state as a mapping that
        `PipelineState.model_validate` can round-trip.
    """
    settings = settings or get_settings()
    llm = llm or get_llm_client(settings)
    search = search or get_search_client(settings)
    store = store or JsonStore()

    graph = StateGraph(PipelineState)
    graph.add_node("research", lambda s: research_node(s, llm=llm, search=search, settings=settings))
    graph.add_node("qualify", lambda s: qualify_node(s, llm=llm, settings=settings))
    graph.add_node("draft", lambda s: draft_node(s, llm=llm, settings=settings))
    graph.add_node("verify", lambda s: verify_node(s, llm=llm, settings=settings))
    graph.add_node("log", lambda s: log_node(s, store=store))

    graph.add_edge(START, "research")
    graph.add_edge("research", "qualify")
    graph.add_conditional_edges("qualify", _route_after_qualify, {"draft": "draft", "log": "log"})
    graph.add_edge("draft", "verify")
    graph.add_edge("verify", "log")
    graph.add_edge("log", END)

    return graph.compile()
