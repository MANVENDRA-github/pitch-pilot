"""P1 LIVE VALIDATION harness — audit the agentic research node against real sites.

This is a DIAGNOSTIC tool, not part of the package. It runs `run_research`
against a few real domains and captures everything an auditor needs to judge
whether P1 is solid enough to build P2 on:

* the full list of grounded `Fact`s (claim / category / source_url / evidence),
* the LLM-chosen ``queries_run`` sequence and any ``result.errors``,
* how many candidate facts the extractor DROPPED on its evidence check
  (captured from the ``pitch_pilot.nodes.research`` logger),
* characters pulled from the company's OWN site fetch vs. from search results,
* call counts (LLM planner / LLM extractor / search) and wall-clock time,
* an INDEPENDENT groundedness re-check: each sampled fact's ``source_url`` is
  re-fetched with httpx and its ``evidence`` snippet is re-checked against the
  freshly fetched text (whitespace/case-insensitive) — PASS/FAIL per fact,
* an optional small LLM judge pass: faithful / overreach / unsupported for a
  handful of (claim, evidence) pairs.

It NEVER mutates package source. It wraps the real clients in counting proxies,
monkeypatches ``research.fetch_page`` only at runtime (to measure the seed
fetch), and attaches a log handler. Results are written to a JSON file and a
human-readable summary is printed.

Usage (Windows / PowerShell)::

    .\\.venv\\Scripts\\Activate.ps1
    python scripts\\validate_p1_research.py
    python scripts\\validate_p1_research.py stripe.com posthog.com nilenso.com
    python scripts\\validate_p1_research.py --no-judge --delay 8 --out audit.json

Needs the same real API keys as the smoke test (GEMINI_API_KEY, TAVILY_API_KEY
in ``.env`` or the environment). Free-tier friendly: keep the domain list small,
keep the default delay between domains, and the judge sample tiny.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass, field

# --- Package imports (the things under audit) -------------------------------
from pitch_pilot.clients.fetch import fetch_page as real_fetch_page
from pitch_pilot.clients.llm import LLMClient, get_llm_client
from pitch_pilot.clients.search import SearchClient, get_search_client
from pitch_pilot.config import ConfigError, get_settings
from pitch_pilot.models.lead import Company
from pitch_pilot.models.search import SearchResult
from pitch_pilot.nodes import research as research_mod
from pitch_pilot.nodes.research import (
    _EXTRACTOR_SYSTEM,
    _PLANNER_SYSTEM,
    _normalize,
    _seed_url,
    run_research,
)

DEFAULT_DOMAINS = ["stripe.com", "posthog.com", "nilenso.com"]
DEFAULT_OUT = "scripts/p1_validation_data.json"
DEFAULT_REPORT = "docs/validation/p1-research-audit.md"

# Backoff defaults for graceful 429 / quota handling (free-tier friendly).
RATE_LIMIT_MAX_RETRIES = 4
RATE_LIMIT_BASE_DELAY = 8.0

# Heuristics for spotting rate limits / quota exhaustion in raw vendor errors.
_RATE_LIMIT_MARKERS = ("429", "rate limit", "ratelimit", "quota", "resource_exhausted",
                       "resource exhausted", "too many requests")


def _looks_rate_limited(message: str) -> bool:
    low = message.lower()
    return any(marker in low for marker in _RATE_LIMIT_MARKERS)


def _call_with_backoff(fn, *, max_retries: int, base_delay: float, on_rate_limit=None):
    """Call ``fn()``; on a rate-limit error, sleep with exponential backoff and retry.

    Non-rate-limit exceptions propagate immediately (we want to observe the node's
    true behavior on genuine bugs). Rate-limit exceptions are retried up to
    ``max_retries`` times with delays of ``base_delay * 2**attempt`` seconds; each
    occurrence (including the final, retries-exhausted one) is reported via
    ``on_rate_limit(message)`` so the caller can count it. After exhaustion the last
    exception is re-raised.

    Args:
        fn: Zero-arg callable performing the rate-limited request.
        max_retries: How many times to retry after the first rate-limit failure.
        base_delay: Base seconds for the exponential backoff.
        on_rate_limit: Optional callback invoked with the error string on each
            rate-limit detection.

    Returns:
        Whatever ``fn()`` returns on success.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — classify, then retry or re-raise
            msg = f"{type(exc).__name__}: {exc}"
            if not _looks_rate_limited(msg):
                raise
            if on_rate_limit is not None:
                on_rate_limit(msg)
            if attempt >= max_retries:
                raise
            delay = base_delay * (2 ** attempt)
            logging.getLogger("validate").warning(
                "rate limited (attempt %d/%d) — backing off %.0fs: %s",
                attempt + 1, max_retries, delay, msg)
            time.sleep(delay)
            attempt += 1


