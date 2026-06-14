"""The pitch-pilot eval runner — rate-limit-resilient, resumable, cached.

This produces the headline numbers the project is judged on, and it is built for
free-tier reality: research (the expensive ~22-of-~30 LLM calls) is **cached per
domain**, each company's result is **checkpointed** as it finishes (so a run
resumes across sessions/days when free tiers reset), and rate-limit errors are
**backed off and retried** rather than aborting the run.

Commands (``python -m evals.run_eval <command>``):

* ``run`` (default) — evaluate each company (research[cached] → qualify → draft →
  verify), checkpoint results, write a report, print aggregates.
* ``redraft`` — re-run **only** draft + verify for already-qualified companies,
  reusing cached research **and** each record's frozen qualification verdict (the
  qualification matrix is never recomputed). Rewrites the results file in place.
  Use this to refresh draft/verify numbers after changing the draft or verify logic
  without paying for research or perturbing qualification.
* ``recheck`` — the honest, network-bounded metric: re-fetch each used claim's
  source and confirm the evidence still appears, reporting live-verifiability
  **by tier**. Run separately from ``run``.
* ``report`` — recompute metrics from existing results (+ recheck cache) and
  re-write the report, no network.

Run it on a capable model — the 8B model malforms the structured JSON; use Groq's
70B (``GROQ_MODEL=llama-3.3-70b-versatile``) or Gemini.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import re
import time
from pathlib import Path

from pitch_pilot.clients.fetch import fetch_page
from pitch_pilot.clients.llm import LLMClient, LLMError, get_llm_client
from pitch_pilot.clients.search import get_search_client
from pitch_pilot.config import ConfigError, Settings, get_settings
from pitch_pilot.models.icp import ICP, load_icp
from pitch_pilot.models.lead import Company
from pitch_pilot.models.qualification import QualificationResult
from pitch_pilot.models.research import ResearchResult
from pitch_pilot.nodes.draft import run_draft
from pitch_pilot.nodes.qualify import run_qualification
from pitch_pilot.nodes.research import _normalize, run_research
from pitch_pilot.nodes.verify import run_verification
from evals import metrics as metrics_mod

logger = logging.getLogger(__name__)

_EVALS_DIR = Path(__file__).resolve().parent
CACHE_DIR = _EVALS_DIR / "cache"
RESULTS_DIR = _EVALS_DIR / "results"
REPORTS_DIR = _EVALS_DIR / "reports"
DEFAULT_ICP = "examples/eval_icp.json"
DEFAULT_COMPANIES = "examples/eval_companies.json"

# Heuristics for spotting rate-limit / quota exhaustion in a (normalized) LLMError.
_RATE_MARKERS = ("429", "rate limit", "ratelimit", "rate_limit", "resource_exhausted",
                 "resource exhausted", "too many requests", "quota")
_RETRY_AFTER = re.compile(r"(?:try again in|retry in|retrydelay['\"\s:=]+)\s*([0-9.]+)\s*s", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Rate-limit handling.
# ---------------------------------------------------------------------------
def _is_rate_limited(message: str) -> bool:
    """True if an error message looks like a provider rate-limit / quota error."""
    low = message.lower()
    return any(marker in low for marker in _RATE_MARKERS)


def _retry_after_seconds(message: str) -> float | None:
    """Parse a provider-suggested retry delay (seconds) from an error message, if any."""
    match = _RETRY_AFTER.search(message)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


class RetryingLLM:
    """Wraps an `LLMClient`, retrying rate-limit errors with exponential backoff.

    The P3 clients already normalize provider failures to `LLMError`; this proxy
    inspects the message and, on a rate-limit, sleeps (honoring a provider-supplied
    retry-after when present, else exponential ``base_delay * 2**attempt``) and
    retries up to ``max_retries``. Non-rate-limit errors propagate immediately.

    After exhausting retries it sets ``gave_up = True`` and re-raises; the runner
    checks that flag per company (the pipeline nodes catch `LLMError` and degrade
    gracefully, so a persistent rate-limit would otherwise silently produce a bad
    result). Call `reset` before each company.
    """

    def __init__(self, inner: LLMClient, *, max_retries: int = 5, base_delay: float = 8.0,
                 max_delay: float = 120.0, sleep=time.sleep) -> None:
        self.inner = inner
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._sleep = sleep
        self.gave_up = False
        self.calls = 0
        self.retries = 0

    def reset(self) -> None:
        """Clear the per-company ``gave_up`` flag before processing a new company."""
        self.gave_up = False

    def _attempt(self, fn):
        attempt = 0
        while True:
            self.calls += 1
            try:
                return fn()
            except LLMError as exc:
                message = str(exc)
                if not _is_rate_limited(message):
                    raise
                if attempt >= self.max_retries:
                    self.gave_up = True
                    raise
                delay = _retry_after_seconds(message)
                if delay is None:
                    delay = self.base_delay * (2 ** attempt)
                logger.warning("rate limited (attempt %d/%d) — backing off %.1fs",
                               attempt + 1, self.max_retries, min(delay, self.max_delay))
                self._sleep(min(delay, self.max_delay))
                self.retries += 1
                attempt += 1

    def complete(self, system: str, user: str, **kwargs) -> str:
        return self._attempt(lambda: self.inner.complete(system, user, **kwargs))

    def complete_json(self, system: str, user: str, **kwargs) -> dict:
        return self._attempt(lambda: self.inner.complete_json(system, user, **kwargs))


# ---------------------------------------------------------------------------
# Dataset + IO helpers.
# ---------------------------------------------------------------------------
def _slug(text: str) -> str:
    """A filesystem-safe slug for a domain or model id."""
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "x"


def load_companies(path: str | Path) -> list[dict]:
    """Load the eval company list. Accepts a bare JSON array or a ``{companies: [...]}``
    wrapper (the wrapper carries the human-verify warning)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    companies = data["companies"] if isinstance(data, dict) else data
    if not isinstance(companies, list):
        raise ValueError(f"{path}: expected a list of companies (or a 'companies' key).")
    return companies


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSON-Lines file into a list of dicts (empty list if it does not exist)."""
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def append_jsonl(path: Path, record: dict) -> None:
    """Append one record as a JSON line, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def dedupe_results(records: list[dict]) -> list[dict]:
    """Collapse to one record per domain, preferring an ``ok`` record over an error.

    A domain can appear multiple times across resumed runs (error, then ok). For
    metrics we keep the ``ok`` record if any, else the last seen.
    """
    by_domain: dict[str, dict] = {}
    for r in records:
        domain = r.get("domain")
        prev = by_domain.get(domain)
        if prev is None or (prev.get("status") != "ok"):
            by_domain[domain] = r
    return list(by_domain.values())


