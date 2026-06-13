# Evaluation

> **Last updated:** 2026-06-13 · **Source files:** `evals/`, `examples/eval_icp.json`, `examples/eval_companies.json`

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

Qualify → draft → verify are cheap and re-run each eval. See
[ADR-0011](decisions.md) and the [eval harness component](components/evals.md).

### Metrics

| Metric | Definition |
| --- | --- |
| **Qualification accuracy / precision / recall / F1** | Predicted `qualified` vs the verified `label` (positive class = `qualified`). |
| **Draft gate pass-rate** | Of leads the pipeline qualified, the share producing a draft that passes the verify gate. |
| **Mean groundedness** | Mean `groundedness_score` over drafted companies (verified claims / total). |
| **Mean faithfulness** | Mean `faithfulness_score` (claims judged `faithful` / total). |
| **Failure modes** | Counts of verify failures by reason (`unbacked` / `volatile-source` / `not-substring` / `overreach` / `unsupported`) — proves the gate *catches*, not just passes. |
| **Live re-verifiability by tier** | From the separate `recheck` command: re-fetch each used claim's source and confirm the evidence still appears, reported **by source tier**. The honest, network-bounded metric ([ADR-0010](decisions.md)). |
| **Degradation** | Mean facts/company for `sparse` vs `good_fit`. |

## How to run

```powershell
python -m evals.run_eval --limit 3       # evaluate (cached, checkpointed, resumable)
python -m evals.run_eval recheck         # live re-verifiability by tier (network-bounded)
python -m evals.run_eval report          # recompute metrics + rewrite the report
```

Use a capable model — the 8B model malforms the structured JSON. Set
`LLM_PROVIDER=groq` with `GROQ_MODEL=llama-3.3-70b-versatile`, or use Gemini. Each
run writes a dated report to `evals/reports/eval-<date>.md`.

## Current numbers

!!! warning "Partial, positives-only baseline — not the headline"
    The numbers below are from a **partial smoke run of 4 `good_fit` companies**
    (ramp, brex, mercury, checkout) on `groq/llama-3.3-70b-versatile`, with
    **human-unverified labels**. Because the slice contains only positives,
    qualification precision/recall/F1 are trivially 1.0 and are **not** a meaningful
    headline yet — a real baseline needs the negatives and sparse companies (and
    verified labels). The draft-gate, groundedness, faithfulness, failure-mode, and
    live-re-verifiability figures are already informative.

| Metric | Target | Latest (partial) | Model | As of |
| --- | --- | --- | --- | --- |
| Qualification F1 | ≥ 0.80 | 1.0 _(positives-only; not meaningful yet)_ | groq/llama-3.3-70b-versatile | 2026-06-13 |
| Draft gate pass-rate | report-only | 0.75 (3/4) | groq/llama-3.3-70b-versatile | 2026-06-13 |
| Mean groundedness | ≥ 0.90 | 0.94 | groq/llama-3.3-70b-versatile | 2026-06-13 |
| Mean faithfulness | ≥ 0.90 | 0.94 | groq/llama-3.3-70b-versatile | 2026-06-13 |
| Failure modes (gate caught) | report-only | `overreach: 1` | groq/llama-3.3-70b-versatile | 2026-06-13 |
| Live re-verifiability (own_site) | report-only | 1.0 (15/15) | groq/llama-3.3-70b-versatile | 2026-06-13 |
| Facts/company (good_fit) | report-only | 62.25 | groq/llama-3.3-70b-versatile | 2026-06-13 |

The full dated report is at `evals/reports/eval-2026-06-13.md`. Notably the gate
**caught an `overreach` claim** in one draft (checkout) and routed it to review —
evidence that verification catches, not just passes.

> When a full, human-verified run completes, replace this table, update its **As
> of** date and the **Model** column (documentation protocol, rule 7).
