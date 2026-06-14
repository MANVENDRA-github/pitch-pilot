"""The draft node — write grounded outreach by SELECTING which facts to stand on.

This node writes the outreach email. The hero guarantee is that **every fact the
draft is grounded in is a real, source-backed `Fact`** — and, under **Policy B**,
only a first-party (``own_site`` / ``authoritative``) fact.

Release 0.8.0 decouples *grounding* from *phrasing* (see ADR-0014). The earlier design asked
the model to copy fact text verbatim into ``hooks`` and then validated each hook as
a substring of the source. That made grounding brittle: a perfectly faithful
paraphrase was thrown away because it was not a verbatim copy. The substring check
belongs at **extraction** (where the research node already verifies each fact's
``evidence`` is a literal substring of its source), not at the draft layer.

So the draft layer now works like this:

1. **Tier-gating the claim pool.** Only ``own_site`` / ``authoritative`` facts are
   offered to the model as *claimable*, each on a **numbered** line.
   ``third_party_snippet`` facts are passed in a separate **context-only** section —
   usable for tone or framing, never as a stated claim.
2. **Selection by reference, not by copy.** The model writes the email as free prose
   (it may paraphrase the facts naturally) and returns the **ids** of the claimable
   facts it grounded the email in. ``Draft.hooks_used`` is then those selected facts'
   canonical claim text. Because the ids resolve to real first-party facts, every
   hook is grounded **by construction** — each already passed the extraction-time
   evidence-substring check. No hook text is re-substring-checked, and a paraphrased
   body is never fuzzy-matched back to a fact.

The body's prose is *not* trusted blindly — the verify node independently judges the
body's claims for faithfulness against the selected facts. This node's job is to
produce a grounded draft and an honest list of the facts it stood on.
"""

from __future__ import annotations

import logging
import re

from pitch_pilot.clients.llm import LLMClient, LLMError, get_llm_client, trim_to_token_budget
from pitch_pilot.config import Settings, get_settings
from pitch_pilot.graph.state import PipelineState
from pitch_pilot.models.draft import Draft
from pitch_pilot.models.fact import Fact
from pitch_pilot.models.qualification import QualificationResult
from pitch_pilot.models.research import ResearchResult
from pitch_pilot.nodes.research import _normalize

logger = logging.getLogger(__name__)

_MAX_FACTS_IN_PROMPT = 30
_MAX_FACT_CHARS = 240
# Per-block token budget. The draft prompt renders two blocks (claimable + context),
# so each is bounded so their sum stays well under a small (8192-token) free-tier
# context window even with many/long facts (see ADR-0013).
_FACTS_TOKEN_BUDGET = 1500

# Policy B: only these tiers may carry a stated draft claim.
_CLAIMABLE_TIERS = ("own_site", "authoritative")

# Gate-critical call: draft deterministically for reproducible output (see verify).
_DRAFT_TEMPERATURE = 0.0

# Backstop for fact-id leakage. The numbered ids are a selection mechanism for the
# ``facts_used`` field only; the model is told never to put them in the prose, and
# this strips any that slip through (e.g. ``(Fact 15)`` / ``[fact 2]``).
_FACT_ID_RE = re.compile(r"\s*[\(\[]\s*facts?\s*#?\s*\d+\s*[\)\]]", re.IGNORECASE)


def _strip_fact_ids(text: str) -> str:
    """Remove any leaked fact-id citations like ``(Fact 15)`` from draft prose."""
    cleaned = _FACT_ID_RE.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)        # collapse doubled spaces
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)   # tidy space-before-punctuation
    return cleaned.strip()

