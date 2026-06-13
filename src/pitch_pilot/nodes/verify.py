"""The verify node — the real groundedness gate over a draft (P3).

This is the enforcement point for the hero guarantee, applied to the finished
draft: **every claim the draft stands on must be first-party-sourced,
substring-anchored, and judged to be faithfully supported by its evidence.** A
claim is *verified* only when all four hold:

1. **Backed** — the claim maps to a real `Fact` in the research.
2. **First-party tier** — that fact is ``own_site`` or ``authoritative`` (Policy B:
   a stated claim may never rest on a ``third_party_snippet`` fact).
3. **Substring-anchored** — the fact carries a verbatim ``evidence`` snippet (the
   extraction-time substring guard held; recorded on the fact).
4. **Faithful** — an LLM judge rates the claim↔evidence pair ``"faithful"`` (or
   ``"overreach"`` when ``FAITHFULNESS_STRICT`` is off). This is *distinct* from the
   substring check: substring proves the evidence is present, the judge decides
   whether it actually *supports* the claim as stated.

The draft **passes only if every claim is verified.** Each claim that fails is
recorded with its specific reason (``unbacked`` / ``volatile-source`` /
``not-substring`` / ``overreach`` / ``unsupported``), and a full per-claim audit
trail is returned in `VerificationResult.claim_verdicts`.

The node is **network-free except for the LLM judge call** — it does not re-fetch
sources. Independent live re-verification of each source is an *eval-time* metric
(P4), reported separately by tier, not part of the per-run hot path (see ADR-0010
in the decisions log).
"""

from __future__ import annotations

import logging

from pitch_pilot.clients.llm import LLMClient, LLMError, get_llm_client
from pitch_pilot.config import Settings, get_settings
from pitch_pilot.graph.state import PipelineState
from pitch_pilot.models.draft import Draft
from pitch_pilot.models.fact import Fact
from pitch_pilot.models.research import ResearchResult
from pitch_pilot.models.verification import ClaimVerdict, VerificationResult
from pitch_pilot.nodes.research import _normalize

logger = logging.getLogger(__name__)

# Tiers a stated claim may rest on (Policy B). A claim backed only by a
# third_party_snippet fact is a policy violation, not merely a soft flag.
_FIRST_PARTY_TIERS = {"own_site", "authoritative"}
_TIER_RANK = {"own_site": 0, "authoritative": 1, "third_party_snippet": 2}

_FAITHFULNESS_SYSTEM = (
    "You are a strict groundedness judge for an SDR agent. You are given a CLAIM "
    "and an EVIDENCE snippet copied verbatim from a source. Decide ONLY whether the "
    "EVIDENCE supports the CLAIM — never use outside knowledge.\n\n"
    "Verdicts:\n"
    "- 'faithful': the EVIDENCE directly supports the CLAIM as stated.\n"
    "- 'overreach': the EVIDENCE partially supports it, but the CLAIM generalizes, "
    "exaggerates, or adds beyond what the EVIDENCE actually says.\n"
    "- 'unsupported': the EVIDENCE does not support the CLAIM.\n\n"
    'Respond with a JSON object: {"verdict": "faithful"|"overreach"|"unsupported", '
    '"reason": "<one short sentence>"}'
)


def judge_faithfulness(claim: str, evidence: str, llm: LLMClient) -> dict:
    """Ask the LLM whether ``evidence`` actually supports ``claim``.

    This judges *support*, which the substring check cannot: the evidence can be a
    genuine verbatim snippet yet still not back the claim (or back a weaker version
    of it). Fails **closed** — any judge error returns ``"unsupported"`` so a flaky
    model never lets an unverified claim through.

    Args:
        claim: The draft claim under audit.
        evidence: The verbatim evidence snippet from the backing fact.
        llm: LLM client used for the judgement.

    Returns:
        A dict ``{"verdict": "faithful"|"overreach"|"unsupported", "reason": str}``.
    """
    user = f"CLAIM: {claim}\n\nEVIDENCE: {evidence}"
    try:
        payload = llm.complete_json(_FAITHFULNESS_SYSTEM, user)
    except LLMError as exc:
        logger.warning("faithfulness judge failed for claim %r: %s", claim, exc)
        return {"verdict": "unsupported", "reason": f"judge call failed: {exc}"}
    verdict = str(payload.get("verdict", "")).strip().lower()
    if verdict not in {"faithful", "overreach", "unsupported"}:
        verdict = "unsupported"
    return {"verdict": verdict, "reason": str(payload.get("reason", "")).strip()}


