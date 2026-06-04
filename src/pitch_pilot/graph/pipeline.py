"""The deterministic outer pipeline for pitch-pilot (built in P1).

pitch-pilot uses a **hybrid** architecture:

    * a **deterministic outer graph** wires the fixed business steps in a known
      order — ``research → qualify → (gate) → draft → verify → log`` — so the
      control flow is auditable and reproducible; and
    * an **agentic research sub-loop** lives *inside* the research node, where the
      model is free to choose and refine search queries until it has gathered
      enough grounded facts.

This module assembles that outer graph with LangGraph on top of the typed
`PipelineState` contract. For P0 it is a
documented stub — the state contract is the only graph artifact that exists yet.
"""

from __future__ import annotations

from typing import Any


def build_pipeline() -> Any:
    """Build and compile the LangGraph pipeline.

    Not implemented in P0. The deterministic outer graph
    (``research → qualify → draft → verify → log``) is constructed in P1 on top
    of `PipelineState`.

    Returns:
        A compiled LangGraph application (in P1).

    Raises:
        NotImplementedError: always, until P1.
    """
    raise NotImplementedError(
        "build_pipeline() is a P0 stub. The LangGraph outer graph is built in P1 "
        "on top of pitch_pilot.graph.state.PipelineState."
    )