_DRAFT_SYSTEM = (
    "You are an SDR writing a short, specific cold outreach email. You are given a "
    "NUMBERED list of CLAIMABLE FACTS about a company and (sometimes) CONTEXT-ONLY "
    "facts, plus a note on why the company qualified. Write a concise, genuine email "
    "(subject + body, 3-5 sentences).\n\n"
    "Rules:\n"
    "- Ground every concrete claim about the company in the CLAIMABLE FACTS. You may "
    "paraphrase them naturally — you need NOT copy them verbatim — but never state "
    "anything about the company the CLAIMABLE FACTS do not support, and never use "
    "outside knowledge.\n"
    "- CONTEXT-ONLY facts may inform tone or framing but must NEVER be stated as a "
    "claim and must NEVER appear in 'facts_used'.\n"
    "- In 'facts_used', return the integer ids (from the numbered CLAIMABLE FACTS) of "
    "every fact your email is grounded in.\n"
    "- The fact id numbers are ONLY for the 'facts_used' field. NEVER write an id in "
    "the subject or body — no '(Fact 3)', 'Fact 3', '[2]', etc. The email must read "
    "as natural prose a prospect would see.\n"
    "- Do not be pushy; no fake urgency; no placeholders like [Name].\n\n"
    'Respond with a JSON object: {"subject": "...", "body": "...", "facts_used": '
    "[<id>, ...]}"
)


def _claimable_facts(facts: list[Fact]) -> list[Fact]:
    """First-party facts (own_site / authoritative) — the only ones a claim may use.

    Sorted own_site first so the model is nudged toward the most trustworthy source.
    """
    claimable = [f for f in facts if f.source_tier in _CLAIMABLE_TIERS]
    tier_rank = {"own_site": 0, "authoritative": 1}
    return sorted(claimable, key=lambda f: tier_rank.get(f.source_tier, 2))


def _context_facts(facts: list[Fact]) -> list[Fact]:
    """Third-party-snippet facts — usable as background context, never as a claim."""
    return [f for f in facts if f.source_tier == "third_party_snippet"]


def _shown_claimable(claimable: list[Fact]) -> list[Fact]:
    """The claimable facts actually shown to the model, in id order (1-based position).

    Capped to ``_MAX_FACTS_IN_PROMPT`` and then to the per-block token budget. The id
    a fact is given in the prompt is its 1-based index in this list, so the same list
    is used both to render the prompt and to resolve the model's selected ids.
    """
    capped = claimable[:_MAX_FACTS_IN_PROMPT]
    lines = [f"[{i}] {fact.claim[:_MAX_FACT_CHARS]}" for i, fact in enumerate(capped, start=1)]
    kept = trim_to_token_budget(lines, _FACTS_TOKEN_BUDGET)
    return capped[: len(kept)]


def _numbered_block(facts: list[Fact]) -> str:
    """Render facts as numbered, id-tagged lines (the ids the model selects by)."""
    return "\n".join(f"[{i}] {fact.claim[:_MAX_FACT_CHARS]}" for i, fact in enumerate(facts, start=1))


def _context_block(facts: list[Fact]) -> str:
    """Render context-only facts as compact tier-tagged lines, bounded to a budget."""
    lines = [f"- [{fact.source_tier}] {fact.claim[:_MAX_FACT_CHARS]}" for fact in facts[:_MAX_FACTS_IN_PROMPT]]
    return "\n".join(trim_to_token_budget(lines, _FACTS_TOKEN_BUDGET))


def _draft_user_prompt(
    company_name: str,
    claimable: list[Fact],
    context: list[Fact],
    qualification: QualificationResult | None,
) -> str:
    """Build the draft prompt: who the company is, why it qualified, and the numbered
    claimable facts it may ground the email in (plus context-only background)."""
    why = qualification.reason if qualification else "(not provided)"
    shown = _shown_claimable(claimable)
    context_block = (
        "\n\nCONTEXT-ONLY facts (background only; NEVER state as a claim or select):\n"
        + _context_block(context)
        if context
        else ""
    )
    return (
        f"COMPANY: {company_name}\n"
        f"WHY IT QUALIFIED: {why}\n\n"
        "CLAIMABLE FACTS (numbered; you may ground the email ONLY in these):\n"
        f"{_numbered_block(shown)}"
        f"{context_block}\n\n"
        "Write the outreach email now, then list in 'facts_used' the ids of the "
        "CLAIMABLE FACTS you grounded it in."
    )


def _as_id(item: object) -> int | None:
    """Coerce a model-returned selection into a 1-based fact id, or ``None``.

    Accepts an int (``3``) or a digit string, optionally bracketed (``"3"``,
    ``"[3]"``). Booleans and anything else return ``None``.
    """
    if isinstance(item, bool):
        return None
    if isinstance(item, int):
        return item
    if isinstance(item, str):
        match = re.fullmatch(r"\s*\[?(\d+)\]?\s*", item)
        if match:
            return int(match.group(1))
    return None


