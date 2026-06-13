# Eval Harness

> **Last updated:** 2026-06-14 · **Source files:** `evals/run_eval.py`, `evals/metrics.py`

The eval harness produces the headline numbers in [Evaluation](../evals.md). It
lives outside the package (`evals/`, not `src/`) because it is tooling, not library
API — so it is not in the auto-generated API reference; this page is its reference.
It is designed around free-tier rate limits: **cache, checkpoint, back off, resume.**

## `run_eval.py` — the runner

Four commands via `python -m evals.run_eval <command>`:

| Command | What it does | Network |
| --- | --- | --- |
| `run` (default) | Evaluate each company: research (cached) → qualify → draft → verify; checkpoint each result; write a report; print aggregates. | LLM + search |
| `redraft` | Re-run **only** draft + verify for already-qualified companies, reusing cached research **and** each record's frozen qualification verdict; rewrite the results file in place. Lets the draft/verify logic change without re-researching or perturbing the qualification matrix. | LLM only |
| `recheck` | Re-fetch each used claim's source and confirm the evidence still appears → live-verifiability by tier. | HTTP fetch only |
| `report` | Recompute metrics from existing results (+ recheck cache) and rewrite the report. | none |

Flags: `--limit N` (max **new** companies this run), `--resume` / `--no-resume`,
`--icp PATH`, `--companies PATH`, `--run-id ID` (defaults to the model slug, so
different models keep separate result files).

### Resilience design

- **Research cache** (`evals/cache/<domain>.json`). Research is ~22 of ~30 LLM
  calls; it is serialized (`ResearchResult.model_dump_json`) on first compute and
  reused forever. `load_cached_research` / `save_cached_research`.
- **Checkpoint + resume** (`evals/results/<run_id>.jsonl`). Each company's record is
  appended as it finishes. On re-run, domains already recorded `ok` are skipped, so
  a run resumes across sessions/days. Results are deduped per domain (an `ok` record
  wins over an earlier `error`) for metrics.
- **Backoff** (`RetryingLLM`). Wraps the `LLMClient`; on a rate-limit `LLMError` it
  sleeps (honoring a provider `retry-after` when present, else exponential
  `base_delay · 2^attempt`) and retries. After exhausting retries it sets
  `gave_up` and re-raises. Because the pipeline nodes catch `LLMError` and *degrade
  gracefully*, the runner checks `gave_up` per company and records an **error**
  (retried next run) instead of silently checkpointing a degraded verdict.

Per company, the runner records: predicted qualified + score, draft pass/fail,
`groundedness_score`, `faithfulness_score`, `claim_verdicts`, `tier_breakdown`,
fact count, and any errors.

### Independent live re-check

`recheck` is the honest, network-bounded metric kept off the per-run hot path
([ADR-0010](../decisions.md)). For each claim a draft actually used, it pulls the
evidence from the cached research, re-fetches the `source_url`, and confirms the
evidence is still a substring of the live page — aggregating **by source tier**.
Per-source verdicts are cached so a re-run does not re-fetch.

## `metrics.py` — the numbers

Pure functions over the result records (no IO): `qualification_metrics`
(accuracy / precision / recall / F1 for the `qualified` class), `draft_pass_rate`,
`mean_scores`, `failure_modes` (counts by verify failure reason), `facts_by_category`
(the degradation metric), and `aggregate` (everything, plus the recheck block).
Records with `status == "error"` are excluded from every metric.

## Output

Each `run`/`report` writes `evals/reports/eval-<date>.md` (per-company table +
aggregates) and prints a console summary; the dated headline numbers are copied into
[Evaluation](../evals.md). `evals/cache/` and `evals/results/` are git-ignored
runtime artifacts; the dated reports are kept.
