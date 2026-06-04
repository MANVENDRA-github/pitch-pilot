# Evaluation

> **Last updated:** 2026-06-05 · **Source files:** `evals/`

An agent's quality should be *measured*, not vibed. This page defines how
pitch-pilot is evaluated, the metrics, and the current numbers.

!!! note "Status"
    The evaluation harness is built in **[P4](roadmap.md)**. The methodology and
    metrics below are the spec; the numbers table is seeded with targets and will
    be filled with real measurements once the harness runs.

## Methodology

Evaluation is **offline and reproducible**: the pipeline is run against a small,
hand-labeled dataset of company domains with known-good expectations, and its
outputs are scored automatically (plus a rubric for draft quality). Each run is
stamped with a date so changes over time are visible in the table below.

## Dataset

A labeled fixture set lives under `evals/datasets/` (JSON Lines):

- **Domains** — a spread of in-ICP and out-of-ICP companies.
- **Expected qualification** — `qualified: true/false` per domain, hand-labeled.
- **Ground-truth facts** — a few known, source-backed facts per company used to
  check that research finds real information and that drafts don't fabricate.

The set is intentionally small and curated (quality over size) so it can be
reviewed by a human and kept honest.

## Metrics

| Metric | Definition |
| --- | --- |
| **Groundedness rate** | Share of generated claims that trace to a real `source_url`, across the eval set. The hero metric. |
| **Qualification precision** | Of companies the agent qualified, the share that were truly in-ICP. |
| **Qualification recall** | Of truly in-ICP companies, the share the agent qualified. |
| **Draft quality** | Rubric score (1–5) for relevance and personalization of the outreach. |
| **Cost / run** | LLM + search spend per company processed. |
| **Latency / run** | Wall-clock per company processed. |

## How to run

```powershell
# (P4) once the harness lands:
python -m evals.run_evals
```

The harness loads the dataset, runs the pipeline per domain with the network
mocked or recorded where appropriate, scores the outputs, and prints/writes the
metrics table.

## Current numbers

Targets are provisional and will be revised once a baseline exists.

| Metric | Target | Current | As of |
| --- | --- | --- | --- |
| Groundedness rate | ≥ 0.90 | — (pending P4) | 2026-06-05 |
| Qualification precision | ≥ 0.80 | — (pending P4) | 2026-06-05 |
| Qualification recall | ≥ 0.70 | — (pending P4) | 2026-06-05 |
| Draft quality (1–5) | ≥ 4.0 | — (pending P4) | 2026-06-05 |
| Cost / run | < $0.01 | — (pending P4) | 2026-06-05 |
| Latency / run | < 60s | — (pending P4) | 2026-06-05 |

> When the harness runs, update this table and its **As of** date (see the
> documentation protocol in `CLAUDE.md`, rule 7).
