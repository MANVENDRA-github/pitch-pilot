# Roadmap

> **Last updated:** 2026-06-05 · **Source files:** project-wide

pitch-pilot is built in phases. Each phase is shippable and leaves the project in
a runnable state. Legend: ✅ done · 🟡 in progress · ⬜ planned.

| Phase | Status | Delivers |
| --- | --- | --- |
| **P0 — Foundation** | ✅ done | Installable scaffold: typed contracts, swappable clients, fail-loud config, smoke test, unit tests, and this docs site. |
| **P1 — Pipeline** | ⬜ planned (next) | The deterministic LangGraph outer graph and node functions (`research → qualify → draft → verify → log`) on top of `PipelineState`. |
| **P2 — Agentic research** | ⬜ planned | The bounded ReAct research sub-loop: plan → search → fetch → extract grounded `Fact`s, capped by `RESEARCH_MAX_QUERIES`. |
| **P3 — Verification & scoring** | ⬜ planned | Claim extraction, source-checking, the groundedness score, and the threshold gate that produces a `VerificationResult`. |
| **P4 — Evaluation** | ⬜ planned | Labeled datasets, the metrics harness, and a published baseline (fills the [evals](evals.md) numbers table). |
| **P5 — Storage & review app** | ⬜ planned | Production `Store` backends (HubSpot, Google Sheets) and a human-review UI over the queue (`app/`). |
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
