# evals (placeholder)

Offline evaluation harness for pitch-pilot. Empty in P0 — scaffolded here so the
seam exists from day one.

Planned metrics:

- **Groundedness rate** — fraction of generated claims that trace to a real
  `source_url`. This is the hero metric; target is `GROUNDEDNESS_THRESHOLD`.
- **Qualification precision / recall** — agreement with a human-labeled set of
  qualified vs. disqualified companies.
- **Draft quality** — rubric-scored relevance and personalization of outreach.
- **Cost / latency** — tokens and wall-clock per run, per provider.

Planned layout:

```
evals/
  datasets/      # small labeled fixtures (domains, expected verdicts)
  cases/         # individual eval scenarios
  run_evals.py   # entry point that scores the pipeline against datasets
```
