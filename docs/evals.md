# Evaluation

> **Last updated:** 2026-06-14 · **Source files:** `evals/`, `examples/eval_icp.json`, `examples/eval_companies.json`

An agent's quality should be *measured, not vibed*. This page defines the eval
dataset, the **labeling rubric** that makes the ground truth defensible, the
metrics, and the latest numbers (with the model that produced them).

!!! warning "Labels are human-proposed"
    The labels in `examples/eval_companies.json` are a **proposed** starting point.
    A human must verify each one against the rubric below before any metric computed
    from the set is trustworthy. Several entries are flagged `[VERIFY]`.

## Dataset

Two files under `examples/`:

- **`eval_icp.json`** — a hypothetical fintech ICP: industries fintech / payments /
  lending / neobank / crypto exchange; size 50–1000; regions US / EU / UK / India;
  positive signals (payments at scale, recent growth funding, hiring
  risk/fraud/security, regulatory exposure); negative signals (not fintech / no
  money movement, pre-revenue or <20 employees, large incumbent bank building fraud
  in-house).
- **`eval_companies.json`** — `{domain, category, label, rationale}` entries across
  three categories, deliberately including **negatives** and **sparse** companies
  (not just easy positives):
    - **good_fit → qualified:** ramp, brex, mercury, checkout, plaid, razorpay,
      monzo, wise `[VERIFY size]`.
    - **bad_fit → not_qualified:** notion, figma, linear, vercel, huggingface,
      jpmorganchase (incumbent-bank negative signal).
    - **sparse:** nilenso → not_qualified; fampay, jupiter.money → qualified
      (sparse good-fit edge cases) `[VERIFY thin]`.

## Labeling rubric

A human assigns the ground-truth `label` by reading the company's own site plus a
quick search, then applying these rules **in order**:

1. **Negative-signal veto → `not_qualified`.** If the company matches any ICP
   negative signal — not fintech / no money movement, pre-revenue or <20 employees,
   or a large incumbent bank that builds fraud in-house — label `not_qualified`,
   regardless of anything else.
2. **Industry gate.** Otherwise it must be in a target industry (it moves money:
   payments, lending, neobank, cards, crypto exchange, or fintech infrastructure).
   If not, `not_qualified`.
3. **Size band.** Headcount should plausibly fall in 50–1000. Materially outside
   (e.g. a 5,000-person company) is a `[VERIFY]` edge case; record the call and the
   rationale rather than silently passing it.
4. **Positive signals.** At least one should plausibly hold (payments at scale,
   recent growth funding, hiring risk/fraud/security roles, regulatory exposure).
5. **Otherwise → `qualified`.** Record a one-line `rationale` for every label so the
   ground truth is auditable.

The `category` (`good_fit` / `bad_fit` / `sparse`) is orthogonal to `label` — it
records *why the company is in the set* (and drives the degradation metric), while
`label` is the qualification ground truth.

## Methodology

The harness (`evals/run_eval.py`) is built for free-tier reality:

- **Cache** — research (the expensive ~22-of-~30 LLM calls) is cached per domain in
  `evals/cache/`; it is never recomputed.
- **Checkpoint + resume** — each company's result is appended to
  `evals/results/<run_id>.jsonl` as it finishes; a re-run skips companies already
  recorded `ok`, so a run survives across sessions/days (free tiers reset daily).
- **Backoff** — rate-limit / quota errors are retried with exponential backoff
  (honoring a provider-supplied retry-after); a company that stays rate-limited is
  recorded as an error and retried next run, never aborting the whole run.

Qualify → draft → verify are cheap and re-run each eval. The **`redraft`** command
re-runs *only* draft + verify for the already-qualified companies, reusing cached
research **and** each record's frozen qualification verdict — so the draft/verify
logic can change without paying for research or perturbing the qualification matrix.
See [ADR-0011](decisions.md), [ADR-0014](decisions.md), and the
[eval harness component](components/evals.md).

### Metrics

| Metric | Definition |
| --- | --- |
| **Qualification accuracy / precision / recall / F1** | Predicted `qualified` vs the verified `label` (positive class = `qualified`). |
| **Draft gate pass-rate** | Of leads the pipeline qualified, the share producing a draft that passes the verify gate. |
| **Mean groundedness** | Mean `groundedness_score` over drafted companies. Under the 0.8.0 faithfulness-based definition (see [Groundedness](groundedness.md)) this is *verified body claims / total body claims* — `faithful` body claims (plus `overreach` when not strict) over every factual claim the body makes about the company. |
| **Mean faithfulness** | Mean `faithfulness_score` — body claims judged `faithful` / total body claims. |
| **Failure modes** | Counts of verify failures by reason (`unsupported` / `overreach` / `structural` / `judge-error`) — proves the gate *catches*, not just passes. |
| **Live re-verifiability by tier** | From the separate `recheck` command: re-fetch each used claim's source and confirm the evidence still appears, reported **by source tier**. The honest, network-bounded metric ([ADR-0010](decisions.md)). |
| **Degradation** | Mean facts/company for `sparse` vs `good_fit`. |