# ---------------------------------------------------------------------------
# Counting proxies — wrap the real clients without touching package source.
# ---------------------------------------------------------------------------
class CountingLLM:
    """Wraps an `LLMClient`, counting calls and tagging planner vs extractor.

    Distinguishes the two call sites by comparing the ``system`` prompt against
    the research module's own ``_PLANNER_SYSTEM`` / ``_EXTRACTOR_SYSTEM``
    constants, so the audit can report calls per role. Raw vendor exceptions are
    recorded (with a rate-limit flag) and then RE-RAISED, so the harness observes
    the node's true degradation behavior rather than masking it.
    """

    def __init__(self, inner: LLMClient, max_retries: int = RATE_LIMIT_MAX_RETRIES,
                 base_delay: float = RATE_LIMIT_BASE_DELAY) -> None:
        self.inner = inner
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.planner_calls = 0
        self.extractor_calls = 0
        self.other_calls = 0
        self.errors: list[dict] = []
        self.rate_limited = 0

    def _flag_rate_limit(self, msg: str) -> None:
        self.errors.append({"msg": msg, "rate_limited": True})
        self.rate_limited += 1

    def _tag(self, system: str) -> None:
        if system == _PLANNER_SYSTEM:
            self.planner_calls += 1
        elif system == _EXTRACTOR_SYSTEM:
            self.extractor_calls += 1
        else:
            self.other_calls += 1

    @property
    def total_calls(self) -> int:
        return self.planner_calls + self.extractor_calls + self.other_calls

    def complete(self, system: str, user: str) -> str:
        self._tag(system)
        return _call_with_backoff(
            lambda: self.inner.complete(system, user),
            max_retries=self.max_retries, base_delay=self.base_delay,
            on_rate_limit=self._flag_rate_limit)

    def complete_json(self, system: str, user: str) -> dict:
        self._tag(system)
        try:
            return _call_with_backoff(
                lambda: self.inner.complete_json(system, user),
                max_retries=self.max_retries, base_delay=self.base_delay,
                on_rate_limit=self._flag_rate_limit)
        except Exception as exc:  # noqa: BLE001 — non-rate-limit error: record, re-raise
            msg = f"{type(exc).__name__}: {exc}"
            if not _looks_rate_limited(msg):
                self.errors.append({"msg": msg, "rate_limited": False})
            raise


class CountingSearch:
    """Wraps a `SearchClient`, counting queries and summing returned content."""

    def __init__(self, inner: SearchClient, max_retries: int = RATE_LIMIT_MAX_RETRIES,
                 base_delay: float = RATE_LIMIT_BASE_DELAY) -> None:
        self.inner = inner
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.calls = 0
        self.total_result_chars = 0
        self.total_hits = 0
        self.per_query: list[dict] = []
        self.errors: list[dict] = []
        self.rate_limited = 0

    def _flag_rate_limit(self, query: str, msg: str) -> None:
        self.errors.append({"query": query, "msg": msg, "rate_limited": True})
        self.rate_limited += 1

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        self.calls += 1
        try:
            hits = _call_with_backoff(
                lambda: self.inner.search(query, max_results=max_results),
                max_retries=self.max_retries, base_delay=self.base_delay,
                on_rate_limit=lambda msg: self._flag_rate_limit(query, msg))
        except Exception as exc:  # noqa: BLE001 — non-rate-limit error: record, re-raise
            msg = f"{type(exc).__name__}: {exc}"
            if not _looks_rate_limited(msg):
                self.errors.append({"query": query, "msg": msg, "rate_limited": False})
            raise
        chars = sum(len(h.content or "") for h in hits)
        self.total_result_chars += chars
        self.total_hits += len(hits)
        self.per_query.append({"query": query, "hits": len(hits), "chars": chars})
        return hits


