"""The `Company` and `Lead` models — the subject of a run."""

from __future__ import annotations

from pydantic import BaseModel

from pitch_pilot.models.draft import Draft
from pitch_pilot.models.qualification import QualificationResult
from pitch_pilot.models.verification import VerificationResult


class Company(BaseModel):
    """The company being researched and qualified.

    Attributes:
        domain: The company's primary domain, e.g. ``"acme.com"``. This is the
            single required seed input for an entire pipeline run.
        name: Display name, if known or resolved during research.
    """

    domain: str
    name: str | None = None


class Lead(BaseModel):
    """A lead: the `Company` plus the artifacts a pipeline run produced for it.

    A ``Lead`` is what the `Store` persists at the end of a run. The artifact
    fields are optional because a lead can be logged at different stages: a
    disqualified company carries only its ``qualification``, while a fully
    processed one carries the ``draft`` and ``verification`` too. During the run
    these same artifacts live on the `PipelineState`; the log node copies the
    final ones onto the ``Lead`` so the persisted record is self-contained.

    Attributes:
        company: The company this lead is about.
        qualification: The ICP verdict, if the qualify node ran.
        draft: The outreach draft, if the draft node ran.
        verification: The groundedness audit, if the verify node ran.
        status: The terminal outcome — e.g. ``"ready"``, ``"review"``,
            ``"disqualified"``.
    """

    company: Company
    qualification: QualificationResult | None = None
    draft: Draft | None = None
    verification: VerificationResult | None = None
    status: str = "pending"
