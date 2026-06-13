"""The `VerificationResult` model — the groundedness audit of a draft."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: Outcome of the LLM faithfulness judge for a single claim↔evidence pair.
#:
#: * ``"faithful"`` — the evidence directly supports the claim as stated.
#: * ``"overreach"`` — the evidence partially supports it, but the claim
#:   generalizes or exaggerates beyond what the evidence says.
#: * ``"unsupported"`` — the evidence does not support the claim.
Faithfulness = Literal["faithful", "overreach", "unsupported"]


class ClaimVerdict(BaseModel):
    """The full audit trail for one claim in a draft.

    One of these is produced per claim the draft stands on, whether it passed or
    failed, so a reviewer can see exactly why each claim was accepted or rejected.

    Attributes:
        claim: The draft claim under audit (a `Draft` hook).
        fact_used: The claim text of the backing `Fact` chosen to support it, or
            ``None`` if no fact backs the claim.
        source_url: The backing fact's source URL, or ``None`` if unbacked.
        tier: The backing fact's `SourceTier`, or ``None`` if unbacked.
        substring_ok: Whether the backing fact carries a verbatim ``evidence``
            snippet (the extraction-time substring guard held). ``False`` when the
            backing fact has no evidence.
        faithfulness: The LLM judge's verdict for this claim↔evidence pair, or
            ``None`` when the claim failed an earlier check and was not judged.
    """

    claim: str
    fact_used: str | None = None
    source_url: str | None = None
    tier: str | None = None
    substring_ok: bool = False
    faithfulness: Faithfulness | None = None


class VerificationResult(BaseModel):
    """The result of auditing every claim in a `Draft` against its sources.

    This is the enforcement point for the hero guarantee. Under the P3 gate a draft
    passes only if **every** claim is *verified*: backed by a first-party
    (``own_site`` / ``authoritative``) `Fact` with a verbatim evidence snippet that
    an LLM judge rates as faithfully supporting the claim (see the groundedness
    methodology docs for the full picture).

    Attributes:
        groundedness_score: Fraction of claims that are fully verified, in
            ``[0, 1]`` (``verified_claims / total_claims``). Reported even when the
            draft passes.
        faithfulness_score: Fraction of claims the judge rated ``"faithful"``, in
            ``[0, 1]`` (``faithful_claims / total_claims``).
        total_claims: Total number of claims checked (the draft's hooks).
        grounded_claims: Number of claims that are fully verified (the numerator of
            ``groundedness_score``).
        tier_breakdown: Count of claims per backing source tier, e.g.
            ``{"own_site": 2, "unbacked": 1}``.
        claim_verdicts: The per-claim audit trail (see `ClaimVerdict`).
        flagged_claims: Human-readable failure lines for the claims that did **not**
            verify, each prefixed with the specific reason (``unbacked:`` /
            ``volatile-source:`` / ``not-substring:`` / ``overreach:`` /
            ``unsupported:``).
        passed: ``True`` only if there is at least one claim and every claim is
            verified.
    """

    groundedness_score: float = Field(ge=0.0, le=1.0)
    faithfulness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    total_claims: int
    grounded_claims: int
    tier_breakdown: dict[str, int] = Field(default_factory=dict)
    claim_verdicts: list[ClaimVerdict] = Field(default_factory=list)
    flagged_claims: list[str] = Field(default_factory=list)
    passed: bool
