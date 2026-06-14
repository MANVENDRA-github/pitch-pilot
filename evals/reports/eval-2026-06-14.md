> **Run date:** 2026-06-14 · **Model:** `cerebras/gpt-oss-120b` · **Source:** `evals/run_eval.py`

# pitch-pilot eval — 2026-06-14

> **Labels are human-proposed** (see `examples/eval_companies.json` and the rubric in `docs/evals.md`). Verify before trusting these numbers.

## Aggregates

- **Companies:** 8 evaluated, 0 error(s) of 8
- **Qualification:** accuracy 1.0, precision 1.0, recall 1.0, F1 1.0 (tp=4, fp=0, tn=4, fn=0)
- **Draft gate pass-rate:** 3/4 = 0.75
- **Mean groundedness:** 0.9583 · **mean faithfulness:** 0.9583 (over 4 drafts)
- **Failure modes:** {'unsupported': 0, 'overreach': 1, 'structural': 0, 'judge-error': 0}
- **Facts/company by category (degradation):** {'bad_fit': 56.67, 'borderline': 56.5, 'good_fit': 55.67}
- **Live re-verifiability by tier:** own_site 0.4 (8/20)

## Per-company

| domain | category | label (truth) | predicted | score | draft | grounded | faithful | facts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `stripe.com` | good_fit | qualified | qualified | 0.8333 | pass | 1.0 | 1.0 | 57 |
| `gocardless.com` | good_fit | qualified | qualified | 0.8333 | pass | 1.0 | 1.0 | 61 |
| `chime.com` | good_fit | qualified | qualified | 0.75 | fail | 0.8333 | 0.8333 | 49 |
| `canva.com` | bad_fit | not_qualified | not_qualified | 0.1042 | — | None | None | 54 |
| `datadoghq.com` | bad_fit | not_qualified | not_qualified | 0.2 | — | None | None | 57 |
| `wellsfargo.com` | bad_fit | not_qualified | not_qualified | 0.3667 | — | None | None | 59 |
| `coinbase.com` | borderline | qualified | qualified | 0.6875 | pass | 1.0 | 1.0 | 64 |
| `shopify.com` | borderline | not_qualified | not_qualified | 0.3667 | — | None | None | 49 |