class SeedFetchRecorder:
    """Monkeypatch target for ``research.fetch_page`` — records each fetch.

    The research node only calls ``fetch_page`` for the company's own seed page;
    recording every call lets the audit report the OWN-SITE char count and spot
    near-empty (JS-rendered) sites that force reliance on search alone.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, url: str, timeout: float = 10) -> str:
        text = real_fetch_page(url, timeout=timeout)
        self.calls.append({"url": url, "chars": len(text)})
        return text


class _DropCaptureHandler(logging.Handler):
    """Captures records from the research logger so we can count dropped facts."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append({"level": record.levelname, "msg": record.getMessage()})
        except Exception:  # noqa: BLE001 — logging must never raise
            pass

    @property
    def dropped(self) -> int:
        return sum(1 for r in self.records if r["msg"].startswith("dropped ungrounded fact"))

    @property
    def cap_hits(self) -> int:
        return sum(1 for r in self.records if "per-source fact cap" in r["msg"])


# ---------------------------------------------------------------------------
# Independent groundedness re-check + optional LLM judge.
# ---------------------------------------------------------------------------
def _diverse_sample(facts: list, k: int) -> list:
    """Pick up to ``k`` facts spread across as many distinct source_urls as possible."""
    by_src: dict[str, list] = {}
    for f in facts:
        by_src.setdefault(f.source_url, []).append(f)
    out: list = []
    # Round-robin across sources so the sample isn't dominated by one page.
    while len(out) < k and any(by_src.values()):
        for src in list(by_src.keys()):
            bucket = by_src[src]
            if bucket:
                out.append(bucket.pop(0))
                if len(out) >= k:
                    break
    return out


def independent_recheck(facts: list, seed_url: str, sample_size: int) -> list[dict]:
    """Re-fetch each sampled fact's source_url and re-check the evidence substring.

    Returns one record per checked fact: whether the (re-extracted) live page text
    still contains the evidence snippet, plus whether the source was the company's
    own seed page or a search hit (a seed FAIL is far more meaningful than a search
    FAIL, since search facts were grounded against Tavily's snippet, not the page).
    """
    sample = _diverse_sample(facts, sample_size)
    page_cache: dict[str, str] = {}
    out: list[dict] = []
    for f in sample:
        url = f.source_url
        if url not in page_cache:
            try:
                page_cache[url] = real_fetch_page(url)
            except Exception as exc:  # noqa: BLE001
                page_cache[url] = ""
                logging.getLogger("recheck").warning("re-fetch failed for %s: %s", url, exc)
            time.sleep(0.5)  # be gentle on the re-fetched hosts
        page_text = page_cache[url]
        norm_page = _normalize(page_text)
        norm_ev = _normalize(f.evidence)
        reachable = bool(page_text)
        found = bool(norm_ev) and norm_ev in norm_page
        out.append({
            "claim": f.claim,
            "category": f.category,
            "source_url": url,
            "evidence": f.evidence,
            "is_seed_source": url.rstrip("/") == seed_url.rstrip("/"),
            "source_reachable": reachable,
            "refetch_chars": len(page_text),
            "evidence_found": found,
            "verdict": "PASS" if found else ("DEAD_SOURCE" if not reachable else "FAIL"),
        })
    return out


