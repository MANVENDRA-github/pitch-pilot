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
- **`eval_companies_holdout.json`** — a **held-out** validation set of 8 companies
  **not** in the file above (stripe, gocardless, chime, canva, datadoghq, wellsfargo,
  coinbase, shopify): clear good-fit, clear bad-fit (non-fintech + an incumbent bank),
  and two borderline. It is the clean test of whether a qualifier change generalizes
  rather than overfitting the development 17.

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

**Reproducibility.** The three gate-critical LLM calls — qualify, draft, and the
verify faithfulness judge — run at **temperature 0** so the headline numbers are a
reproducible run, not a lucky sample. (Cerebras is not bit-deterministic even at
temperature 0, so a small residual run-to-run variance remains; the table below is
one canonical run, reported as such.)

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

# Held-out validation (live research for the 8 unseen companies):
python -m evals.run_eval run --companies examples/eval_companies_holdout.json --run-id holdout
python -m evals.run_eval recheck --run-id holdout
python -m evals.run_eval report  --run-id holdout
```

Use a capable model — the 8B model malforms the structured JSON. The headline run
uses `LLM_PROVIDER=cerebras` with `CEREBRAS_MODEL=gpt-oss-120b` (its ~1M tokens/day
free tier runs the whole set in one session); `GROQ_MODEL=llama-3.3-70b-versatile`
or Gemini also work. Each run writes a dated report to `evals/reports/eval-<date>.md`.

## Current numbers

All runs `cerebras/gpt-oss-120b`, gate-critical calls (qualify, draft, verify judge)
at **temperature 0**, **2026-06-14**. The qualifier fix (reliable negative-signal
veto + industry as a required component — [ADR-0015](decisions.md)) was developed
against the original 17 and then validated on a **held-out** set it never saw.

### Headline — held-out validation (n=8, clean test of generalization)

Eight **new** companies (not in the original 17): clear good-fit fintechs, clear
bad-fit (non-fintech + an incumbent bank), and two borderline. See
`examples/eval_companies_holdout.json`.

| Metric | Result | Model | As of |
| --- | --- | --- | --- |
| **Qualification F1** | **1.0** (precision 1.0, recall 1.0, accuracy 1.0; TP=4, FP=0, TN=4, FN=0) | cerebras/gpt-oss-120b | 2026-06-14 |
| Draft gate pass-rate | 0.75 (3/4) | cerebras/gpt-oss-120b | 2026-06-14 |
| Mean groundedness / faithfulness | 0.9583 (over 4 drafts) | cerebras/gpt-oss-120b | 2026-06-14 |
| Live re-verifiability (own_site) | 0.40 (8/20) — see caveat | cerebras/gpt-oss-120b | 2026-06-14 |

Every held-out company landed correctly: `stripe` / `gocardless` / `chime` qualified;
`canva` / `datadoghq` (non-fintech) and `wellsfargo` (incumbent bank) disqualified;
and both borderlines went the labeled way — `coinbase` (crypto exchange, in-ICP)
qualified at 0.69, `shopify` (commerce, not fintech) disqualified at 0.37. The veto
and industry rule generalize to companies the fix was never tuned on.

> **Held-out re-verifiability caveat.** The 0.40 (8/20) looks alarming but is a
> **fetch-access** artifact, not fabrication: 10 of the 12 misses are `dead` — the
> source returned HTTP 403 (bot-blocking) on re-fetch (chime, canva, coinbase block
> automated fetchers), so the evidence couldn't be re-checked at all. Only 2 were
> genuinely absent. The original-17 sources (well-known, fetchable) give the cleaner
> durability signal of **0.9231 (36/39)** below.

### Fix evidence — original 17, before vs. after (development set)

Same set, same temperature-0 config; only the qualifier changed.

| Metric (n=17) | Before fix | After fix |
| --- | --- | --- |
| **Qualification F1** | **0.769** | **0.9474** |
| precision / recall / accuracy | 0.625 / 1.0 / 0.647 | 1.0 / 0.9 / 0.9412 |
| Confusion TP / FP / TN / FN | 10 / 6 / 1 / 0 | 9 / 0 / 7 / 1 |
| Draft gate pass-rate | 0.5625 (9/16) | 0.7778 (7/9) |
| Mean groundedness / faithfulness | 0.8615 | 0.95 |
| Live re-verifiability (own_site) | 0.9032 (56/62) | 0.9231 (36/39) |

The fix eliminated **all six** false positives (precision 0.625 → 1.0): the
non-fintech companies (`figma`, `huggingface`, `linear`, `vercel`, `nilenso`) and the
incumbent bank (`jpmorganchase`) now disqualify via a reliably-firing "not fintech" /
"incumbent bank" veto plus the industry penalty.

**Both sets improved (original 0.769 → 0.947, held-out 1.0), so the fix generalizes
rather than overfitting the 17.**

**The recall cost, stated honestly.** The after-fix original-17 has **one false
negative — `ramp.com`** (recall 1.0 → 0.9). On that run the assessor flakily returned
`industry=unknown` for Ramp (a clear fintech), and because industry is now a required
component, the unknown scored 0.0 and dropped Ramp below threshold (0.28). This is the
deliberate trade-off: making `industry=unknown` count against a company fixes the
non-fintech false positives but, when the assessor *flakily* fails to confirm a real
fintech's industry, it can wrongly reject it. The held-out set did not exhibit this
(all four good-fits, including borderline `coinbase`, qualified), but it is a real
residual risk of the assessor's run-to-run variance.

**Draft-gate overreach — a noted finding (not fixed this pass).** Across runs the gate
rejects a meaningful share of *qualified* drafts (≈44% on the pre-fix 16, ≈22–25%
after). Spot-checking the flagged claims (e.g. "single-point solution for compliance",
"strong foundation for accelerating product delivery", AI-capability embellishments),
these are genuine over-claims the **drafter** adds beyond the cited facts — the gate is
correctly catching drafter overreach, not being pedantic. Tightening the draft prompt
to claim less is the natural next step.

**On the two grounding scores.** Under the default strict gate, `groundedness_score`
and `faithfulness_score` share the same numerator (faithful body claims), so they are
**numerically identical** and reported as **one** signal — not two independent pieces
of evidence. They diverge only with `FAITHFULNESS_STRICT=false`.

Reports: held-out at `evals/reports/eval-2026-06-14-holdout.md`; original-17 after-fix
at `evals/reports/eval-2026-06-14-original17-after.md`.

### Known limitations

These are honest gaps, documented rather than tuned away:

- **Small samples.** Held-out n=8, development n=17, human-proposed labels, a single
  run each. F1 1.0 on eight companies is encouraging, not conclusive — a larger
  held-out set is needed before the headline is bankable.
- **Residual run-to-run variance.** Cerebras is not bit-deterministic even at
  temperature 0, so a company can vary between runs — including the `ramp.com` false
  negative above (flaky `industry=unknown` on a real fintech) and draft gate outcomes.
- **One residual qualification miss type.** The industry rule depends on the assessor
  confirming industry; when it flakily returns `unknown` for a genuine fintech, the
  company is wrongly rejected. A confidence/retry on the industry assessment, or a
  softer penalty, is a candidate follow-up — to be validated on a held-out set.
- **Labels are human-proposed.** The ground-truth labels (both sets) still need a
  human pass against the rubric before the numbers are fully trustworthy.

> When the qualification logic changes or a new model is used, re-run on **both** the
> development and held-out sets, then replace these tables and bump the **As of** date
> and **Model** (documentation protocol, rule 7).
