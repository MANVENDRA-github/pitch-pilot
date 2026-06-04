"""Pipeline nodes for pitch-pilot.

A *node* is the unit of work executed by the LangGraph pipeline (built in P1+).
Each node takes the `PipelineState`, performs one
step, and returns the updated state. This package is intentionally empty in P0 —
only the typed state contract exists yet.

Planned nodes (P1+):
    * ``research_node``   — run the agentic research sub-loop → ``ResearchResult``
    * ``qualify_node``    — score the company against the ICP → ``QualificationResult``
    * ``draft_node``      — write grounded outreach from facts → ``Draft``
    * ``verify_node``     — check every claim against its source → ``VerificationResult``
    * ``log_node``        — persist the lead + enqueue it for human review
    * ``discover_node``   — (future seam) find new candidate domains to seed runs
"""
