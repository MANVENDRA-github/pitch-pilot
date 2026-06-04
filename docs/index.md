# pitch-pilot

> **Last updated:** 2026-06-05 · **Source files:** `README.md`, `src/pitch_pilot/`

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
   agentic loop        │ gate: disqualified → stop  │ gate: score ≥ threshold
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

P0 (the foundation) is complete: typed contracts, swappable clients, configuration,
a working smoke test, tests, and this documentation site. The live pipeline and
node logic land in [P1](roadmap.md). This project is built in public as a
portfolio-grade reference for a grounded, agentic outbound system.
