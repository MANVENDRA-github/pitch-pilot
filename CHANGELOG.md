# Changelog

All notable changes to **pitch-pilot** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_Nothing yet — P5 (storage and review app: production `Store` backends and a
human-review UI over the queue) is next._

## [0.8.0] — 2026-06-14

**Draft grounding decoupled from phrasing.** The first full Cerebras eval revealed
that validating draft hooks as **verbatim source substrings** was brittle: faithful
paraphrases were discarded, so most qualified companies scored
`groundedness=0.0` with empty verdicts — an artifact of the matching layer, not of
grounding. Source-text grounding belongs at extraction; the draft should ground by
*selecting facts* and the verify gate should judge the *body*'s faithfulness (see
ADR-0014).

### Changed

- **Draft node** (`nodes/draft.py`) — grounds by **fact-selection**: the model is
  shown the claimable (first-party) facts as a numbered list and returns the **ids**
  it grounded the email in. `hooks_used` is the canonical claim text of those facts,
  grounded by construction. No more verbatim-hook substring matching or fuzzy
  matching of paraphrased text back to facts.
- **Verify node** (`nodes/verify.py`) — now a **structural** check (every hook
  re-resolves to a first-party fact; else `structural`) plus **one** body-faithfulness
  judge (`judge_body`) over the draft **body** against the selected facts, rating each
  body claim `faithful` / `overreach` / `unsupported`. Passes iff grounded, body
  non-empty, judge ran, and no `unsupported` (and no `overreach` under
  `FAITHFULNESS_STRICT`). `groundedness_score` redefined as
  `faithful_body_claims / total_body_claims`. `judge_faithfulness` is replaced by
  `judge_body`.
- **`VerificationResult` / `ClaimVerdict`** — `claim_verdicts` now describe **body**
  claims (with the supporting fact cited by id); `tier_breakdown` counts the grounding
  hooks by tier; failure reasons shrink to `unsupported` / `overreach` / `structural`
  / `judge-error`.

### Added

- **`redraft` eval command** (`evals/run_eval.py`) — re-runs only draft + verify for
  already-qualified companies, reusing cached research **and** each record's frozen
  qualification verdict (the qualification matrix is never recomputed).

### Eval (cerebras/gpt-oss-120b, 2026-06-14)

- Qualification **unchanged** (frozen): TP=10, FP=3, TN=4, FN=0; F1 **0.870**.
- Draft-gate pass-rate **11/13 = 0.846**, mean groundedness **0.936**, mean
  faithfulness **0.936**, own_site live re-verifiability **0.90 (45/50)**. See
  `docs/evals.md` (with a documented Known Limitations section on the three
  qualification false positives — the industry-gating fix is deferred future work).

## [0.7.0] — 2026-06-13

**Cerebras provider.** Adds a third LLM provider so the eval can run end-to-end in
one session on Cerebras's ~1M tokens/day free tier (~10x Groq) (see ADR-0013).

### Added

- `CerebrasClient` (`clients/llm.py`) — OpenAI-compatible via `cerebras-cloud-sdk`;
  mirrors `GroqClient` (JSON mode + lenient parsing + error normalization to
  `LLMError`). Default model `gpt-oss-120b` (available models vary by Cerebras
  account/tier — check `models.list()`).
- `CEREBRAS_API_KEY` (optional) and `CEREBRAS_MODEL` settings; `get_llm_client()`
  now selects `"cerebras"`. `Settings.active_model` resolves the model id for the
  active provider.
- `CONTEXT_TOKEN_CAP` (8192) and `trim_to_token_budget()` — bound the qualify/draft
  facts payloads so no single prompt exceeds Cerebras's free-tier context window.
- `cerebras-cloud-sdk` runtime dependency (lazily imported — only needed when the
  provider is selected).

### Changed

- `LLM_PROVIDER` now accepts `"cerebras"` in addition to `"gemini"` / `"groq"`; the
  smoke check and eval report use `Settings.active_model` so the right model is named.

## [0.6.0] — 2026-06-13

**Lean research depth (default).** Research is leaner by default — cheaper, faster,
and within free-tier token caps — at the same depth the eval and production both
run, so there is no eval-vs-prod mismatch (see ADR-0012).

### Added

- `RESEARCH_MAX_PAGE_CHARS` (default `3500`) — truncates each source's text fed to
  the extractor; the biggest token lever (was ~12,000). `extract_facts` truncates
  once and runs the evidence-substring check against that same truncated text, so
  groundedness is preserved.
- `RESEARCH_MAX_FACTS_PER_SOURCE` (default `5`) setting (was a fixed `8`).

### Changed

- `RESEARCH_MAX_QUERIES` default 4 → **3**.
- Research depth is now configured via `Settings` (the three knobs above) and
  passed through `run_research` to the extractor; the old module constants survive
  only as lean fallback defaults. Per-company research token use roughly halves.

## [0.5.0] — 2026-06-13

**P4 — Eval harness.** The offline evaluation that produces the headline numbers,
built for free-tier reality (cache, checkpoint, back off, resume).

### Added

- `examples/eval_icp.json` and `examples/eval_companies.json` — a hypothetical
  fintech ICP and a labeled company set spanning `good_fit` / `bad_fit` (incl. an
  incumbent-bank negative-signal case) / `sparse`. Labels are explicitly
  **human-proposed** with a `[VERIFY]` flag and a defensible labeling rubric in
  `docs/evals.md`.
- `evals/run_eval.py` — the runner with three commands: `run` (research[cached] →
  qualify → draft → verify, checkpointed and resumable), `recheck` (independent live
  re-verification of used sources, reported by tier), and `report` (recompute, no
  network). Research is cached per domain; rate-limits are retried with exponential
  backoff via `RetryingLLM`; a persistently rate-limited company is recorded as an
  error and retried next run rather than aborting the run.
- `evals/metrics.py` — pure metric functions: qualification accuracy / precision /
  recall / F1, draft-gate pass-rate, mean groundedness & faithfulness, a
  failure-mode breakdown (proves the gate catches, not just passes), live
  re-verifiability by tier, and facts/company by category (degradation).
- `docs/evals.md` rewritten (methodology, rubric, metrics, numbers table) and a new
  `docs/components/evals.md` documenting the harness internals.
- CLI: `python -m evals.run_eval [--limit N] [--resume] [recheck|report]`.

### Changed

- `pytest` now also has the repo root on `pythonpath` so the `evals` package is
  importable in tests; `evals/cache/` and `evals/results/` are git-ignored runtime
  artifacts (dated reports under `evals/reports/` are kept).

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
