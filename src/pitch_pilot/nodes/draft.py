"""The draft node — write grounded outreach from the research facts.

This node writes the outreach email. The hard rule, straight from the project's
hero guarantee, is that **every hook the draft leans on must be a real `Fact`** —
the model may phrase things, but it may not introduce a claim that the research
did not establish. P3 sharpens that rule with **Policy B (first-party-only for
claims)**: a stated draft claim may rest only on an ``own_site`` or
``authoritative`` fact.

Two enforcement layers make that true regardless of what the LLM returns:

1. **Tier-gating the claim pool.** Only ``own_site`` / ``authoritative`` facts are
   offered to the model as *claimable*. ``third_party_snippet`` facts are passed in
   a separate **context-only** section — the model may use them for tone or framing,
   but they can never become a hook. This is the direct lesson of the P1
   validation: search-snippet sources were the ones that failed live
   re-verification, so they must never carry a stated claim.
2. **Validating the outputs.** Whatever ``hooks`` the model claims it used are
   matched back against the *claimable* (first-party) facts; anything that does not
   map to one is discarded. So ``Draft.hooks_used`` is always a subset of the
   first-party research facts, by construction.

The body's prose is *not* trusted blindly — the verify node independently audits
the draft's claims against sources. This node's job is to produce a grounded
draft and an honest list of the facts it stood on.
"""

from __future__ import annotations

import logging

from pitch_pilot.clients.llm import LLMClient, LLMError, get_llm_client
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

# Policy B: only these tiers may carry a stated draft claim.
_CLAIMABLE_TIERS = ("own_site", "authoritative")

_DRAFT_SYSTEM = (
    "You are an SDR writing a short, specific cold outreach email. You are given "
    "CLAIMABLE FACTS about a company and (sometimes) CONTEXT-ONLY facts, plus a note "
    "on why the company qualified. Write a concise, genuine email (subject + body, "
    "3-5 sentences).\n\n"
    "Strict rules:\n"
    "- Every concrete claim about the company MUST come from the CLAIMABLE FACTS. "
    "Never invent or add information from outside knowledge.\n"
    "- CONTEXT-ONLY facts may inform tone or framing but must NEVER be stated as a "
    "claim and must NEVER appear in 'hooks'.\n"
    "- In 'hooks', list the exact claim text of every CLAIMABLE FACT you referenced, "
    "copied verbatim.\n"
    "- Do not be pushy; no fake urgency; no placeholders like [Name].\n\n"
    'Respond with a JSON object: {"subject": "...", "body": "...", "hooks": '
    '["<exact claimable fact>", ...]}'
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


def _facts_block(facts: list[Fact]) -> str:
    """Render facts as compact, tier-tagged lines for the draft prompt."""
    return "\n".join(
        f"- [{fact.source_tier}] {fact.claim[:_MAX_FACT_CHARS]}"
        for fact in facts[:_MAX_FACTS_IN_PROMPT]
    )


def _draft_user_prompt(
    company_name: str,
    claimable: list[Fact],
    context: list[Fact],
    qualification: QualificationResult | None,
) -> str:
    """Build the draft prompt: who the company is, why it qualified, and which facts it may use."""
    why = qualification.reason if qualification else "(not provided)"
    context_block = (
        "\n\nCONTEXT-ONLY facts (background; NEVER state as a claim or use as a hook):\n"
        + _facts_block(context)
        if context
        else ""
    )
    return (
        f"COMPANY: {company_name}\n"
        f"WHY IT QUALIFIED: {why}\n\n"
        "CLAIMABLE FACTS (the only facts you may state and put in hooks):\n"
        f"{_facts_block(claimable)}"
        f"{context_block}\n\n"
        "Write the outreach email now."
    )


def run_draft(
    research: ResearchResult | None,
    qualification: QualificationResult | None,
    llm: LLMClient,
    settings: Settings,
) -> Draft:
    """Write a grounded outreach `Draft` from the research facts (Policy B).

    The LLM drafts the email; this function constrains and audits it: only
    first-party (``own_site`` / ``authoritative``) facts are offered as claimable,
    third-party-snippet facts are passed as context only, and every hook the model
    returns is matched back against the claimable facts so ``hooks_used`` is always
    a subset of the first-party research facts (see the module docstring).

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
            _DRAFT_SYSTEM, _draft_user_prompt(company_name, claimable, context, qualification)
        )
    except LLMError as exc:
        logger.warning("draft LLM call failed: %s", exc)
        return Draft(subject="", body="", hooks_used=[])

    subject = str(payload.get("subject", "")).strip()
    body = str(payload.get("body", "")).strip()

    # Validate hooks: keep only those that map to a real CLAIMABLE fact (verbatim,
    # whitespace/case-insensitive), de-duplicated, using the canonical fact text.
    claimable_by_norm = {_normalize(fact.claim): fact.claim for fact in claimable}
    hooks_used: list[str] = []
    seen: set[str] = set()
    for hook in payload.get("hooks", []) if isinstance(payload.get("hooks"), list) else []:
        key = _normalize(str(hook))
        if key in claimable_by_norm and key not in seen:
            seen.add(key)
            hooks_used.append(claimable_by_norm[key])

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