def _candidate_facts(claim: str, facts: list[Fact]) -> list[Fact]:
    """Facts whose claim matches ``claim`` (normalized), highest-tier first.

    Unlike a pure backing check this ignores evidence, so a claim backed by a fact
    with *no* evidence still resolves to a candidate — and then fails the substring
    check rather than being reported as unbacked. Within a tier, facts that carry
    evidence sort first.
    """
    key = _normalize(claim)
    matches = [f for f in facts if _normalize(f.claim) == key]
    return sorted(
        matches,
        key=lambda f: (_TIER_RANK.get(f.source_tier, 3), 0 if f.evidence else 1),
    )


def _verify_claim(claim: str, facts: list[Fact], llm: LLMClient, strict: bool) -> tuple[ClaimVerdict, str]:
    """Audit one claim; return its `ClaimVerdict` and a status string.

    Status is ``"verified"`` or one of the failure reasons ``unbacked`` /
    ``volatile-source`` / ``not-substring`` / ``overreach`` / ``unsupported``. The
    faithfulness judge is only called once a claim is backed by a first-party fact
    with evidence — there is nothing to judge otherwise.
    """
    candidates = _candidate_facts(claim, facts)
    if not candidates:
        return ClaimVerdict(claim=claim, substring_ok=False), "unbacked"

    chosen = candidates[0]
    verdict = ClaimVerdict(
        claim=claim,
        fact_used=chosen.claim,
        source_url=chosen.source_url,
        tier=chosen.source_tier,
        substring_ok=bool(chosen.evidence),
    )

    if chosen.source_tier not in _FIRST_PARTY_TIERS:
        return verdict, "volatile-source"
    if not verdict.substring_ok:
        return verdict, "not-substring"

    faithfulness = judge_faithfulness(claim, chosen.evidence, llm)["verdict"]
    verdict.faithfulness = faithfulness  # type: ignore[assignment]
    if faithfulness == "faithful":
        return verdict, "verified"
    if faithfulness == "overreach":
        return verdict, ("overreach" if strict else "verified")
    return verdict, "unsupported"


def run_verification(
    draft: Draft | None,
    research: ResearchResult | None,
    llm: LLMClient,
    settings: Settings,
) -> VerificationResult:
    """Audit a draft's claims and decide whether it passes the groundedness gate.

    Each claim in ``draft.hooks_used`` is audited by `_verify_claim`. The draft
    passes only if there is at least one claim and **every** claim is verified.
    Scores are reported even when the draft passes (see the groundedness
    methodology docs for definitions).

    Args:
        draft: The `Draft` to audit (its ``hooks_used`` are the claims checked).
        research: The research providing the grounding facts.
        llm: LLM client used for the faithfulness judge.
        settings: Settings supplying ``faithfulness_strict``.

    Returns:
        A `VerificationResult` with the per-claim verdicts, the groundedness and
        faithfulness scores, the tier breakdown, the flagged failures, and the
        pass/fail decision.
    """
    facts = list(research.facts) if research else []
    claims = list(draft.hooks_used) if draft else []
    total = len(claims)

    verdicts: list[ClaimVerdict] = []
    flagged: list[str] = []
    tier_breakdown: dict[str, int] = {}
    verified = 0
    faithful = 0
    for claim in claims:
        verdict, status = _verify_claim(claim, facts, llm, settings.faithfulness_strict)
        verdicts.append(verdict)
        tier_breakdown[verdict.tier or "unbacked"] = tier_breakdown.get(verdict.tier or "unbacked", 0) + 1
        if verdict.faithfulness == "faithful":
            faithful += 1
        if status == "verified":
            verified += 1
        else:
            flagged.append(f"{status}: {claim}")

    groundedness_score = round(verified / total, 4) if total else 0.0
    faithfulness_score = round(faithful / total, 4) if total else 0.0
    passed = total > 0 and verified == total

    return VerificationResult(
        groundedness_score=groundedness_score,
        faithfulness_score=faithfulness_score,
        total_claims=total,
        grounded_claims=verified,
        tier_breakdown=tier_breakdown,
        claim_verdicts=verdicts,
        flagged_claims=flagged,
        passed=passed,
    )


def verify_node(
    state: PipelineState,
    *,
    llm: LLMClient | None = None,
    settings: Settings | None = None,
) -> dict:
    """Graph adapter: verify ``state.draft`` against ``state.research``.

    Dependencies default to the configured client/settings but can be injected so
    the pipeline can run on a mocked LLM judge with no network.

    Args:
        state: The pipeline state; reads ``state.draft`` and ``state.research``.
        llm: LLM client for the faithfulness judge; built from settings when omitted.
        settings: Settings; loaded via `get_settings` when omitted.

    Returns:
        A dict ``{"verification": VerificationResult}`` to merge into the state.
    """
    settings = settings or get_settings()
    llm = llm or get_llm_client(settings)
    verification = run_verification(state.draft, state.research, llm, settings)
    return {"verification": verification}