_JUDGE_SYSTEM = (
    "You are a strict groundedness judge. Given a CLAIM and an EVIDENCE snippet "
    "copied from a source, decide whether the evidence supports the claim. Reply "
    "with JSON: {\"verdict\": one of \"faithful\"|\"overreach\"|\"unsupported\", "
    "\"reason\": \"<one short sentence>\"}. 'faithful' = the evidence directly "
    "states the claim; 'overreach' = the evidence is related but the claim adds "
    "or exaggerates beyond it; 'unsupported' = the evidence does not back the claim."
)


def judge_sample(facts: list, llm: LLMClient, k: int) -> list[dict]:
    """Use one LLM call per fact (<= k) to judge claim-vs-evidence faithfulness."""
    out: list[dict] = []
    for f in _diverse_sample(facts, k):
        user = f"CLAIM: {f.claim}\n\nEVIDENCE: {f.evidence}"
        try:
            payload = llm.complete_json(_JUDGE_SYSTEM, user)
            verdict = str(payload.get("verdict", "")).strip().lower() or "unknown"
            reason = str(payload.get("reason", "")).strip()
        except Exception as exc:  # noqa: BLE001
            verdict, reason = "judge_error", f"{type(exc).__name__}: {exc}"
        out.append({"claim": f.claim, "evidence": f.evidence,
                    "judge_verdict": verdict, "judge_reason": reason})
        time.sleep(0.3)
    return out


# ---------------------------------------------------------------------------
# Per-domain run.
# ---------------------------------------------------------------------------
@dataclass
class DomainRun:
    domain: str
    ok: bool = False
    crash: str | None = None
    data: dict = field(default_factory=dict)


def run_one_domain(domain: str, settings, sample_size: int, judge_size: int) -> DomainRun:
    """Run research for one domain under full instrumentation and capture the audit data."""
    out = DomainRun(domain=domain)

    counting_llm = CountingLLM(get_llm_client(settings))
    counting_search = CountingSearch(get_search_client(settings))
    seed_recorder = SeedFetchRecorder()
    drop_handler = _DropCaptureHandler()

    research_logger = logging.getLogger("pitch_pilot.nodes.research")
    prev_level = research_logger.level
    research_logger.setLevel(logging.INFO)
    research_logger.addHandler(drop_handler)
    original_fetch = research_mod.fetch_page
    research_mod.fetch_page = seed_recorder  # runtime patch only; restored in finally

    t0 = time.perf_counter()
    try:
        result = run_research(Company(domain=domain), counting_llm, counting_search, settings)
        wall = time.perf_counter() - t0
        out.ok = True
    except Exception:  # noqa: BLE001 — capture a hard crash and keep auditing others
        wall = time.perf_counter() - t0
        out.crash = traceback.format_exc()
        result = None
    finally:
        research_mod.fetch_page = original_fetch
        research_logger.removeHandler(drop_handler)
        research_logger.setLevel(prev_level)

    seed_url = _seed_url(domain)
    seed_chars = seed_recorder.calls[0]["chars"] if seed_recorder.calls else 0

    common = {
        "wall_clock_s": round(wall, 2),
        "llm_calls": {
            "total": counting_llm.total_calls,
            "planner": counting_llm.planner_calls,
            "extractor": counting_llm.extractor_calls,
            "other": counting_llm.other_calls,
        },
        "llm_errors": counting_llm.errors,
        "llm_rate_limited": counting_llm.rate_limited,
        "search_calls": counting_search.calls,
        "search_total_hits": counting_search.total_hits,
        "search_errors": counting_search.errors,
        "search_rate_limited": counting_search.rate_limited,
        "chars_own_site": seed_chars,
        "chars_search_results": counting_search.total_result_chars,
        "seed_fetches": seed_recorder.calls,
        "per_query_search": counting_search.per_query,
        "dropped_facts": drop_handler.dropped,
        "per_source_cap_hits": drop_handler.cap_hits,
        "research_log": drop_handler.records,
    }

    if result is None:
        out.data = {**common, "crashed": True}
        return out

    facts_payload = [
        {"claim": f.claim, "category": f.category, "source_url": f.source_url,
         "source_title": f.source_title, "confidence": f.confidence, "evidence": f.evidence}
        for f in result.facts
    ]
    by_cat: dict[str, int] = {}
    for f in result.facts:
        by_cat[f.category or "(uncategorized)"] = by_cat.get(f.category or "(uncategorized)", 0) + 1

    recheck = independent_recheck(result.facts, seed_url, sample_size)
    passed = sum(1 for r in recheck if r["verdict"] == "PASS")
    seed_checks = [r for r in recheck if r["is_seed_source"]]
    seed_passed = sum(1 for r in seed_checks if r["verdict"] == "PASS")

    judged = judge_sample(result.facts, counting_llm, judge_size) if judge_size > 0 else []

    out.data = {
        **common,
        "crashed": False,
        "total_facts": len(result.facts),
        "source_count": result.source_count,
        "facts_per_category": by_cat,
        "queries_run": result.queries_run,
        "errors": result.errors,
        "facts": facts_payload,
        "recheck": {
            "checked": len(recheck),
            "passed": passed,
            "rate": round(passed / len(recheck), 3) if recheck else None,
            "seed_checked": len(seed_checks),
            "seed_passed": seed_passed,
            "details": recheck,
        },
        "judge": judged,
    }
    return out


