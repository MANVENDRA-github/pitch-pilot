"""The agentic research node — gather grounded facts about a company.

This module is the heart of pitch-pilot's research phase. Given a `Company`
(really just a domain), it produces a `ResearchResult` full of
source-tagged `Fact`s covering the dimensions an SDR cares about: company
**overview**, recent **news**, **hiring** signals, and **tech** signals.

What makes it *agentic* is the control flow: the next search query is **chosen by
the LLM**, not read from a fixed list. The loop is:

    seed-fetch → extract → plan (LLM picks the next query, or stops) → search →
    extract → plan → … until the planner is satisfied or the query budget runs out.

Groundedness is enforced *by construction* at two points:

1. A `Fact` cannot exist without an ``http(s)`` ``source_url`` (the model's own
   validator), so every fact is born with a citation.
2. The **extractor** additionally requires a verbatim ``evidence`` snippet and
   drops any candidate fact whose evidence is not actually found in the source
   text — a cheap anti-hallucination check that stops the model from smuggling in
   claims from its own prior knowledge.

The node never crashes on a bad page or an empty search: failures are recorded on
`ResearchResult.errors` and the loop moves on. It is fully synchronous, matching
the rest of the P0/P1 codebase.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from pydantic import ValidationError

from pitch_pilot.clients.fetch import fetch_page
from pitch_pilot.clients.llm import LLMClient, LLMError, get_llm_client
from pitch_pilot.clients.search import SearchClient, get_search_client
from pitch_pilot.config import Settings, get_settings
from pitch_pilot.graph.state import PipelineState
from pitch_pilot.models.fact import Fact, SourceTier
from pitch_pilot.models.lead import Company
from pitch_pilot.models.research import ResearchResult

logger = logging.getLogger(__name__)

# Hosts treated as `authoritative` primary sources when tiering a fact's source.
# Deliberately conservative — official filings/registries only. The P1 validation
# showed commercial aggregators (Crunchbase, Tracxn, Wellfound, …) are routinely
# bot-blocked or stale, so they stay in the default `third_party_snippet` tier.
# Extending this set is a deliberate, reviewable act (see ADR-0008).
_AUTHORITATIVE_HOSTS: frozenset[str] = frozenset({"sec.gov"})

# The dimensions an SDR cares about. These are both the planner's coverage
# targets and the only categories the extractor is allowed to assign.
RESEARCH_DIMENSIONS: tuple[str, ...] = ("overview", "news", "hiring", "tech")
_VALID_CATEGORIES = set(RESEARCH_DIMENSIONS)

# Hard caps that bound a single research run. The page-char and per-source caps are
# the lean *fallback* defaults used when `extract_facts` is called without explicit
# limits; the shipping defaults live in `Settings` (`research_max_page_chars`,
# `research_max_facts_per_source`) and `run_research` passes those through. See
# ADR-0012 for why research depth is leaned out by default.
MAX_FACTS_PER_SOURCE = 5       # fallback per-source fact cap (Settings overrides)
MAX_EVIDENCE_CHARS = 200       # evidence snippet length cap (mirrors Fact.evidence)
SEARCH_RESULTS_PER_QUERY = 4   # how many hits to extract from per search query
MAX_TEXT_CHARS = 3_500         # fallback page-text cap fed to the extractor (Settings overrides)

_WHITESPACE = re.compile(r"\s+")

_EXTRACTOR_SYSTEM = (
    "You are a fact-extraction engine for an SDR research agent. You are given the "
    "visible TEXT of a single web page or search result and must extract only the "
    "claims that the TEXT itself explicitly supports.\n\n"
    "Rules:\n"
    "- Extract ONLY claims directly and explicitly supported by the provided TEXT.\n"
    "- NEVER use outside or prior knowledge. If you are not certain the TEXT states "
    "it, do not include it.\n"
    "- For each claim, copy a short 'evidence' snippet (at most 200 characters) "
    "VERBATIM from the TEXT. It must be a literal substring of the TEXT, not a "
    "paraphrase or a summary.\n"
    "- 'category' must be exactly one of: overview, news, hiring, tech.\n"
    "- 'confidence' is your 0.0-1.0 confidence that the TEXT supports the claim.\n"
    "- If the TEXT contains no usable company facts, return an empty list.\n\n"
    'Respond with a JSON object of the form: {"facts": [{"claim": "...", '
    '"evidence": "...", "category": "overview", "confidence": 0.0}]}'
)

_PLANNER_SYSTEM = (
    "You are the planner for an autonomous SDR research agent. Your job is to decide "
    "the single NEXT web-search query that best fills gaps in what we know about a "
    "company, or to declare research complete.\n\n"
    "We care about four dimensions: overview (what the company does), news (recent "
    "events, funding, launches), hiring (open roles, team growth), and tech (the "
    "technologies and stack they use).\n\n"
    "You are given the company, a summary of the facts gathered so far grouped by "
    "dimension (with which dimensions are still thin), and the list of queries "
    "already run.\n\n"
    "Choose exactly one of:\n"
    "- Propose the most useful next search query (a real web-search string) that "
    "targets a thin or missing dimension. Never repeat a query already run.\n"
    "- Declare research done when coverage is reasonable across the dimensions or "
    "further searching is unlikely to help.\n\n"
    'Respond with a JSON object: {"done": <bool>, "reason": "<short string>", '
    '"next_query": "<query string, or null when done>"}'
)


def _normalize(text: str) -> str:
    """Lower-case and collapse whitespace for a forgiving substring comparison."""
    return _WHITESPACE.sub(" ", (text or "")).strip().lower()


def _seed_url(domain: str) -> str:
    """Turn a bare domain into an ``http(s)`` URL suitable for a `Fact.source_url`.

    Args:
        domain: A domain (``"acme.com"``) or an already-qualified URL.

    Returns:
        The domain unchanged if it already starts with ``http://``/``https://``,
        otherwise the domain prefixed with ``https://``.
    """
    candidate = (domain or "").strip()
    if candidate.lower().startswith(("http://", "https://")):
        return candidate
    return f"https://{candidate}"


def _host(url: str) -> str:
    """Return the lower-cased host of a URL or bare domain, without ``www.`` or port."""
    candidate = (url or "").strip()
    if not candidate.lower().startswith(("http://", "https://")):
        candidate = f"https://{candidate}"
    host = (urlparse(candidate).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _is_same_or_subdomain(host: str, domain: str) -> bool:
    """True if ``host`` is ``domain`` itself or a subdomain of it (``blog.x`` of ``x``)."""
    return bool(host) and bool(domain) and (host == domain or host.endswith(f".{domain}"))


def classify_source_tier(url: str, company_domain: str | None) -> SourceTier:
    """Classify how trustworthy a fact's source is, from its URL.

    The tiering is purely structural (host-based), so it is deterministic and
    cheap — no model call. See `SourceTier` for what each tier means and ADR-0008
    for the rationale.

    Args:
        url: The fact's ``source_url``.
        company_domain: The company's own domain (``"acme.com"`` or a URL). Pages
            on this domain — including sub-pages and subdomains found via search —
            are the company speaking about itself and tier as ``"own_site"``.

    Returns:
        ``"own_site"`` for the company's own domain, ``"authoritative"`` for a
        recognized primary source (`_AUTHORITATIVE_HOSTS`), otherwise
        ``"third_party_snippet"`` (the default).
    """
    host = _host(url)
    domain = _host(company_domain) if company_domain else ""
    if _is_same_or_subdomain(host, domain):
        return "own_site"
    if host in _AUTHORITATIVE_HOSTS or any(
        host.endswith(f".{auth}") for auth in _AUTHORITATIVE_HOSTS
    ):
        return "authoritative"
    return "third_party_snippet"


def _coerce_confidence(value: object) -> float:
    """Coerce a model-supplied confidence to a float clamped to ``[0, 1]``.

    Falls back to ``0.5`` when the value is missing or not numeric.
    """
    try:
        confidence = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    return min(1.0, max(0.0, confidence))


def _facts_payload(payload: dict) -> list:
    """Pull the list of candidate fact items out of an extractor JSON payload.

    Prefers a ``"facts"`` key, but tolerates a model that wrapped the list under a
    different key by falling back to the first list-valued entry.
    """
    items = payload.get("facts")
    if items is None:
        for value in payload.values():
            if isinstance(value, list):
                items = value
                break
    return items if isinstance(items, list) else []


def extract_facts(
    text: str,
    source_url: str,
    source_title: str | None,
    llm: LLMClient,
    company_domain: str | None = None,
    *,
    max_page_chars: int = MAX_TEXT_CHARS,
    max_facts_per_source: int = MAX_FACTS_PER_SOURCE,
) -> list[Fact]:
    """Extract grounded `Fact`s from a single source's text.

    This is the groundedness guard. It asks the LLM (via
    `LLMClient.complete_json`) to return only claims that the supplied ``text``
    explicitly supports, each with a verbatim ``evidence`` snippet. Every
    candidate is then checked: its evidence must actually appear in the source text
    (a whitespace- and case-insensitive substring match). Candidates that fail the
    check — typically claims the model pulled from its own prior knowledge — are
    dropped and logged. Extraction is capped at ``max_facts_per_source`` per source.

    The source text is truncated to ``max_page_chars`` **before** extraction, and
    the substring check runs against that *same* truncated text — so the model only
    ever sees, and can only ground claims in, the text we actually verify against.
    This truncation is the dominant token/cost lever (see ADR-0012).

    Each surviving fact is tagged with a `SourceTier` via `classify_source_tier`
    (``company_domain`` decides what counts as the company's own site).

    Args:
        text: The clean source text (a fetched page or a search-result snippet).
        source_url: The URL the text came from; becomes each `Fact.source_url`.
        source_title: Human-readable title of the source, if known.
        llm: The LLM client used to perform extraction.
        company_domain: The company's own domain, used to tier the source. When
            omitted, no source can be recognized as ``"own_site"``.
        max_page_chars: Truncate the source text to this many characters before
            extraction (and grounding). Defaults to `MAX_TEXT_CHARS`.
        max_facts_per_source: Stop after this many grounded facts from this source.
            Defaults to `MAX_FACTS_PER_SOURCE`.

    Returns:
        A list of grounded `Fact`s (possibly empty). Never raises for a bad page
        or a bad LLM response — those cases yield an empty list.
    """
    # Truncate ONCE: the model sees exactly the text we will ground against.
    source_text = (text or "").strip()[:max_page_chars]
    if not source_text:
        return []

    user_prompt = (
        f"SOURCE URL: {source_url}\n"
        f"SOURCE TITLE: {source_title or '(unknown)'}\n\n"
        "TEXT:\n"
        f"{source_text}"
    )
    try:
        payload = llm.complete_json(_EXTRACTOR_SYSTEM, user_prompt)
    except LLMError as exc:
        logger.warning("extractor LLM call failed for %s: %s", source_url, exc)
        return []

    normalized_source = _normalize(source_text)
    source_tier = classify_source_tier(source_url, company_domain)
    facts: list[Fact] = []
    for item in _facts_payload(payload):
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim", "")).strip()
        evidence = str(item.get("evidence", "")).strip()
        if not claim or not evidence:
            continue
        # Anti-hallucination check: the evidence must really be in the source text.
        if _normalize(evidence) not in normalized_source:
            logger.info(
                "dropped ungrounded fact from %s (evidence not found in source): %s",
                source_url,
                claim,
            )
            continue
        category = item.get("category")
        if category not in _VALID_CATEGORIES:
            category = None
        try:
            fact = Fact(
                claim=claim,
                source_url=source_url,
                source_title=source_title,
                category=category,
                confidence=_coerce_confidence(item.get("confidence")),
                # A prefix of a verified substring is still a substring, so
                # truncating here keeps the snippet grounded.
                evidence=evidence[:MAX_EVIDENCE_CHARS],
                source_tier=source_tier,
            )
        except ValidationError as exc:
            logger.info("skipped invalid fact from %s: %s", source_url, exc)
            continue
        facts.append(fact)
        if len(facts) >= max_facts_per_source:
            logger.info("hit per-source fact cap (%d) for %s", max_facts_per_source, source_url)
            break
    return facts


def _coverage_summary(company: Company, facts: list[Fact], queries_run: list[str]) -> str:
    """Render the planner's user prompt: what we know and what's still thin.

    Groups the gathered facts by dimension so the planner can see which of
    ``overview`` / ``news`` / ``hiring`` / ``tech`` are thin, and lists the
    queries already run so it does not repeat them.
    """
    by_dim: dict[str, list[str]] = {dim: [] for dim in RESEARCH_DIMENSIONS}
    for fact in facts:
        if fact.category in by_dim:
            by_dim[fact.category].append(fact.claim)

    lines = [
        f"COMPANY: {company.name or company.domain} ({company.domain})",
        "",
        "FACTS GATHERED SO FAR (by dimension):",
    ]
    for dim in RESEARCH_DIMENSIONS:
        claims = by_dim[dim]
        marker = " [THIN]" if not claims else ""
        lines.append(f"- {dim}: {len(claims)} fact(s){marker}")
        for claim in claims[:5]:
            lines.append(f"    - {claim}")

    lines.append("")
    if queries_run:
        lines.append("QUERIES ALREADY RUN (do not repeat any of these):")
        lines.extend(f"- {query}" for query in queries_run)
    else:
        lines.append("QUERIES ALREADY RUN: none yet.")
    lines.append("")
    lines.append("Decide the next search query, or whether research is done.")
    return "\n".join(lines)


def _plan_next_query(
    company: Company,
    facts: list[Fact],
    queries_run: list[str],
    llm: LLMClient,
) -> tuple[bool, str, str | None]:
    """Ask the LLM to choose the next query or declare research done.

    Args:
        company: The company being researched.
        facts: Facts gathered so far (used to summarize coverage).
        queries_run: Queries already executed (so the planner won't repeat them).
        llm: The LLM client used for planning.

    Returns:
        A ``(done, reason, next_query)`` tuple. ``next_query`` is ``None`` when
        the planner is done or did not return a usable query. A failed planner
        call is treated as "done" so the loop ends gracefully.
    """
    try:
        payload = llm.complete_json(
            _PLANNER_SYSTEM, _coverage_summary(company, facts, queries_run)
        )
    except LLMError as exc:
        logger.warning("research planner LLM call failed: %s", exc)
        return True, f"planner error: {exc}", None

    done = bool(payload.get("done", False))
    reason = str(payload.get("reason", "")).strip()
    next_query = payload.get("next_query")
    if isinstance(next_query, str):
        next_query = next_query.strip() or None
    else:
        next_query = None
    return done, reason, next_query


def run_research(
    company: Company,
    llm: LLMClient,
    search: SearchClient,
    settings: Settings,
) -> ResearchResult:
    """Run the agentic research loop for a company and return grounded facts.

    The loop is LLM-driven (see the module docstring): it seeds from the
    company's own site, then repeatedly lets the planner choose the next search
    query — extracting source-tagged `Fact`s from every page and search result —
    until the planner declares it done or the query budget
    (`Settings.research_max_queries`) is exhausted. The budget is a hard cap that
    always overrides the planner's wish to continue.

    Args:
        company: The company to research (its ``domain`` is the seed).
        llm: LLM client used for both planning and extraction.
        search: Search client used to run the planner's chosen queries.
        settings: Settings supplying ``research_max_queries`` and friends.

    Returns:
        A `ResearchResult` whose ``facts`` are all grounded (each carries an
        ``http(s)`` ``source_url`` and a verbatim ``evidence`` snippet), whose
        ``queries_run`` records the LLM-chosen query sequence, and whose
        ``errors`` collects any non-fatal failures encountered along the way.
    """
    result = ResearchResult(company=company)
    seen_claims: set[str] = set()

    def accumulate(new_facts: list[Fact]) -> int:
        """Add new facts, de-duplicating by normalized claim text."""
        added = 0
        for fact in new_facts:
            key = _normalize(fact.claim)
            if key in seen_claims:
                continue
            seen_claims.add(key)
            result.facts.append(fact)
            added += 1
        return added

    # 1. SEED — fetch the company's own site and extract the first facts.
    seed_url = _seed_url(company.domain)
    try:
        seed_text = fetch_page(seed_url)
    except Exception as exc:  # noqa: BLE001 — never crash research on a bad page
        seed_text = ""
        result.errors.append(f"seed fetch raised for {seed_url}: {exc}")
    if seed_text:
        accumulate(extract_facts(
            seed_text, seed_url, company.name, llm, company.domain,
            max_page_chars=settings.research_max_page_chars,
            max_facts_per_source=settings.research_max_facts_per_source,
        ))
    else:
        result.errors.append(f"seed page returned no usable text: {seed_url}")

    # 2-5. Agentic loop: PLAN -> (stop?) -> SEARCH -> EXTRACT -> reflect.
    max_queries = settings.research_max_queries
    while True:
        # Budget is a hard cap over the agentic choice. Checked *before* planning so
        # we never spend a planner LLM call whose query we could not act on anyway.
        if len(result.queries_run) >= max_queries:
            logger.info("research hit query budget (%d); stopping.", max_queries)
            break
        done, reason, next_query = _plan_next_query(
            company, result.facts, result.queries_run, llm
        )
        if done or not next_query:
            logger.info("research planner stopped: %s", reason or "done")
            break
        if next_query in result.queries_run:
            logger.info("planner repeated query %r; stopping to avoid a loop.", next_query)
            break

        result.queries_run.append(next_query)
        try:
            hits = search.search(next_query, max_results=SEARCH_RESULTS_PER_QUERY)
        except Exception as exc:  # noqa: BLE001 — a failed search must not crash the run
            result.errors.append(f"search failed for {next_query!r}: {exc}")
            continue
        if not hits:
            result.errors.append(f"no search results for query: {next_query!r}")
            continue

        for hit in hits:
            if not hit.content or not hit.url:
                continue
            try:
                hit_facts = extract_facts(
                    hit.content, hit.url, hit.title, llm, company.domain,
                    max_page_chars=settings.research_max_page_chars,
                    max_facts_per_source=settings.research_max_facts_per_source,
                )
            except Exception as exc:  # noqa: BLE001 — extraction must not crash the run
                result.errors.append(f"extraction failed for {hit.url}: {exc}")
                continue
            accumulate(hit_facts)

    return result


def research_node(
    state: PipelineState,
    *,
    llm: LLMClient | None = None,
    search: SearchClient | None = None,
    settings: Settings | None = None,
) -> dict:
    """Graph adapter: run research for ``state.company`` and return the update.

    A thin wrapper that calls `run_research` and returns the partial state update
    the LangGraph pipeline merges in. Dependencies default to the configured
    clients (via `get_settings` / `get_llm_client` / `get_search_client`) but can
    be injected — `build_pipeline` passes them so the whole graph can run on
    mocked clients with no network.

    Args:
        state: The pipeline state; only ``state.company`` is read.
        llm: LLM client; built from settings when omitted.
        search: Search client; built from settings when omitted.
        settings: Settings; loaded via `get_settings` when omitted.

    Returns:
        A dict ``{"research": ResearchResult}`` to merge into the pipeline state.
    """
    settings = settings or get_settings()
    llm = llm or get_llm_client(settings)
    search = search or get_search_client(settings)
    research = run_research(state.company, llm, search, settings)
    return {"research": research}