## How to run

```powershell
python -m evals.run_eval --limit 3       # evaluate (cached, checkpointed, resumable)
python -m evals.run_eval redraft         # re-run draft+verify only (qualification frozen)
python -m evals.run_eval recheck         # live re-verifiability by tier (network-bounded)
python -m evals.run_eval report          # recompute metrics + rewrite the report
```

Use a capable model — the 8B model malforms the structured JSON. The headline run
uses `LLM_PROVIDER=cerebras` with `CEREBRAS_MODEL=gpt-oss-120b` (its ~1M tokens/day
free tier runs the whole set in one session); `GROQ_MODEL=llama-3.3-70b-versatile`
or Gemini also work. Each run writes a dated report to `evals/reports/eval-<date>.md`.

## Current numbers

Full run over all **17 companies** (good_fit, bad_fit, and sparse — positives,
negatives, and thin cases) on **`cerebras/gpt-oss-120b`**, **2026-06-14**. The
draft + verify numbers are from the 0.8.0 structural fix (draft grounding =
fact-selection; verify = body-faithfulness judge — see
[ADR-0014](decisions.md) and [Groundedness](groundedness.md)). The qualification
matrix is unchanged from the original run; only draft + verify were re-run (via
`redraft`), so qualification is frozen and the draft/verify numbers are real and
comparable.

| Metric | Target | Latest | Model | As of |
| --- | --- | --- | --- | --- |
| Qualification F1 | ≥ 0.80 | **0.870** (precision 0.769, recall 1.0, accuracy 0.824) | cerebras/gpt-oss-120b | 2026-06-14 |
| Draft gate pass-rate | report-only | **0.846 (11/13)** | cerebras/gpt-oss-120b | 2026-06-14 |
| Mean groundedness | ≥ 0.90 | **0.936** | cerebras/gpt-oss-120b | 2026-06-14 |
| Mean faithfulness | ≥ 0.90 | **0.936** | cerebras/gpt-oss-120b | 2026-06-14 |
| Failure modes (gate caught) | report-only | `unsupported: 1`, `overreach: 2` | cerebras/gpt-oss-120b | 2026-06-14 |
| Live re-verifiability (own_site) | report-only | **0.90 (45/50)** | cerebras/gpt-oss-120b | 2026-06-14 |
| Facts/company (good_fit / bad_fit / sparse) | report-only | 61.75 / 54.5 / 50.33 | cerebras/gpt-oss-120b | 2026-06-14 |

**Qualification confusion matrix** (n=17): TP=10, FP=3, TN=4, FN=0 →
precision 10/13 = 0.769, recall 10/10 = 1.0, F1 0.870, accuracy 14/17 = 0.824.

**What the draft gate caught.** Of 13 qualified companies, 11 produced a passing
draft. The 2 failures are both qualification **false positives**, and the gate
flagged them for the right reason:

- `linear.app` — groundedness 0.667; one body claim flagged `overreach`
  ("Linear has distributed, remote-first teams").
- `vercel.com` — groundedness 0.5; one `overreach` and one `unsupported`
  ("strong foundation for fast, secure AI-native apps").

Every grounding fact used across all drafts was `own_site` tier (Policy B holds),
and 90% (45/50) of those sources still carried their evidence verbatim on live
re-fetch. The full dated report is at `evals/reports/eval-2026-06-14.md`.

### Known limitations

These are honest gaps, documented rather than tuned away:

- **Three qualification false positives** (`linear.app`, `vercel.com`,
  `nilenso.com`), all with **F1 unchanged at 0.870**:
    - `linear.app` and `vercel.com` — the qualifier scored `industry=unknown` (it
      could not confirm fintech/money-movement from the research) and that
      *unknown* was dropped from the fit score rather than penalized, so a single
      matched positive signal ("recently raised growth funding") pushed each over
      the 0.5 threshold (score 0.53). Both are dev-tools companies that should be
      `not_qualified`.
    - `nilenso.com` — the model **mis-assessed** industry as `match` for a
      software consultancy (score 0.67), so it qualified despite being out of ICP.
- **The industry-gating fix is FUTURE WORK, deliberately not applied here.** The
  obvious fix — treat `industry=unknown` (and tighten the industry assessment) as
  disqualifying rather than score-neutral — would likely clear `linear`/`vercel`.
  But tuning the scorer against the same 17-company set and then reporting the
  improved number on that set would not be defensible (it would be fitting to the
  test set). The fix belongs with a held-out set, in a later phase.
- **Labels are human-proposed.** The ground-truth labels still need a human pass
  against the rubric before the qualification numbers are fully trustworthy.

> When the qualification logic changes (against a held-out set) or a new model is
> used, re-run, then replace this table and bump its **As of** date and **Model**
> column (documentation protocol, rule 7).
