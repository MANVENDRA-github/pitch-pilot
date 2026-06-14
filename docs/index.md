# pitch-pilot

> **Last updated:** 2026-06-14 · **Source files:** `README.md`, `src/pitch_pilot/`

**pitch-pilot** is an autonomous [SDR](glossary.md) (Sales Development Rep) agent.
Give it a company **domain** and it researches the company, qualifies it against
your [Ideal Customer Profile](glossary.md), drafts outreach, verifies every claim
against a real source, and files the result for a human to review.

```text
domain → research → qualify → draft → verify → log
```

## The groundedness thesis

The hero feature is **groundedness**: *no fact exists without a `source_url`.*

Most "AI SDR" tools generate fluent outreach that is confidently wrong — invented
funding rounds, misattributed quotes, hallucinated headcounts. pitch-pilot takes
the opposite stance: the atomic unit of research is a typed
[`Fact`](data-models.md) that **cannot be constructed without an `http(s)`
source URL**. Outreach is drafted *only* from grounded facts, every claim is then
re-checked against its source, and a draft is allowed through only if its
groundedness score clears a configurable threshold.

The result is outreach you can trust and audit: every sentence traces back to a
page. See [Groundedness](groundedness.md) for the deep dive.

## Results at a glance

`cerebras/gpt-oss-120b`, gate-critical calls at temperature 0, 2026-06-14. The
qualifier fix ([ADR-0015](decisions.md)) was developed on the original 17 and
validated on a **held-out** set it never saw:

- **Held-out (n=8, headline): Qualification F1 1.0** (precision 1.0, recall 1.0;
  TP/FP/TN/FN = 4/0/4/0) — every unseen company landed correctly, including an
  incumbent bank and two borderlines.
- **Original 17 (development): F1 0.769 → 0.947** after the fix (all six false
  positives eliminated; one new false negative from a flaky industry assessment).
- **Mean groundedness 0.95–0.96**, equal to the faithfulness score under the strict
  gate (one signal, not two).

Both sets improved, so the fix generalizes rather than overfitting. Full provenance,
before/after tables, the live-re-verifiability caveat, and the honest limitations:
[Evaluation](evals.md).

## What it does — and refuses to do

| Does | Refuses |
| --- | --- |
| Researches a company from its domain, citing every fact | ❌ Never auto-sends — qualified leads go to a human-review queue |
| Qualifies against a declarative ICP | ❌ No LinkedIn scraping (out of scope by design) |
| Drafts outreach grounded in cited facts | ❌ No ungrounded claims — a `Fact` without a source can't exist |
| Verifies each claim and scores groundedness | ❌ No paid data brokers — built to run on free tiers |

See [Limitations](limitations.md) for the full, honest scope.

## Architecture at a glance

pitch-pilot is a **hybrid**: a *deterministic outer graph* wires the fixed
business steps in a known, auditable order, while an *agentic research sub-loop*
runs inside the research step where open-ended exploration actually helps.

```text
   ┌──────────┐   ┌──────────┐   ┌────────┐   ┌────────┐   ┌────────┐
   │ RESEARCH │──▶│ QUALIFY  │──▶│ DRAFT  │──▶│ VERIFY │──▶│  LOG   │
   └──────────┘   └────┬─────┘   └────────┘   └───┬────┘   └────────┘
   agentic loop        │ gate: disqualified → stop  │ gate: body claims faithful?
```

Read more in [Architecture](architecture.md) and [Pipeline](pipeline.md).

## Quickstart

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env   # then add GEMINI_API_KEY + TAVILY_API_KEY
python -m pitch_pilot.cli smoke
```

Full instructions: [Getting Started](getting-started.md).

## Where to go next

- **[Getting Started](getting-started.md)** — install, configure, run the smoke test.
- **[Configuration](configuration.md)** — every environment variable and setting.
- **[Architecture](architecture.md)** / **[Pipeline](pipeline.md)** — how it's built.
- **[Data Models](data-models.md)** — the typed contracts that carry everything.
- **[Groundedness](groundedness.md)** — the hero feature in depth.
- **[Roadmap](roadmap.md)** — phases P0–P7 and where we are.
- **API Reference** (in the top navigation) — auto-generated live from the source docstrings.

## Project status

The end-to-end pipeline is shipping (P0–P4): the agentic research loop, ICP
qualification, grounded drafting, the groundedness verification gate, and the
offline [evaluation harness](evals.md) with the numbers above. Storage backends and
a human-review app are next ([P5](roadmap.md)). This project is built in public as a
portfolio-grade reference for a grounded, agentic outbound system.
