# Roadmap

> **Last updated:** 2026-06-14 · **Source files:** project-wide

pitch-pilot is built in phases. Each phase is shippable and leaves the project in
a runnable state. Legend: ✅ done · 🟡 in progress · ⬜ planned.

| Phase | Status | Delivers |
| --- | --- | --- |
| **P0 — Foundation** | ✅ done | Installable scaffold: typed contracts, swappable clients, fail-loud config, smoke test, unit tests, and this docs site. |
| **P1 — Agentic research** | ✅ done | The bounded, LLM-driven research loop (seed → plan → search → extract grounded `Fact`s, capped by `RESEARCH_MAX_QUERIES`), the `Fact.evidence` substring grounding check, a `research_node` graph adapter, and the `research` CLI command. |
| **P2 — Pipeline** | ✅ done | The deterministic LangGraph outer graph (`build_pipeline()`) and the node functions (`qualify → draft → verify → log`) wired on top of `PipelineState`, plus `Fact.source_tier`, the `pitch-pilot run` CLI, and a basic verification gate. |
| **P3 — Verification & scoring** | ✅ done | The real groundedness gate: Policy B (first-party-only claims), an LLM faithfulness judge, the `groundedness_score` / `faithfulness_score` / `tier_breakdown` metrics and per-claim audit trail, and the documented [methodology](groundedness.md). _Refined in 0.8.0: draft grounding decoupled from phrasing — fact-selection + a body-faithfulness judge ([ADR-0014](decisions.md))._ |
| **P4 — Evaluation** | ✅ done | The labeled eval set (positives + negatives + sparse, with a defensible rubric), the rate-limit-resilient harness (`evals/run_eval.py` — cache, checkpoint, backoff, resume, `redraft`), the metrics module, the independent live re-check by tier, and the dated report. Headline numbers recorded in [Evaluation](evals.md) (cerebras/gpt-oss-120b, 2026-06-14); labels remain human-proposed. |
| **P5 — Storage & review app** | ⬜ planned (next) | Production `Store` backends (HubSpot, Google Sheets) and a human-review UI over the queue (`app/`). |
| **P6 — Discovery** | ⬜ planned | The `discover_node` seam that sources candidate domains (inbound lists, look-alikes, market maps) under $0 constraints. |
| **P7 — Hardening & deploy** | ⬜ planned | Observability, rate limiting, packaging, the live docs site, and CI/CD. |

## What "done" means per phase

A phase is not done until:

1. Its code has Google-style docstrings and the matching docs are updated.
2. The unit test suite passes with no network.
3. `mkdocs build --strict` passes (no broken links, no missing pages).
4. The [Changelog](changelog.md) has an entry and this table is updated.

See the documentation-maintenance protocol in `CLAUDE.md` for the full rules.

## Guiding constraints (every phase)

- **Groundedness first** — no claim without a source.
- **Never auto-send** — a human approves outbound.
- **$0-friendly** — runs on free-tier LLM + search; no paid data brokers.

See [Limitations](limitations.md) for the boundaries these imply.
