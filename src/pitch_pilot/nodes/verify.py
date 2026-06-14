"""The verify node — the real groundedness gate over a draft (0.8.0).

This is the enforcement point for the hero guarantee, applied to the finished
draft. Release 0.8.0 splits the check into a **structural** part (cheap, by construction) and
a **faithfulness** part (an LLM judge over the body), reflecting that source-text
grounding now lives at *extraction*, not here (see ADR-0014):

1. **Structural — grounding facts.** Every ``hook_used`` must resolve to a
   first-party (``own_site`` / ``authoritative``) `Fact` in the research. This is
   true *by construction* (the draft node only selects such facts by id); the gate
   re-resolves and asserts it, recording any hook that fails to resolve as a
   ``structural`` failure (it must never happen).
2. **Faithfulness — the body.** A single LLM judge reads the draft **body** and the
   set of selected facts, extracts every factual claim the body makes *about the
   company*, and rates each ``faithful`` / ``overreach`` / ``unsupported`` against
   those facts. This judges *support*, which a substring check cannot: a fact can be
   genuinely sourced yet not back a particular paraphrase in the body.

The draft **passes** only if: there is at least one grounded hook, the body is
non-empty, there are no structural failures, the judge call succeeded, and **no body
claim is ``unsupported``** (and none is ``overreach`` when ``FAITHFULNESS_STRICT``).
Each failing claim is recorded with its reason (``structural`` / ``overreach`` /
``unsupported`` / ``judge-error``), and a full per-claim audit trail is returned in
`VerificationResult.claim_verdicts`.

The node is **network-free except for the single LLM judge call** — it does not
re-fetch sources. Independent live re-verification of each source is an *eval-time*
metric (P4), reported separately by tier (see ADR-0010 in the decisions log).
"""

from __future__ import annotations

import logging
import re
from collections import Counter

from pitch_pilot.clients.llm import LLMClient, LLMError, get_llm_client
from pitch_pilot.config import Settings, get_settings
from pitch_pilot.graph.state import PipelineState
from pitch_pilot.models.draft import Draft
from pitch_pilot.models.fact import Fact
from pitch_pilot.models.research import ResearchResult
from pitch_pilot.models.verification import ClaimVerdict, VerificationResult
from pitch_pilot.nodes.research import _normalize

logger = logging.getLogger(__name__)

# Tiers a stated claim may rest on (Policy B). A hook that resolves to anything else
# is a structural violation, not merely a soft flag.
_FIRST_PARTY_TIERS = {"own_site", "authoritative"}
_TIER_RANK = {"own_site": 0, "authoritative": 1}

# Gate-critical call: judge deterministically so a draft's verdict is reproducible.
_JUDGE_TEMPERATURE = 0.0

_FAITHFULNESS_SYSTEM = (
    "You are a strict groundedness judge for an SDR agent. You are given the BODY of "
    "a cold outreach email and a NUMBERED list of FACTS (each copied from a source, "
    "with its verbatim evidence). Identify every factual assertion the BODY makes "
    "ABOUT THE COMPANY (the recipient). Ignore greetings, questions, the sender's own "
    "product or pitch, and generic pleasantries. For each company claim decide ONLY "
    "from the FACTS whether it is supported — never use outside knowledge.\n\n"
    "Verdicts:\n"
    "- 'faithful': a FACT directly supports the claim as stated.\n"
    "- 'overreach': a FACT partially supports it, but the claim generalizes, "
    "exaggerates, or adds beyond what the FACT actually says.\n"
    "- 'unsupported': no FACT supports the claim.\n\n"
    "For each claim also return 'fact_id': the id of the supporting FACT (or null when "
    "unsupported).\n\n"
    'Respond with a JSON object: {"claims": [{"claim": "...", "verdict": '
    '"faithful"|"overreach"|"unsupported", "fact_id": <id|null>, "reason": '
    '"<one short sentence>"}]}'
)


def _as_id(item: object) -> int | None:
    """Coerce a model-returned fact reference into a 1-based id, or ``None``."""
    if isinstance(item, bool):
        return None
    if isinstance(item, int):
        return item
    if isinstance(item, str):
        match = re.fullmatch(r"\s*\[?(\d+)\]?\s*", item)
        if match:
            return int(match.group(1))
    return None


def judge_body(body: str, selected: list[Fact], llm: LLMClient) -> tuple[bool, list[dict]]:
    """Judge the draft ``body``'s claims for faithfulness against the selected facts.

    One LLM call: the judge extracts each factual claim the body makes about the
    company and rates it against the selected facts, naming the supporting fact by id.
    Fails **closed** — any judge error or malformed response returns ``ok=False`` so a
    flaky model never lets an unjudged body pass the gate.

    Args:
        body: The draft body to audit.
        selected: The first-party facts the draft is grounded in (ids are 1-based).
        llm: LLM client used for the judgement.

    Returns:
        ``(ok, claims)`` where ``ok`` is whether the judge call succeeded and
        ``claims`` is a list of ``{"claim", "verdict", "fact", "reason"}`` dicts
        (``fact`` is the resolved `Fact` or ``None``).
    """
    facts_block = "\n".join(
        f"[{i}] {fact.claim}" + (f" — evidence: {fact.evidence}" if fact.evidence else "")
        for i, fact in enumerate(selected, start=1)
    )
    user = f"BODY:\n{body}\n\nFACTS:\n{facts_block}"
    try:
        payload = llm.complete_json(_FAITHFULNESS_SYSTEM, user, temperature=_JUDGE_TEMPERATURE)
    except LLMError as exc:
        logger.warning("body faithfulness judge failed: %s", exc)
        return False, []

    raw = payload.get("claims")
    if not isinstance(raw, list):
        logger.warning("body faithfulness judge returned no 'claims' list: %r", payload)
        return False, []

    claims: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim", "")).strip()
        if not claim:
            continue
        verdict = str(item.get("verdict", "")).strip().lower()
        if verdict not in {"faithful", "overreach", "unsupported"}:
            verdict = "unsupported"
        fact: Fact | None = None
        fid = _as_id(item.get("fact_id"))
        if verdict != "unsupported" and fid is not None and 1 <= fid <= len(selected):
            fact = selected[fid - 1]
        claims.append({"claim": claim, "verdict": verdict, "fact": fact,
                       "reason": str(item.get("reason", "")).strip()})
    return True, claims