# ---------------------------------------------------------------------------
# Markdown audit report.
# ---------------------------------------------------------------------------
def _is_broken(d: dict) -> bool:
    """True if a per-domain record represents a hard crash (no usable data)."""
    return bool(d.get("crash") or d.get("crashed"))


def overall_verdict(domains: list[dict]) -> tuple[str, str]:
    """Decide the overall P2-readiness verdict from the per-domain audit records.

    Returns:
        A ``(label, headline)`` pair. ``label`` is ``"SOLID FOR P2"``,
        ``"TUNE-FIRST"``, or ``"INCONCLUSIVE"``; ``headline`` is a one-line reason.
    """
    crashed = [d for d in domains if _is_broken(d)]
    rates = [
        (d.get("recheck", {}).get("rate") or 0.0)
        for d in domains if not _is_broken(d) and d.get("recheck", {}).get("checked")
    ]
    if crashed:
        names = ", ".join(f"`{d['domain']}`" for d in crashed)
        return ("TUNE-FIRST", f"{len(crashed)} domain(s) crashed mid-run ({names}) — robustness gap before P2.")
    if not rates:
        return ("INCONCLUSIVE", "No domains produced re-checkable facts.")
    lo = min(rates)
    if lo >= 0.8:
        return ("SOLID FOR P2", "All domains met >= 0.80 independent groundedness with no crashes.")
    if lo >= 0.5:
        return ("TUNE-FIRST", f"At least one domain scored {lo:.0%}-80% independent groundedness — investigate before building on it.")
    return ("TUNE-FIRST", f"At least one domain scored below 50% ({lo:.0%}) independent groundedness.")


