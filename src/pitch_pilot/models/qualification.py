"""The `QualificationResult` model — the verdict of scoring vs. the ICP."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QualificationResult(BaseModel):
    """The outcome of qualifying a company against an `ICP`.

    Attributes:
        qualified: Whether the company passed the qualification gate.
        score: Fit score in ``[0, 1]``.
        reason: Short human-readable justification for the verdict.
        matched_signals: ICP signals the company satisfied.
        missed_signals: ICP signals the company failed or lacked.
    """

    qualified: bool
    score: float = Field(ge=0.0, le=1.0)
    reason: str
    matched_signals: list[str] = Field(default_factory=list)
    missed_signals: list[str] = Field(default_factory=list)
