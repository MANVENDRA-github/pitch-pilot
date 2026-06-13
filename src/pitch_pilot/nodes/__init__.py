"""Pipeline nodes for pitch-pilot.

A *node* is the unit of work executed by the LangGraph pipeline. Each node takes
the `PipelineState`, performs one step, and returns the partial state update to
merge in.

Implemented:
    * ``research_node`` / `run_research` — the agentic research sub-loop that
      gathers grounded `Fact`s → ``ResearchResult`` (P1).
    * ``qualify_node`` / `run_qualification` — score the company against the ICP →
      ``QualificationResult`` (P2).
    * ``draft_node`` / `run_draft` — write grounded outreach from facts →
      ``Draft`` (P2).
    * ``verify_node`` / `run_verification` / `judge_faithfulness` — the groundedness
      gate over a draft (first-party tier + substring + LLM faithfulness judge) →
      ``VerificationResult`` (P2 basic gate, hardened in P3).
    * ``log_node`` / `log_lead` — persist the lead + enqueue it for human review
      (P2).

Planned (later phases):
    * ``discover_node``   — (future seam) find new candidate domains to seed runs
"""

from pitch_pilot.nodes.draft import draft_node, run_draft
from pitch_pilot.nodes.log import log_lead, log_node
from pitch_pilot.nodes.qualify import qualify_node, run_qualification
from pitch_pilot.nodes.research import (
    classify_source_tier,
    extract_facts,
    research_node,
    run_research,
)
from pitch_pilot.nodes.verify import judge_faithfulness, run_verification, verify_node

__all__ = [
    "run_research",
    "research_node",
    "extract_facts",
    "classify_source_tier",
    "run_qualification",
    "qualify_node",
    "run_draft",
    "draft_node",
    "run_verification",
    "verify_node",
    "judge_faithfulness",
    "log_lead",
    "log_node",
]