def build_recommendations(domains: list[dict]) -> list[str]:
    """Derive prioritized tuning recommendations from the per-domain signals."""
    recs: list[str] = []
    for d in domains:
        if _is_broken(d):
            recs.append(
                f"**[P0 robustness]** `{d['domain']}` crashed mid-run — a bad page or "
                f"source aborted the whole domain. Make `run_research` degrade to a "
                f"recorded error instead of raising.")
    rl = sum(d.get("llm_rate_limited", 0) + d.get("search_rate_limited", 0)
             for d in domains if not _is_broken(d))
    if rl:
        recs.append(
            f"**[infra/pacing]** {rl} rate-limit hit(s) observed (auto-retried with backoff). "
            f"On free tier, raise `--delay`, lower `RESEARCH_MAX_QUERIES`, or pace requests.")
    for d in domains:
        if _is_broken(d):
            continue
        rc = d.get("recheck", {})
        dead = [x for x in rc.get("details", []) if x.get("verdict") == "DEAD_SOURCE"]
        if dead:
            recs.append(
                f"**[source-freshness]** `{d['domain']}` had {len(dead)} source(s) unreachable "
                f"on independent re-fetch — facts cite pages that later 404/blocked. Consider "
                f"re-validating source liveness at draft time.")
        sc, sp = rc.get("seed_checked", 0), rc.get("seed_passed", 0)
        if sc and sp < sc:
            recs.append(
                f"**[extractor grounding]** `{d['domain']}` failed {sc - sp}/{sc} seed-sourced "
                f"fact(s) on live re-check — evidence passed the substring guard at extract time "
                f"but not on re-fetch. Investigate normalization / dynamic content.")
        facts = d.get("total_facts") or 0
        dropped = d.get("dropped_facts") or 0
        if dropped and dropped >= max(3, facts):
            recs.append(
                f"**[extractor yield]** `{d['domain']}` dropped {dropped} candidate fact(s) "
                f"(kept {facts}) on the evidence check — the LLM is paraphrasing evidence instead "
                f"of copying it verbatim. Tighten the extractor prompt to quote source text exactly.")
        bad = [j for j in d.get("judge", []) if j.get("judge_verdict") in ("overreach", "unsupported")]
        if bad:
            recs.append(
                f"**[claim faithfulness / P3]** `{d['domain']}` had {len(bad)} sampled claim(s) "
                f"judged overreach/unsupported — claims drift beyond their evidence. Relevant for "
                f"the P3 verification gate.")
    return recs


def _trunc(text: str, n: int) -> str:
    text = (text or "").replace("\n", " ").replace("|", "\\|").strip()
    return text if len(text) <= n else text[: n - 3] + "..."


