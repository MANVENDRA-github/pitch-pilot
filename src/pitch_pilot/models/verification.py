"""The `VerificationResult` model — the groundedness audit of a draft."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VerificationResult(BaseModel):
    """The result of checking every claim in a `Draft`
    against a source.

    This is the enforcement point for the hero guarantee: a draft only passes if
    its groundedness score clears the configured threshold
    (``GROUNDEDNESS_THRESHOLD``).

    Attributes:
        groundedness_score: Fraction of claims that are grounded, in ``[0, 1]``.
        total_claims: Total number of factual claims detected in the draft.
        grounded_claims: Number of claims successfully traced to a source.
        flagged_claims: The specific claims that could **not** be grounded.
        passed: Whether the draft cleared the groundedness threshold.
    """

    groundedness_score: float = Field(ge=0.0, le=1.0)
    total_claims: int
    grounded_claims: int
    flagged_claims: list[str] = Field(default_factory=list)
    passed: bool
