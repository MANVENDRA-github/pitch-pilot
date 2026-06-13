"""The qualify node — score a researched company against the ICP.

This node decides whether a company is worth drafting outreach to. It is a
deliberate **hybrid**: the LLM does the part it is good at (fuzzy, semantic
matching of ICP signals against the grounded facts, citing the fact that supports
each match), and **deterministic Python** does the part that must be auditable and
repeatable (turning that assessment into a score, applying a hard veto on negative
signals, and making the qualified / not-qualified call).

Two principles shape it:

* **Unknowns are never guessed.** When the facts do not establish a signal, the
  assessment marks it ``"unknown"`` rather than assuming it is present or absent.
  Unknown *structural* attributes (industry, size, region) are dropped from the
  score and the remaining weights are renormalized, so a company is not punished
  for a research gap.
* **A matched negative signal is a hard veto.** No fit score, however high, can
  qualify a company that matches an ICP negative signal (a competitor, a
  non-profit, …).

The scoring weights and the veto live here in code (not in the prompt), so the
verdict is reproducible and explainable from the same assessment every time.
"""

from __future__ import annotations

import logging

from pitch_pilot.clients.llm import LLMClient, LLMError, get_llm_client, trim_to_token_budget
from pitch_pilot.config import Settings, get_settings
from pitch_pilot.graph.state import PipelineState
from pitch_pilot.models.fact import Fact
from pitch_pilot.models.icp import ICP
from pitch_pilot.models.qualification import QualificationResult
from pitch_pilot.models.research import ResearchResult
from pitch_pilot.nodes.research import _normalize

logger = logging.getLogger(__name__)

# Component weights for the deterministic fit score. Structural components that
# the facts leave unknown are dropped and the remaining weights renormalized.
_WEIGHTS = {"industry": 0.35, "size": 0.25, "region": 0.15, "positives": 0.25}

# How many facts to show the assessor, how long each line may be, and a token
# budget for the whole facts block so the prompt fits a small (8192-token)
# free-tier context window even with many/long facts (see ADR-0013).
_MAX_FACTS_IN_PROMPT = 40
_MAX_FACT_CHARS = 240
_FACTS_TOKEN_BUDGET = 3000

_QUALIFY_SYSTEM = (
    "You are the qualification assessor for an SDR agent. You are given an Ideal "
    "Customer Profile (ICP) and a list of GROUNDED FACTS about a company (each fact "
    "is backed by a real source). Your ONLY job is to judge, for each ICP attribute "
    "and signal, whether the FACTS support it. You do NOT decide if the company "
    "qualifies — downstream code does that.\n\n"
    "Strict rules:\n"
    "- Judge ONLY from the provided FACTS. Never use outside knowledge.\n"
    "- If the FACTS do not establish something, its status is 'unknown'. Do not "
    "guess 'match' or 'no_match'.\n"
    "- For every match, cite the supporting fact text in 'evidence_fact' (copied "
    "from the FACTS); otherwise use an empty string.\n"
    "- For employee_count, return an integer 'value' only if a fact states it, "
    "else null.\n"
    "- Echo each ICP signal verbatim in its 'signal' field.\n\n"
    'Respond with a JSON object of exactly this shape: {"industry": {"status": '
    '"match|no_match|unknown", "evidence_fact": "..."}, "region": {"status": '
    '"match|no_match|unknown", "evidence_fact": "..."}, "employee_count": '
    '{"value": <int or null>, "evidence_fact": "..."}, "positive_signals": '
    '[{"signal": "...", "status": "match|no_match|unknown", "evidence_fact": "..."}], '
    '"negative_signals": [{"signal": "...", "status": "match|no_match|unknown", '
    '"evidence_fact": "..."}]}'
)


def _facts_block(facts: list[Fact]) -> str:
    """Render facts as compact, source-tagged lines, bounded to a token budget."""
    lines = [
        f"- [{fact.source_tier}] {fact.claim[:_MAX_FACT_CHARS]} (source: {fact.source_url})"
        for fact in facts[:_MAX_FACTS_IN_PROMPT]
    ]
    return "\n".join(trim_to_token_budget(lines, _FACTS_TOKEN_BUDGET))


def _qualify_user_prompt(icp: ICP, facts: list[Fact]) -> str:
    """Build the assessor's user prompt: the ICP to match and the facts to match it against."""
    return (
        "ICP:\n"
        f"- industries: {icp.industries}\n"
        f"- employee band: {icp.min_employees}-{icp.max_employees}\n"
        f"- regions: {icp.regions}\n"
        f"- positive_signals: {icp.positive_signals}\n"
        f"- negative_signals: {icp.negative_signals}\n\n"
        "GROUNDED FACTS:\n"
        f"{_facts_block(facts)}\n\n"
        "Assess each ICP attribute and signal against the FACTS."
    )


def _assess(icp: ICP, facts: list[Fact], llm: LLMClient) -> dict:
    """Ask the LLM to assess the ICP against the facts; return ``{}`` on failure."""
    try:
        return llm.complete_json(_QUALIFY_SYSTEM, _qualify_user_prompt(icp, facts))
    except LLMError as exc:
        logger.warning("qualification assessor LLM call failed: %s", exc)
        return {}


def _status_of(entry: object) -> str:
    """Normalize a ``{"status": ...}`` entry to one of match/no_match/unknown."""
    status = str((entry or {}).get("status", "unknown")).strip().lower() if isinstance(entry, dict) else "unknown"
    return status if status in {"match", "no_match", "unknown"} else "unknown"


