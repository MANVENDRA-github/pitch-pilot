"""Pipeline nodes for pitch-pilot.

A *node* is the unit of work executed by the LangGraph pipeline. Each node takes
the `PipelineState`, performs one step, and returns the partial state update to
merge in.

Implemented:
    * ``research_node`` / `run_research` — the agentic research sub-loop that
      gathers grounded `Fact`s → ``ResearchResult`` (P1).

Planned (later phases):
    * ``qualify_node``    — score the company against the ICP → ``QualificationResult``
    * ``draft_node``      — write grounded outreach from facts → ``Draft``
    * ``verify_node``     — check every claim against its source → ``VerificationResult``
    * ``log_node``        — persist the lead + enqueue it for human review
    * ``discover_node``   — (future seam) find new candidate domains to seed runs
"""

from pitch_pilot.nodes.research import (
    extract_facts,
    research_node,
    run_research,
)

__all__ = [
    "run_research",
    "research_node",
    "extract_facts",
]
