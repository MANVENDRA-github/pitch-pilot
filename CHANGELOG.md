# Changelog

All notable changes to **pitch-pilot** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_Nothing yet — P1 (deterministic pipeline + nodes) is next._

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
