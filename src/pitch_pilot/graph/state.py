"""The typed state contract that flows through the pipeline.

`PipelineState` is the single object passed from node to node. Each node
reads what it needs and fills in its slice of the state, so the artifacts
accumulate as the run progresses:

    company + icp                      (seed / inputs)
        → research                     (filled by the research node)
        → qualification                (filled by the qualify node)
        → draft                        (filled by the draft node)
        → verification                 (filled by the verify node)

The optional fields are ``None`` until their producing node runs, which makes
the state safe to construct at the very start of a run with only the seed inputs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from pitch_pilot.models.draft import Draft
from pitch_pilot.models.icp import ICP
from pitch_pilot.models.lead import Company
from pitch_pilot.models.qualification import QualificationResult
from pitch_pilot.models.research import ResearchResult
from pitch_pilot.models.verification import VerificationResult


class PipelineState(BaseModel):
    """Mutable, fully-typed state for one pipeline run.

    Attributes:
        company: The company being processed (the seed input).
        icp: The Ideal Customer Profile to qualify against (an input).
        research: Grounded facts, once the research node has run.
        qualification: ICP verdict, once the qualify node has run.
        draft: Outreach draft, once the draft node has run.
        verification: Groundedness audit, once the verify node has run.
        status: Coarse run status, e.g. ``"pending"``, ``"running"``,
            ``"qualified"``, ``"disqualified"``, ``"done"``, ``"error"``.
        errors: Accumulated, non-fatal error messages from nodes.
    """

    company: Company
    icp: ICP
    research: ResearchResult | None = None
    qualification: QualificationResult | None = None
    draft: Draft | None = None
    verification: VerificationResult | None = None
    status: str = "pending"
    errors: list[str] = Field(default_factory=list)
