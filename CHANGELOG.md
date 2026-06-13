# Changelog

All notable changes to **pitch-pilot** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_Nothing yet — P4 (evaluation: labeled datasets, the metrics harness, a published
baseline, and the eval-time independent live re-verification deferred in ADR-0010)
is next._

## [0.4.0] — 2026-06-13

**P3 — Verification & scoring.** The basic verify gate becomes the real
groundedness gate. A passing draft now makes a precise, defensible promise: every
claim is first-party-sourced, substring-anchored, and judged faithful to its
evidence by an LLM — with a per-claim audit trail.

### Added

- `judge_faithfulness(claim, evidence, llm)` — the core P3 addition: an LLM judge
  that rates each claim↔evidence pair `faithful` / `overreach` / `unsupported`.
  Distinct from the substring check (presence): it judges actual *support*. Fails
  closed (any judge error ⇒ `unsupported`).
- `VerificationResult` gains `claim_verdicts` (a per-claim audit trail — see the new
  `ClaimVerdict` model), `faithfulness_score` (faithful / total claims), and
  `tier_breakdown` (claims per backing source tier).
- `FAITHFULNESS_STRICT` setting (default `true`): when true an `overreach` verdict
  fails the gate; when false only `unsupported` fails.
- The `run` CLI now prints per-claim verdicts (tier, `substring_ok`, faithfulness),
  the `faithfulness_score`, the tier breakdown, and the pass/fail with reasons.

### Changed

- **Policy B (first-party-only for claims), enforced end-to-end.** The draft node
  restricts the *claimable* fact pool to `own_site` / `authoritative` facts;
  `third_party_snippet` facts may inform context but can never become a hook. The
  verify node hard-fails any claim backed only by a `third_party_snippet` fact
  (`volatile-source`) — a policy violation, not a soft flag. (Supersedes P2's
  numeric-only tier gate.) See ADR-0010.
- The verify gate is now: a claim is *verified* iff backed + first-party tier +
  `substring_ok` + faithful (`overreach` allowed only when `FAITHFULNESS_STRICT` is
  off); a draft passes only if **every** claim verifies. `flagged_claims` records
  the specific reason per failure (`unbacked` / `volatile-source` / `not-substring`
  / `overreach` / `unsupported`).
- `run_verification(...)` and `verify_node` now take an LLM client (for the judge);
  the verify node is network-free except for that one call. Independent live
  re-verification of sources is deferred to P4 as an eval-time, by-tier metric
  (ADR-0010).
- `groundedness.md` rewritten as a precise four-layer methodology with exact metric
  definitions and an honest headline framing.

### Fixed

- The LLM clients (`GeminiClient` / `GroqClient`) now normalize provider SDK
  exceptions to `LLMError`, so a vendor failure (e.g. Groq's server-side
  `json_validate_failed`, or a rate-limit) no longer escapes a node's `except
  LLMError` and crashes the pipeline mid-run — nodes degrade gracefully instead.

## [0.3.0] — 2026-06-13

**P2 — Pipeline.** The deterministic outer graph is wired end-to-end: a company
domain now flows through research → qualify → draft → verify → log and lands as a
grounded, human-reviewable lead. Nothing is ever auto-sent.

### Added

- `Fact.source_tier` (`own_site` / `authoritative` / `third_party_snippet`) —
  assigned deterministically from the source URL by the research node
  (`classify_source_tier`). Encodes the P1 validation finding that own-site facts
  are far more durable than search-snippet facts (ADR-0008).
- `run_qualification(...)` + `qualify_node` — a hybrid qualifier: the LLM
  semantically matches ICP attributes/signals against the facts, then deterministic
  code computes a weighted fit score, applies a hard veto on matched negative
  signals, and decides against the new `QUALIFY_THRESHOLD`. Unknowns are never
  guessed (ADR-0009).
- `run_draft(...)` + `draft_node` — writes outreach only from grounded facts:
  withholds hard numerics sourced from `third_party_snippet` facts, and validates
  every hook back to a real fact so `Draft.hooks_used` is always a subset of the
  research facts.
- `run_verification(...)` + `verify_node` — the basic groundedness gate: each draft
  claim must map to a `Fact` with a source and evidence; claims are flagged
  `unbacked:` or `volatile:`; a draft passes only if the score clears
  `GROUNDEDNESS_THRESHOLD` and nothing is unbacked.
- `log_lead(...)` + `log_node` — the terminal step: persists a self-contained
  `Lead` and routes it (`ready` saved / `review` enqueued / `disqualified` saved)
  via the `Store`. Never auto-sends.
- `build_pipeline()` — the compiled LangGraph `StateGraph` over `PipelineState`
  with the two conditional gates (qualify → draft/log; verify → log decides
  ready/review). Accepts injectable clients/store for offline testing.
- `pitch-pilot run <domain> [--icp PATH]` CLI command — runs the full pipeline and
  prints the qualification verdict, the draft, the verification score with flagged
  claims, and where the lead was logged.
- `QUALIFY_THRESHOLD` setting (default `0.5`); `examples/icp.sample.json` and
  `load_icp()`.

### Changed

- `Lead` now carries the run's artifacts (`qualification`, `draft`,
  `verification`, `status`) so a persisted lead is self-contained.
- `build_pipeline()` is implemented (was a P0 stub that raised
  `NotImplementedError`).
- `extract_facts(...)` takes an optional `company_domain` to tier each fact's
  source.

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