def build_markdown(dump: dict, run_date: str) -> str:
    """Render the human-readable P1 validation audit as Markdown."""
    domains = dump.get("domains", [])
    label, headline = overall_verdict(domains)
    recs = build_recommendations(domains)

    L: list[str] = []
    L.append(f"> **Last updated:** {run_date} · **Source files:** "
             f"`src/pitch_pilot/nodes/research.py`, `scripts/validate_p1_research.py`")
    L.append("")
    L.append("# P1 Research — Live Validation Audit")
    L.append("")
    L.append("Live audit of the agentic research node (`run_research`) against real domains "
             "with network access, generated by `scripts/validate_p1_research.py`. "
             "**Do not hand-edit — re-run the script to regenerate.**")
    L.append("")
    L.append(f"- **Run date:** {run_date}")
    L.append(f"- **LLM provider:** `{dump.get('provider')}`")
    L.append(f"- **`RESEARCH_MAX_QUERIES`:** {dump.get('research_max_queries')}")
    L.append(f"- **Domains audited:** " + ", ".join(f"`{d['domain']}`" for d in domains))
    L.append("")
    L.append("## Verdict")
    L.append("")
    answer = "**Yes — solid enough for P2.**" if label == "SOLID FOR P2" else \
             ("**Inconclusive.**" if label == "INCONCLUSIVE" else "**No — tune first.**")
    L.append(f"{answer} {headline}")
    L.append("")
    L.append("> _\"Independent groundedness\" = the script re-fetched each sampled fact's "
             "`source_url` over the network and confirmed the `evidence` snippet is still a "
             "substring of the live page (whitespace/case-insensitive). This is a stricter, "
             "out-of-band check than the extractor's own evidence guard. Seed-sourced FAILs "
             "(the company's own site) matter more than search-sourced ones, which were grounded "
             "against the search snippet rather than the live page._")
    L.append("")
    L.append("## Per-domain summary")
    L.append("")
    L.append("| Domain | Facts | Sources | Queries | Dropped | Independent groundedness | Dead src | LLM calls | Search | Time (s) |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for d in domains:
        if _is_broken(d):
            L.append(f"| `{d['domain']}` | — | — | — | — | **CRASHED** | — | — | — | {d.get('wall_clock_s', '?')} |")
            continue
        rc = d.get("recheck", {})
        rate = rc.get("rate")
        dead = sum(1 for x in rc.get("details", []) if x.get("verdict") == "DEAD_SOURCE")
        gr = f"{rc.get('passed')}/{rc.get('checked')} ({rate})" if rc.get("checked") else "n/a"
        L.append(f"| `{d['domain']}` | {d.get('total_facts')} | {d.get('source_count')} | "
                 f"{len(d.get('queries_run', []))} | {d.get('dropped_facts')} | {gr} | {dead} | "
                 f"{d.get('llm_calls', {}).get('total')} | {d.get('search_calls')} | {d.get('wall_clock_s')} |")
    L.append("")
    L.append("## Tuning recommendations")
    L.append("")
    if recs:
        L.append("Prioritized (bracketed tag = area / phase):")
        L.append("")
        for i, r in enumerate(recs, 1):
            L.append(f"{i}. {r}")
    else:
        L.append("None — no groundedness, robustness, or pacing issues surfaced in this run.")
    L.append("")
    L.append("## Per-domain detail")
    L.append("")
    for d in domains:
        L.append(f"### `{d['domain']}`")
        L.append("")
        if _is_broken(d):
            crash = (d.get("crash") or "").strip()
            last = crash.splitlines()[-1] if crash else "(no traceback captured)"
            L.append(f"**CRASHED** after {d.get('wall_clock_s', '?')}s. Last traceback line:")
            L.append("")
            L.append(f"`{_trunc(last, 300)}`")
            L.append("")
            continue
        llmc = d.get("llm_calls", {})
        L.append(f"- Facts: **{d.get('total_facts')}** across {d.get('source_count')} source(s); "
                 f"by category: `{d.get('facts_per_category')}`")
        L.append(f"- Dropped on evidence check: **{d.get('dropped_facts')}**; "
                 f"per-source cap hits: {d.get('per_source_cap_hits')}")
        L.append(f"- Own-site fetch: {d.get('chars_own_site')} chars; "
                 f"search-result text: {d.get('chars_search_results')} chars")
        L.append(f"- Calls: LLM total {llmc.get('total')} "
                 f"(planner {llmc.get('planner')}, extractor {llmc.get('extractor')}, other {llmc.get('other')}); "
                 f"search {d.get('search_calls')} ({d.get('search_total_hits')} hits)")
        L.append(f"- Rate-limit hits: LLM {d.get('llm_rate_limited')}, search {d.get('search_rate_limited')}")
        L.append(f"- Wall-clock: {d.get('wall_clock_s')}s")
        qs = d.get("queries_run", [])
        if qs:
            L.append(f"- Queries run ({len(qs)}): " + ", ".join(f"`{_trunc(q, 80)}`" for q in qs))
        errs = d.get("errors", [])
        if errs:
            L.append(f"- Non-fatal errors ({len(errs)}):")
            for e in errs:
                L.append(f"    - {_trunc(str(e), 200)}")
        L.append("")
        rc = d.get("recheck", {})
        details = rc.get("details", [])
        if details:
            L.append(f"**Independent groundedness re-check** — {rc.get('passed')}/{rc.get('checked')} PASS "
                     f"(seed-sourced: {rc.get('seed_passed')}/{rc.get('seed_checked')}):")
            L.append("")
            L.append("| Verdict | Category | Seed? | Claim | Source |")
            L.append("| --- | --- | --- | --- | --- |")
            for x in details:
                seed = "yes" if x.get("is_seed_source") else "no"
                L.append(f"| {x.get('verdict')} | {x.get('category')} | {seed} | "
                         f"{_trunc(x.get('claim'), 90)} | {_trunc(x.get('source_url'), 60)} |")
            L.append("")
        judged = d.get("judge", [])
        if judged:
            L.append("**LLM faithfulness judge** (sampled claim vs. evidence):")
            L.append("")
            L.append("| Verdict | Claim | Reason |")
            L.append("| --- | --- | --- |")
            for j in judged:
                L.append(f"| {j.get('judge_verdict')} | {_trunc(j.get('claim'), 70)} | "
                         f"{_trunc(j.get('judge_reason'), 90)} |")
            L.append("")
    L.append("---")
    L.append("")
    L.append("_Generated by `scripts/validate_p1_research.py`. Raw data dump alongside this report._")
    L.append("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P1 live research validation harness.")
    parser.add_argument("domains", nargs="*", default=None,
                        help=f"Domains to audit (default: {' '.join(DEFAULT_DOMAINS)})")
    parser.add_argument("--delay", type=float, default=6.0,
                        help="Seconds to sleep between domains (free-tier courtesy).")
    parser.add_argument("--sample-size", type=int, default=8,
                        help="Max facts per domain for the independent re-check.")
    parser.add_argument("--judge-size", type=int, default=3,
                        help="Max facts per domain for the LLM claim-vs-evidence judge (0 disables).")
    parser.add_argument("--no-judge", action="store_true", help="Disable the LLM judge pass.")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Where to write the JSON data dump.")
    parser.add_argument("--report", default=DEFAULT_REPORT,
                        help="Where to write the Markdown audit report.")
    args = parser.parse_args(argv)

    domains = args.domains or DEFAULT_DOMAINS
    judge_size = 0 if args.no_judge else args.judge_size

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    try:
        settings = get_settings()
    except ConfigError as exc:
        print(f"[FAIL] Config: {exc}")
        print("\nCreate a .env (copy .env.example to .env and fill in your keys), then re-run.")
        return 1

    print(f"P1 validation — provider={settings.llm_provider}, "
          f"max_queries={settings.research_max_queries}, domains={domains}\n")

    runs: list[DomainRun] = []
    for i, domain in enumerate(domains):
        print(f"=== [{i+1}/{len(domains)}] researching {domain} ...", flush=True)
        run = run_one_domain(domain, settings, args.sample_size, judge_size)
        if run.crash:
            print(f"    !! CRASHED: {run.crash.splitlines()[-1]}")
        else:
            d = run.data
            rc = d.get("recheck", {})
            print(f"    facts={d.get('total_facts')} sources={d.get('source_count')} "
                  f"queries={len(d.get('queries_run', []))} dropped={d.get('dropped_facts')} "
                  f"recheck={rc.get('passed')}/{rc.get('checked')} "
                  f"llm_calls={d['llm_calls']['total']} search={d['search_calls']} "
                  f"time={d['wall_clock_s']}s")
        runs.append(run)
        if i < len(domains) - 1 and args.delay > 0:
            time.sleep(args.delay)

    dump = {
        "provider": settings.llm_provider,
        "research_max_queries": settings.research_max_queries,
        "domains": [{"domain": r.domain, "ok": r.ok, "crash": r.crash, **r.data} for r in runs],
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(dump, fh, indent=2, ensure_ascii=False)

    # ---- Markdown audit report ----
    run_date = datetime.date.today().isoformat()
    report_md = build_markdown(dump, run_date)
    report_dir = os.path.dirname(args.report)
    if report_dir:
        os.makedirs(report_dir, exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as fh:
        fh.write(report_md)

    # ---- Console summary (per-domain groundedness rate, drop count, verdict) ----
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for r in runs:
        if r.crash:
            print(f"  {r.domain:16s} CRASHED")
            continue
        d = r.data
        rc = d.get("recheck", {})
        rate = rc.get("rate")
        dead = sum(1 for x in rc.get("details", []) if x["verdict"] == "DEAD_SOURCE")
        print(f"  {r.domain:16s} independent groundedness "
              f"{rc.get('passed')}/{rc.get('checked')} (rate={rate})  "
              f"dropped={d.get('dropped_facts')}  dead_sources={dead}  facts={d.get('total_facts')}")

    # Single source of truth for the verdict — the same function the report uses.
    label, headline = overall_verdict(dump["domains"])
    print(f"\n  VERDICT: {label} — {headline}")
    print(f"  Report written to: {args.report}")
    print(f"  Full data written to: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