# ---------------------------------------------------------------------------
# Research cache.
# ---------------------------------------------------------------------------
def cache_path(domain: str) -> Path:
    """Path to a domain's cached `ResearchResult` JSON."""
    return CACHE_DIR / f"{_slug(domain)}.json"


def load_cached_research(domain: str) -> ResearchResult | None:
    """Load a domain's cached research, or ``None`` if not cached / unreadable."""
    path = cache_path(domain)
    if not path.exists():
        return None
    try:
        return ResearchResult.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — a corrupt cache entry should not abort
        logger.warning("ignoring unreadable research cache for %s: %s", domain, exc)
        return None


def save_cached_research(research: ResearchResult) -> None:
    """Persist a `ResearchResult` to the per-domain research cache."""
    path = cache_path(research.company.domain)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(research.model_dump_json(indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Evaluate one company.
# ---------------------------------------------------------------------------
def evaluate_one(entry: dict, icp: ICP, llm, search, settings: Settings, *, use_cache: bool = True) -> dict:
    """Run the pipeline for one company and build its result record.

    Research is reused from cache when available (and cached on first computation).
    Qualify → draft → verify always re-run (they are cheap). If the LLM proxy gives
    up on a persistent rate-limit during a phase, returns an ``error`` record so the
    company is retried on the next resumed run rather than recorded with a degraded
    verdict.

    Args:
        entry: A dataset entry (``domain`` / ``category`` / ``label`` / ``rationale``).
        icp: The ICP to qualify against.
        llm: The (retrying) LLM client.
        search: The search client.
        settings: Run settings.
        use_cache: Whether to read/write the research cache.

    Returns:
        A result record dict (``status`` ``"ok"`` or ``"error"``).
    """
    domain = entry["domain"]
    base = {"domain": domain, "category": entry.get("category"), "label": entry.get("label")}

    research = load_cached_research(domain) if use_cache else None
    from_cache = research is not None
    if research is None:
        if hasattr(llm, "reset"):
            llm.reset()
        research = run_research(Company(domain=domain), llm, search, settings)
        if getattr(llm, "gave_up", False):
            return {**base, "status": "error", "error": "rate-limited during research"}
        if use_cache:
            save_cached_research(research)

    if hasattr(llm, "reset"):
        llm.reset()
    qual = run_qualification(research, icp, llm, settings)
    draft = run_draft(research, qual, llm, settings) if qual.qualified else None
    ver = run_verification(draft, research, llm, settings) if draft is not None else None
    if getattr(llm, "gave_up", False):
        return {**base, "status": "error", "error": "rate-limited during qualify/draft/verify"}

    return {
        **base,
        "status": "ok",
        "from_cache": from_cache,
        "predicted_qualified": qual.qualified,
        "score": qual.score,
        "qual_reason": qual.reason,
        "matched_signals": qual.matched_signals,
        "missed_signals": qual.missed_signals,
        "draft_passed": (ver.passed if ver else None),
        "groundedness_score": (ver.groundedness_score if ver else None),
        "faithfulness_score": (ver.faithfulness_score if ver else None),
        "tier_breakdown": (ver.tier_breakdown if ver else {}),
        "claim_verdicts": ([cv.model_dump() for cv in ver.claim_verdicts] if ver else []),
        "flagged_claims": (ver.flagged_claims if ver else []),
        "fact_count": len(research.facts),
        "source_count": research.source_count,
        "errors": research.errors,
    }


def run_eval(companies: list[dict], icp: ICP, *, llm, search, settings: Settings,
             results_file: Path, limit: int | None = None, resume: bool = True) -> list[dict]:
    """Evaluate each company, checkpointing as we go; returns all result records.

    Companies already recorded ``ok`` in ``results_file`` are skipped (resume).
    ``limit`` caps the number of *newly processed* companies this run. Any
    unexpected error for a company is recorded and the run continues.
    """
    existing = read_jsonl(results_file)
    done = {r["domain"] for r in existing if r.get("status") == "ok"} if resume else set()
    processed = 0
    for entry in companies:
        domain = entry["domain"]
        if limit is not None and processed >= limit:
            break
        if domain in done:
            logger.info("skip %s (already evaluated)", domain)
            continue
        try:
            record = evaluate_one(entry, icp, llm, search, settings)
        except Exception as exc:  # noqa: BLE001 — never abort the whole run for one company
            record = {"domain": domain, "category": entry.get("category"),
                      "label": entry.get("label"), "status": "error",
                      "error": f"{type(exc).__name__}: {exc}"}
        append_jsonl(results_file, record)
        processed += 1
        _print_company_line(record)
    return read_jsonl(results_file)


# ---------------------------------------------------------------------------
# Re-run draft + verify only (qualification frozen).
# ---------------------------------------------------------------------------
def _qual_from_record(record: dict) -> QualificationResult:
    """Reconstruct the frozen `QualificationResult` from an existing result record.

    Only ``reason`` actually feeds drafting; the other fields are carried so the
    object faithfully mirrors the recorded verdict. The verdict is never recomputed.
    """
    return QualificationResult(
        qualified=bool(record.get("predicted_qualified")),
        score=float(record.get("score") or 0.0),
        reason=str(record.get("qual_reason") or ""),
        matched_signals=list(record.get("matched_signals") or []),
        missed_signals=list(record.get("missed_signals") or []),
    )


def redraft(records: list[dict], *, llm, settings: Settings) -> list[dict]:
    """Re-run only draft + verify for already-qualified companies; return new records.

    Research is loaded from cache and the qualification verdict is copied verbatim
    from the existing record (never recomputed), so the qualification matrix is
    untouched. Records that were disqualified, errored, or lack cached research are
    passed through unchanged. Each redrafted record's draft/verify fields are replaced
    with the freshly computed values.
    """
    out: list[dict] = []
    for record in records:
        if record.get("status") != "ok" or not record.get("predicted_qualified"):
            out.append(record)
            continue
        domain = record.get("domain")
        research = load_cached_research(domain)
        if research is None:
            logger.warning("redraft: no cached research for %s; leaving record unchanged", domain)
            out.append(record)
            continue
        if hasattr(llm, "reset"):
            llm.reset()
        qual = _qual_from_record(record)
        draft = run_draft(research, qual, llm, settings)
        ver = run_verification(draft, research, llm, settings)
        if getattr(llm, "gave_up", False):
            logger.warning("redraft: rate-limited on %s; leaving record unchanged", domain)
            out.append(record)
            continue
        updated = {
            **record,
            "from_cache": True,
            "hooks_used": list(draft.hooks_used),
            "draft_passed": ver.passed,
            "groundedness_score": ver.groundedness_score,
            "faithfulness_score": ver.faithfulness_score,
            "tier_breakdown": ver.tier_breakdown,
            "claim_verdicts": [cv.model_dump() for cv in ver.claim_verdicts],
            "flagged_claims": ver.flagged_claims,
        }
        out.append(updated)
        _print_company_line(updated)
    return out


# ---------------------------------------------------------------------------
# Independent live re-check (the honest, network-bounded metric).
# ---------------------------------------------------------------------------
def _recheck_cache_path(run_id: str) -> Path:
    return CACHE_DIR / f"recheck_{_slug(run_id)}.json"


def recheck(results: list[dict], *, load_research=load_cached_research,
            fetch=fetch_page, cache: dict | None = None, allow_fetch: bool = True) -> dict:
    """Re-fetch each used claim's source and confirm the evidence still appears.

    For every claim a draft actually used (from ``claim_verdicts``), the evidence
    snippet is pulled from the domain's cached research, the source URL is
    re-fetched, and we check the evidence is still a substring of the live page —
    aggregating live-verifiability **by tier**. Per-source verdicts are cached so a
    re-run does not re-fetch.

    Args:
        results: Per-company result records (``ok`` only are used).
        load_research: ``domain -> ResearchResult | None`` (cached research).
        fetch: ``url -> text`` fetcher.
        cache: Optional mutable verdict cache (``key -> {"present","dead"}``);
            updated in place so the caller can persist it.
        allow_fetch: When ``False``, sources not already in ``cache`` are skipped
            rather than fetched — lets the ``run`` / ``report`` paths reuse a prior
            recheck's cache without any network.

    Returns:
        ``{tier: {"checked", "present", "dead", "rate"}}``.
    """
    cache = cache if cache is not None else {}
    tiers: dict[str, dict[str, int]] = {}

    def _bump(tier: str, present: bool, dead: bool) -> None:
        bucket = tiers.setdefault(tier, {"checked": 0, "present": 0, "dead": 0})
        bucket["checked"] += 1
        bucket["present"] += int(present)
        bucket["dead"] += int(dead)

    research_by_domain: dict[str, ResearchResult | None] = {}
    for record in metrics_mod.ok_results(results):
        domain = record.get("domain")
        if domain not in research_by_domain:
            research_by_domain[domain] = load_research(domain)
        research = research_by_domain[domain]
        if research is None:
            continue
        fact_by_key = {(_normalize(f.claim), f.source_url): f for f in research.facts}

        for cv in record.get("claim_verdicts", []):
            fact_used, url, tier = cv.get("fact_used"), cv.get("source_url"), cv.get("tier")
            if not fact_used or not url:
                continue
            fact = fact_by_key.get((_normalize(fact_used), url))
            evidence = fact.evidence if fact else ""
            if not evidence:
                continue
            key = f"{url}||{_normalize(evidence)[:80]}"
            if key not in cache:
                if not allow_fetch:
                    continue  # cache-only (no network): skip sources not yet checked
                text = fetch(url)
                cache[key] = {"present": bool(text) and _normalize(evidence) in _normalize(text),
                              "dead": not bool(text)}
            verdict = cache[key]
            _bump(tier or "unknown", verdict["present"], verdict["dead"])

    return {
        tier: {**counts, "rate": round(counts["present"] / counts["checked"], 4) if counts["checked"] else 0.0}
        for tier, counts in sorted(tiers.items())
    }


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------
def _print_company_line(record: dict) -> None:
    if record.get("status") != "ok":
        print(f"  {record['domain']:20s} ERROR: {record.get('error')}")
        return
    pq = "QUAL" if record.get("predicted_qualified") else "DISQ"
    dp = record.get("draft_passed")
    draft = "—" if dp is None else ("PASS" if dp else "FAIL")
    print(f"  {record['domain']:20s} {pq} score={record.get('score'):.2f} "
          f"draft={draft} g={record.get('groundedness_score')} "
          f"f={record.get('faithfulness_score')} facts={record.get('fact_count')}"
          f"{' [cache]' if record.get('from_cache') else ''}")


def print_summary(agg: dict) -> None:
    """Print the aggregate metrics to the console."""
    q = agg["qualification"]
    d = agg["draft_gate"]
    s = agg["scores"]
    print("\n" + "=" * 60)
    print(f"EVAL SUMMARY  ({agg['n_ok']}/{agg['n_total']} evaluated, {agg['n_error']} errors)")
    print("=" * 60)
    print(f"  Qualification: acc={q['accuracy']} precision={q['precision']} "
          f"recall={q['recall']} f1={q['f1']}  (tp={q['tp']} fp={q['fp']} tn={q['tn']} fn={q['fn']})")
    print(f"  Draft gate pass-rate: {d['passed']}/{d['attempted']} = {d['pass_rate']}")
    print(f"  Mean groundedness={s['mean_groundedness']}  mean faithfulness={s['mean_faithfulness']}  "
          f"(over {s['n_drafted']} drafts)")
    print(f"  Failure modes: {agg['failure_modes']}")
    print(f"  Facts/company by category: {agg['facts_by_category']}")
    if agg["live_reverifiability"]:
        print(f"  Live re-verifiability by tier: "
              + ", ".join(f"{t}={v['rate']} ({v['present']}/{v['checked']})"
                          for t, v in agg["live_reverifiability"].items()))
    else:
        print("  Live re-verifiability by tier: (run `recheck` to compute)")


def _md_table(rows: list[list[str]], header: list[str]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(cells) + " |" for cells in rows]
    return out


def build_report(results: list[dict], agg: dict, model_label: str, run_date: str) -> str:
    """Render the full eval report (per-company table + aggregates) as Markdown."""
    q, d, s = agg["qualification"], agg["draft_gate"], agg["scores"]
    L: list[str] = []
    L.append(f"> **Run date:** {run_date} · **Model:** `{model_label}` · "
             f"**Source:** `evals/run_eval.py`")
    L.append("")
    L.append(f"# pitch-pilot eval — {run_date}")
    L.append("")
    L.append("> **Labels are human-proposed** (see `examples/eval_companies.json` and the "
             "rubric in `docs/evals.md`). Verify before trusting these numbers.")
    L.append("")
    L.append("## Aggregates")
    L.append("")
    L.append(f"- **Companies:** {agg['n_ok']} evaluated, {agg['n_error']} error(s) of {agg['n_total']}")
    L.append(f"- **Qualification:** accuracy {q['accuracy']}, precision {q['precision']}, "
             f"recall {q['recall']}, F1 {q['f1']} (tp={q['tp']}, fp={q['fp']}, tn={q['tn']}, fn={q['fn']})")
    L.append(f"- **Draft gate pass-rate:** {d['passed']}/{d['attempted']} = {d['pass_rate']}")
    L.append(f"- **Mean groundedness:** {s['mean_groundedness']} · **mean faithfulness:** "
             f"{s['mean_faithfulness']} (over {s['n_drafted']} drafts)")
    L.append(f"- **Failure modes:** {agg['failure_modes']}")
    L.append(f"- **Facts/company by category (degradation):** {agg['facts_by_category']}")
    if agg["live_reverifiability"]:
        L.append("- **Live re-verifiability by tier:** "
                 + ", ".join(f"{t} {v['rate']} ({v['present']}/{v['checked']})"
                             for t, v in agg["live_reverifiability"].items()))
    L.append("")
    L.append("## Per-company")
    L.append("")
    rows = []
    for r in dedupe_results(results):
        if r.get("status") != "ok":
            rows.append([f"`{r.get('domain')}`", r.get("category", ""), r.get("label", ""),
                         "**ERROR**", r.get("error", ""), "", "", "", ""])
            continue
        dp = r.get("draft_passed")
        rows.append([
            f"`{r.get('domain')}`", r.get("category", ""), r.get("label", ""),
            "qualified" if r.get("predicted_qualified") else "not_qualified",
            f"{r.get('score')}",
            "—" if dp is None else ("pass" if dp else "fail"),
            f"{r.get('groundedness_score')}", f"{r.get('faithfulness_score')}",
            f"{r.get('fact_count')}",
        ])
    L += _md_table(rows, ["domain", "category", "label (truth)", "predicted",
                          "score", "draft", "grounded", "faithful", "facts"])
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------
def _model_label(settings: Settings) -> str:
    return f"{settings.llm_provider}/{settings.active_model}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pitch-pilot eval harness.")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "redraft", "recheck", "report"],
                        help="run (default), redraft (re-run draft+verify only, "
                             "qualification frozen), recheck (live re-verify), or "
                             "report (recompute).")
    parser.add_argument("--limit", type=int, default=None, help="Max NEW companies to process this run.")
    parser.add_argument("--resume", action="store_true", default=True,
                        help="Skip companies already evaluated (default).")
    parser.add_argument("--no-resume", dest="resume", action="store_false",
                        help="Re-evaluate everything, ignoring existing results.")
    parser.add_argument("--icp", default=DEFAULT_ICP, help=f"ICP JSON (default {DEFAULT_ICP}).")
    parser.add_argument("--companies", default=DEFAULT_COMPANIES,
                        help=f"Companies JSON (default {DEFAULT_COMPANIES}).")
    parser.add_argument("--run-id", default=None, help="Results/report id (default: model slug).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    try:
        settings = get_settings()
    except ConfigError as exc:
        print(f"[FAIL] Config: {exc}")
        return 1

    run_id = args.run_id or _slug(_model_label(settings))
    results_file = RESULTS_DIR / f"{run_id}.jsonl"
    model_label = _model_label(settings)
    run_date = datetime.date.today().isoformat()

    if args.command == "redraft":
        records = dedupe_results(read_jsonl(results_file))
        if not records:
            print(f"No results at {results_file}. Run the eval first.")
            return 1
        max_retries = int(os.environ.get("EVAL_MAX_RETRIES", "8"))
        base_delay = float(os.environ.get("EVAL_BASE_DELAY", "10"))
        llm = RetryingLLM(get_llm_client(settings), max_retries=max_retries, base_delay=base_delay)
        print(f"Redraft run '{run_id}' on {model_label} — re-running draft+verify for "
              f"qualified companies (qualification frozen).\n")
        updated = redraft(records, llm=llm, settings=settings)
        results_file.parent.mkdir(parents=True, exist_ok=True)
        with results_file.open("w", encoding="utf-8") as fh:
            for record in updated:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        n_qual = sum(1 for r in updated if r.get("status") == "ok" and r.get("predicted_qualified"))
        print(f"\nRewrote {results_file} ({n_qual} qualified companies redrafted).")
        print("Run `python -m evals.run_eval recheck` then `report` for the new numbers.")
        return 0

    if args.command == "recheck":
        results = read_jsonl(results_file)
        if not results:
            print(f"No results at {results_file}. Run the eval first.")
            return 1
        cache_file = _recheck_cache_path(run_id)
        cache = {}
        if cache_file.exists():
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        print(f"Re-checking used claim sources for run '{run_id}' ...")
        tiers = recheck(results, cache=cache)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        for tier, v in tiers.items():
            print(f"  {tier:20s} {v['present']}/{v['checked']} present "
                  f"(rate={v['rate']}, dead={v['dead']})")
        return 0

    if args.command == "report":
        results = read_jsonl(results_file)
        recheck_cache = _recheck_cache_path(run_id)
        live = recheck(results, cache=json.loads(recheck_cache.read_text(encoding="utf-8")),
                       allow_fetch=False) if recheck_cache.exists() else None
        agg = metrics_mod.aggregate(dedupe_results(results), recheck=live)
        report = build_report(results, agg, model_label, run_date)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        report_path = REPORTS_DIR / f"eval-{run_date}.md"
        report_path.write_text(report, encoding="utf-8")
        print_summary(agg)
        print(f"\nReport written to {report_path}")
        return 0

    # --- run ---
    icp = load_icp(args.icp)
    companies = load_companies(args.companies)
    # Patient retry budget: a single call grinds through per-minute TPM limits
    # (which reset every ~60s) instead of giving up. Tunable via env for slower tiers.
    max_retries = int(os.environ.get("EVAL_MAX_RETRIES", "8"))
    base_delay = float(os.environ.get("EVAL_BASE_DELAY", "10"))
    llm = RetryingLLM(get_llm_client(settings), max_retries=max_retries, base_delay=base_delay)
    search = get_search_client(settings)
    print(f"Eval run '{run_id}' on {model_label} — {len(companies)} companies "
          f"(limit={args.limit}, resume={args.resume})\n")
    print("!! Labels are human-proposed — verify before trusting metrics (see docs/evals.md).\n")

    results = run_eval(companies, icp, llm=llm, search=search, settings=settings,
                       results_file=results_file, limit=args.limit, resume=args.resume)

    recheck_cache = _recheck_cache_path(run_id)
    live = recheck(results, cache=json.loads(recheck_cache.read_text(encoding="utf-8")),
                   allow_fetch=False) if recheck_cache.exists() else None
    agg = metrics_mod.aggregate(dedupe_results(results), recheck=live)
    report = build_report(results, agg, model_label, run_date)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"eval-{run_date}.md"
    report_path.write_text(report, encoding="utf-8")
    print_summary(agg)
    print(f"\nResults: {results_file}\nReport:  {report_path}")
    print("Run `python -m evals.run_eval recheck` for live re-verifiability by tier.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