def _selected_hooks(raw: object, shown: list[Fact], claimable: list[Fact]) -> list[str]:
    """Resolve the model's selected fact references to canonical claim text.

    Selection is **by id** (1-based position in the shown claimable list), so each
    hook is a real first-party fact by construction — no substring or fuzzy match.
    As a defensive fallback for models that echo a fact's claim verbatim instead of
    its id, an *exact* (normalized) claim match is also accepted; a paraphrase is
    never matched. The result is de-duplicated, preserving the model's order.
    """
    if not isinstance(raw, list):
        return []
    by_norm_claim = {_normalize(fact.claim): fact for fact in claimable}
    hooks: list[str] = []
    seen: set[str] = set()
    for item in raw:
        fact: Fact | None = None
        idx = _as_id(item)
        if idx is not None and 1 <= idx <= len(shown):
            fact = shown[idx - 1]
        elif isinstance(item, str):
            fact = by_norm_claim.get(_normalize(item))  # verbatim echo, not paraphrase
        if fact is None or fact.claim in seen:
            continue
        seen.add(fact.claim)
        hooks.append(fact.claim)
    return hooks


def run_draft(
    research: ResearchResult | None,
    qualification: QualificationResult | None,
    llm: LLMClient,
    settings: Settings,
) -> Draft:
    """Write a grounded outreach `Draft` from the research facts (Policy B).

    The LLM drafts the email as free prose and selects, *by id*, which claimable
    facts it grounded the email in. Only first-party (``own_site`` /
    ``authoritative``) facts are offered as claimable; third-party-snippet facts are
    passed as context only. ``hooks_used`` is the canonical claim text of the
    selected facts, so it is always a subset of the first-party research facts and is
    grounded by construction — the draft layer does **not** substring- or
    fuzzy-match hook text against the source (see the module docstring and ADR-0014).

    Args:
        research: The research whose ``facts`` ground the draft.
        qualification: The qualification verdict, used only to give the draft
            context on why the company is a fit.
        llm: LLM client used to write the email.
        settings: Run settings (reserved for future drafting knobs).

    Returns:
        A `Draft` whose ``hooks_used`` are guaranteed to be real, first-party facts.
        On no claimable facts or an LLM failure, returns an empty draft (which the
        verify node will then flag).
    """
    facts = list(research.facts) if research else []
    claimable = _claimable_facts(facts)
    if not claimable:
        logger.info("draft: no first-party (claimable) facts; returning empty draft.")
        return Draft(subject="", body="", hooks_used=[])

    context = _context_facts(facts)
    company_name = research.company.name or research.company.domain if research else "the company"
    try:
        payload = llm.complete_json(
            _DRAFT_SYSTEM,
            _draft_user_prompt(company_name, claimable, context, qualification),
            temperature=_DRAFT_TEMPERATURE,
        )
    except LLMError as exc:
        logger.warning("draft LLM call failed: %s", exc)
        return Draft(subject="", body="", hooks_used=[])

    # Strip any leaked fact-id tokens the prompt told the model to keep out (backstop).
    subject = _strip_fact_ids(str(payload.get("subject", "")).strip())
    body = _strip_fact_ids(str(payload.get("body", "")).strip())
    hooks_used = _selected_hooks(payload.get("facts_used"), _shown_claimable(claimable), claimable)

    return Draft(subject=subject, body=body, hooks_used=hooks_used)


def draft_node(
    state: PipelineState,
    *,
    llm: LLMClient | None = None,
    settings: Settings | None = None,
) -> dict:
    """Graph adapter: write a draft from ``state.research`` and ``state.qualification``.

    Dependencies default to the configured client/settings but can be injected so
    the pipeline can run on mocked clients with no network.

    Args:
        state: The pipeline state; reads ``state.research`` and ``state.qualification``.
        llm: LLM client; built from settings when omitted.
        settings: Settings; loaded via `get_settings` when omitted.

    Returns:
        A dict ``{"draft": Draft, "status": "drafted"}`` to merge into the state.
    """
    settings = settings or get_settings()
    llm = llm or get_llm_client(settings)
    draft = run_draft(state.research, state.qualification, llm, settings)
    return {"draft": draft, "status": "drafted"}
