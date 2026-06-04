# Changelog

All notable changes to **pitch-pilot** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_Nothing yet — P2 (the deterministic pipeline graph + the qualify / draft / verify /
log nodes) is next._

## [0.2.0] — 2026-06-05

**P1 — Agentic research.** The research node: given a domain, it gathers
source-tagged, grounded `Fact`s across the dimensions an SDR cares about
(overview, news, hiring, tech). The next search query is chosen by the LLM, not a
fixed list — the loop is genuinely agentic, bounded by a hard query budget.

### Added

- `run_research(company, llm, search, settings)` — the agentic research loop:
  seed-fetch the company site, then let the LLM planner choose each next search
  query (`{"done", "reason", "next_query"}`) until it is satisfied or
  `RESEARCH_MAX_QUERIES` is hit. Records non-fatal failures instead of crashing.
- `extract_facts(...)` — the groundedness guard: extracts only claims the source
  text supports, each with a verbatim `evidence` snippet, and drops any candidate
  whose evidence is not a substring of the source (a cheap anti-hallucination
  check). Capped per source.
- `research_node(state)` — a thin LangGraph adapter that calls `run_research` and
  returns `{"research": ResearchResult}`, ready for the pipeline to wire in.
- `pitch-pilot research <domain>` CLI command — prints grounded facts grouped by
  category (each with its source URL) and a summary line.
- `Fact.evidence` — a short (`<= 200` char) verbatim snippet from the source text
  supporting the claim.
- `ResearchResult.errors` — non-fatal problems (failed fetch, empty search,
  extraction error) collected during a research run.

### Changed

- Groundedness now has a second enforcement layer: evidence must appear in the
  source (the extractor substring check), in addition to `Fact` requiring a
  `source_url` by construction.

## [0.1.0] — 2026-06-05

**P0 — Foundation.** The installable scaffold: typed contracts, swappable
clients, configuration, a smoke test, the unit-test suite, and this documentation
system.

### Added

- Typed data contracts (pydantic v2): `Fact` (enforces an `http(s)` `source_url`
  at construction), `SearchResult`, `ICP`, `Company`, `Lead`, `ResearchResult`,
  `QualificationResult`, `Draft`, `VerificationResult`.
- `PipelineState` typed state contract and a documented `build_pipeline()` stub.
- Swappable external clients behind small interfaces: `LLMClient`
  (`GeminiClient` default, `GroqClient` alternative), `SearchClient`
  (`TavilyClient`), and `fetch_page()` (httpx + selectolax).
- Typed, fail-loud configuration (`Settings` / `get_settings`) via
  pydantic-settings.
- `Store` protocol with a file-backed `JsonStore` plus a human-review queue.
- `pitch-pilot smoke` CLI command — the P0 acceptance gate.
- Unit test suite (no network) and the MkDocs Material documentation site with an
  auto-generated API reference and a documentation-maintenance protocol
  (`CLAUDE.md`).

### Notes

- Hero guarantee: **groundedness** — no `Fact` may exist without a `source_url`.
- Deliberate scope: pitch-pilot never auto-sends, and LinkedIn scraping is out of
  scope.
