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
    """The full audit trail for one claim the draft body makes about the company.

    Under the 0.8.0 gate the faithfulness judge extracts each factual claim the draft
    **body** makes about the company and rates it against the facts the draft is
    grounded in. One ``ClaimVerdict`` is produced per such body claim — whether it was
    judged faithful or not — so a reviewer can see exactly which fact (if any) backs
    each claim and how the judge rated it.

    Attributes:
        claim: The body claim under audit (extracted by the faithfulness judge).
        fact_used: The claim text of the supporting `Fact` the judge cited, or
            ``None`` when the claim is ``unsupported``.
        source_url: The supporting fact's source URL, or ``None`` when unsupported.
        tier: The supporting fact's `SourceTier`, or ``None`` when unsupported.
        substring_ok: Whether the supporting fact carries a verbatim ``evidence``
            snippet (the extraction-time substring guard held). ``False`` when there
            is no supporting fact.
        faithfulness: The LLM judge's verdict for this body claim
            (``faithful`` / ``overreach`` / ``unsupported``).
    """

    claim: str
    fact_used: str | None = None
    source_url: str | None = None
    tier: str | None = None
    substring_ok: bool = False
    faithfulness: Faithfulness | None = None


class VerificationResult(BaseModel):
    """The result of auditing a `Draft`: structural grounding plus body faithfulness.

    This is the enforcement point for the hero guarantee. Under the 0.8.0 gate a draft
    passes only if it is grounded in at least one first-party (``own_site`` /
    ``authoritative``) `Fact`, has a non-empty body, the faithfulness judge ran, and
    **no body claim is ``unsupported``** (and none is ``overreach`` when
    ``FAITHFULNESS_STRICT``). Source-text substring grounding lives at extraction, so
    the hooks are grounded by construction; this result audits the body's *support*
    (see the groundedness methodology docs for the full picture).

    Attributes:
        groundedness_score: Fraction of body claims that count as verified, in
            ``[0, 1]`` (``verified_claims / total_body_claims``), where a claim is
            verified when the judge rates it ``faithful`` (or ``overreach`` when
            ``FAITHFULNESS_STRICT`` is off). Under the default strict mode this equals
            ``faithful_claims / total_body_claims``. ``0.0`` when the draft is
            ungrounded or its body is empty; ``1.0`` when a grounded body makes no
            checkable company claim.
        faithfulness_score: Fraction of body claims the judge rated ``"faithful"``, in
            ``[0, 1]`` (``faithful_claims / total_body_claims``).
        total_claims: Number of body claims the judge extracted and checked.
        grounded_claims: Number of body claims that count as verified (the numerator
            of ``groundedness_score``).
        tier_breakdown: Count of the draft's grounding facts (hooks) per source tier,
            e.g. ``{"own_site": 2, "authoritative": 1}``.
        claim_verdicts: The per-body-claim audit trail (see `ClaimVerdict`).
        flagged_claims: Human-readable failure lines, each prefixed with the specific
            reason (``structural:`` / ``overreach:`` / ``unsupported:`` /
            ``judge-error:``).
        passed: ``True`` only if the draft is grounded, the body is non-empty, the
            judge ran, and no body claim failed.
    """

    groundedness_score: float = Field(ge=0.0, le=1.0)
    faithfulness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    total_claims: int
    grounded_claims: int
    tier_breakdown: dict[str, int] = Field(default_factory=dict)
    claim_verdicts: list[ClaimVerdict] = Field(default_factory=list)
    flagged_claims: list[str] = Field(default_factory=list)
    passed: bool