def _first_party_by_claim(facts: list[Fact]) -> dict[str, Fact]:
    """Map each first-party fact's normalized claim to its best-tier fact.

    Within a claim, the highest tier wins (own_site over authoritative) and, at the
    same tier, a fact carrying evidence is preferred.
    """
    best: dict[str, Fact] = {}
    for fact in facts:
        if fact.source_tier not in _FIRST_PARTY_TIERS:
            continue
        key = _normalize(fact.claim)
        current = best.get(key)
        rank = (_TIER_RANK[fact.source_tier], 0 if fact.evidence else 1)
        if current is None or rank < (_TIER_RANK[current.source_tier], 0 if current.evidence else 1):
            best[key] = fact
    return best


def _resolve_hooks(hooks: list[str], facts: list[Fact]) -> tuple[list[Fact], list[str]]:
    """Resolve each hook to a first-party `Fact`; report any that fail to resolve.

    Hooks are first-party grounded facts by construction (the draft node only selects
    such facts). This re-resolves them and records any hook that does not map to a
    first-party fact as a ``structural`` failure — the invariant should hold, so a
    failure here means an upstream bug, not a model mistake.
    """
    by_claim = _first_party_by_claim(facts)
    selected: list[Fact] = []
    failures: list[str] = []
    seen: set[str] = set()
    for hook in hooks:
        fact = by_claim.get(_normalize(hook))
        if fact is None:
            failures.append(hook)
            continue
        if fact.claim in seen:
            continue
        seen.add(fact.claim)
        selected.append(fact)
    return selected, failures


def run_verification(
    draft: Draft | None,
    research: ResearchResult | None,
    llm: LLMClient,
    settings: Settings,
) -> VerificationResult:
    """Audit a draft and decide whether it passes the groundedness gate.

    Resolves the draft's hooks to first-party facts (structural), then runs a single
    faithfulness judge over the body against those facts. The draft passes only if it
    has a grounded hook, a non-empty body, no structural failure, a successful judge
    call, and no ``unsupported`` body claim (and no ``overreach`` when
    ``faithfulness_strict``). See the groundedness methodology docs for the metric
    definitions.

    Args:
        draft: The `Draft` to audit (``hooks_used`` are the grounding facts; ``body``
            is what the judge evaluates).
        research: The research providing the grounding facts.
        llm: LLM client used for the faithfulness judge.
        settings: Settings supplying ``faithfulness_strict``.

    Returns:
        A `VerificationResult` with the per-body-claim verdicts, the groundedness and
        faithfulness scores, the tier breakdown of the grounding facts, the flagged
        failures, and the pass/fail decision.
    """
    facts = list(research.facts) if research else []
    hooks = list(draft.hooks_used) if draft else []
    body = (draft.body if draft else "") or ""
    strict = settings.faithfulness_strict

    selected, structural_failures = _resolve_hooks(hooks, facts)
    tier_breakdown = dict(Counter(fact.source_tier for fact in selected))

    ok, judged = (True, [])
    if selected and body.strip():
        ok, judged = judge_body(body, selected, llm)

    verdicts: list[ClaimVerdict] = []
    flagged: list[str] = [f"structural: {hook}" for hook in structural_failures]
    faithful = 0
    verified = 0
    for jc in judged:
        verdict = jc["verdict"]
        fact: Fact | None = jc["fact"]
        verdicts.append(ClaimVerdict(
            claim=jc["claim"],
            fact_used=fact.claim if fact else None,
            source_url=fact.source_url if fact else None,
            tier=fact.source_tier if fact else None,
            substring_ok=bool(fact and fact.evidence),
            faithfulness=verdict,  # type: ignore[arg-type]
        ))
        if verdict == "faithful":
            faithful += 1
            verified += 1
        elif verdict == "overreach":
            if strict:
                flagged.append(f"overreach: {jc['claim']}")
            else:
                verified += 1
        else:
            flagged.append(f"unsupported: {jc['claim']}")

    total = len(judged)
    judge_failed = bool(selected) and bool(body.strip()) and not ok
    if judge_failed:
        flagged.append("judge-error: faithfulness judge failed")

    if not selected or not body.strip() or judge_failed:
        groundedness_score = 0.0
        faithfulness_score = 0.0
    elif total == 0:
        # A grounded draft whose body makes no checkable company claim: nothing is
        # unfaithful, so it is vacuously grounded.
        groundedness_score = 1.0
        faithfulness_score = 1.0
    else:
        groundedness_score = round(verified / total, 4)
        faithfulness_score = round(faithful / total, 4)

    passed = (
        bool(selected)
        and bool(body.strip())
        and not structural_failures
        and not judge_failed
        and all(v.faithfulness != "unsupported" for v in verdicts)
        and (not strict or all(v.faithfulness != "overreach" for v in verdicts))
    )

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