def _index_by_signal(items: object) -> dict[str, str]:
    """Map normalized signal text → status from an assessment signal list."""
    out: dict[str, str] = {}
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("signal") is not None:
                out[_normalize(str(item["signal"]))] = _status_of(item)
    return out


def _employee_value(entry: object) -> int | None:
    """Pull an integer employee count from the assessment, or ``None`` if unknown."""
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _size_status(value: int | None, icp: ICP) -> str:
    """Classify the employee count against the ICP band."""
    if value is None:
        return "unknown"
    return "match" if icp.min_employees <= value <= icp.max_employees else "no_match"


def _component_score(status: str) -> float | None:
    """A known component scores 1.0 (match) or 0.0 (no_match); unknown returns ``None``."""
    if status == "match":
        return 1.0
    if status == "no_match":
        return 0.0
    return None


def run_qualification(
    research: ResearchResult | None,
    icp: ICP,
    llm: LLMClient,
    settings: Settings,
) -> QualificationResult:
    """Qualify a company against an `ICP` from its grounded research.

    The LLM produces a per-attribute, per-signal assessment of the facts (see the
    module docstring); this function turns that assessment into a deterministic
    fit score, applies the negative-signal veto, and decides qualification against
    ``settings.qualify_threshold``.

    Args:
        research: The research result whose ``facts`` are assessed. If there are
            no facts, the company is not qualified (nothing to judge on).
        icp: The Ideal Customer Profile to score against.
        llm: LLM client used only for the semantic assessment.
        settings: Settings supplying ``qualify_threshold``.

    Returns:
        A `QualificationResult` with the boolean verdict, the ``[0, 1]`` fit
        score, a human-readable reason, and the matched / missed signals. A
        matched negative signal forces ``qualified=False`` regardless of score.
    """
    facts = list(research.facts) if research else []
    if not facts:
        return QualificationResult(
            qualified=False,
            score=0.0,
            reason="No grounded facts available to qualify against the ICP.",
        )

    assessment = _assess(icp, facts, llm)

    industry = _status_of(assessment.get("industry"))
    region = _status_of(assessment.get("region"))
    employees = _employee_value(assessment.get("employee_count"))
    size = _size_status(employees, icp)
    positives = _index_by_signal(assessment.get("positive_signals"))
    negatives = _index_by_signal(assessment.get("negative_signals"))

    # --- Positive signals: matched / total (unknowns and no-matches earn nothing) ---
    matched_signals: list[str] = []
    missed_signals: list[str] = []
    for signal in icp.positive_signals:
        status = positives.get(_normalize(signal), "unknown")
        if status == "match":
            matched_signals.append(signal)
        elif status == "no_match":
            missed_signals.append(signal)
    positives_component = (
        len(matched_signals) / len(icp.positive_signals) if icp.positive_signals else None
    )

    # --- Weighted, renormalized fit score over the components we actually know ---
    components: dict[str, float | None] = {
        "industry": _component_score(industry),
        "size": _component_score(size),
        "region": _component_score(region),
        "positives": positives_component,
    }
    weighted = sum(_WEIGHTS[k] * v for k, v in components.items() if v is not None)
    weight_sum = sum(_WEIGHTS[k] for k, v in components.items() if v is not None)
    score = round(weighted / weight_sum, 4) if weight_sum else 0.0

    # --- Hard veto: any matched negative signal disqualifies, full stop ---
    vetoed = [signal for signal in icp.negative_signals if negatives.get(_normalize(signal)) == "match"]
    for signal in vetoed:
        missed_signals.append(f"(negative) {signal}")

    threshold = settings.qualify_threshold
    qualified = (not vetoed) and score >= threshold

    structural = f"industry={industry}, size={size}, region={region}"
    if vetoed:
        reason = (
            f"Disqualified by negative signal(s): {', '.join(vetoed)} "
            f"(fit score {score:.2f}; {structural})."
        )
    elif qualified:
        reason = (
            f"Fit score {score:.2f} >= threshold {threshold:.2f}; {structural}; "
            f"matched {len(matched_signals)}/{len(icp.positive_signals)} positive signal(s)."
        )
    else:
        reason = (
            f"Fit score {score:.2f} < threshold {threshold:.2f}; {structural}; "
            f"matched {len(matched_signals)}/{len(icp.positive_signals)} positive signal(s)."
        )

    return QualificationResult(
        qualified=qualified,
        score=score,
        reason=reason,
        matched_signals=matched_signals,
        missed_signals=missed_signals,
    )


def qualify_node(
    state: PipelineState,
    *,
    llm: LLMClient | None = None,
    settings: Settings | None = None,
) -> dict:
    """Graph adapter: qualify ``state.research`` against ``state.icp``.

    Dependencies default to the configured client/settings but can be injected so
    the pipeline can run on mocked clients with no network.

    Args:
        state: The pipeline state; reads ``state.research`` and ``state.icp``.
        llm: LLM client; built from settings when omitted.
        settings: Settings; loaded via `get_settings` when omitted.

    Returns:
        A dict with the ``qualification`` result and an updated ``status``
        (``"qualified"`` or ``"disqualified"``) to merge into the state.
    """
    settings = settings or get_settings()
    llm = llm or get_llm_client(settings)
    qualification = run_qualification(state.research, state.icp, llm, settings)
    return {
        "qualification": qualification,
        "status": "qualified" if qualification.qualified else "disqualified",
    }
