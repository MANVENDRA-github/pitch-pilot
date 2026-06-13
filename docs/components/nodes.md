# Nodes

> **Last updated:** 2026-06-14 · **Source files:** `src/pitch_pilot/nodes/`, `src/pitch_pilot/graph/state.py`
>
> P3 hardened the verify node and made Policy B claim-gating the rule in the draft node. **0.8.0** decoupled grounding from phrasing: the draft grounds by *selecting facts* (no verbatim-hook substring check), and verify judges the **body**'s faithfulness to the selected facts (see [ADR-0014](../decisions.md)).

**Status: all five nodes implemented (research in P1; qualify, draft, verify, log in P2).** Each node lives in its own module under `src/pitch_pilot/nodes/`, and `build_pipeline()` wires them into the outer graph (see [`graph.md`](graph.md) and [`../pipeline.md`](../pipeline.md)). Every node follows the same shape: a pure `run_*(...)` function that takes its dependencies as arguments (trivially testable offline) plus a thin `*_node(state)` graph adapter that can be handed injected clients.

## What a node is

A *node* is the unit of work executed by the LangGraph pipeline. Each node is a function that:

1. Takes the `PipelineState` — the single typed object that flows through the run.
2. Performs exactly one step (research, qualify, draft, verify, or log).
3. Returns the state with its own slice filled in.

Because the state accumulates as the run progresses, every node reads what earlier nodes have written and adds its own artifact. The producing-node fields on `PipelineState` start as `None` and are populated in order:

    company + icp                  (seed inputs)
        → research                 (research_node)
        → qualification            (qualify_node)
        → draft                    (draft_node)
        → verification             (verify_node)

The `PipelineState` contract itself — including the `status` and `errors` bookkeeping fields — lives in `src/pitch_pilot/graph/state.py`. See [`../data-models.md`](../data-models.md) for the artifact models (`ResearchResult`, `QualificationResult`, `Draft`, `VerificationResult`) and [`graph.md`](graph.md) for how the nodes are wired into the outer graph.

## Node status

| Node | Reads | Writes | Status / notes |
| --- | --- | --- | --- |
| `research_node` | `company` | `research` (`ResearchResult`) | **Implemented (P1).** Runs the agentic research loop (seed → plan → search → extract Facts), bounded by `RESEARCH_MAX_QUERIES`. Tags each fact with a `source_tier`. |
| `qualify_node` | `research`, `icp` | `qualification` (`QualificationResult`) | **Implemented (P2).** Hybrid: LLM assesses signals vs facts; deterministic code scores + vetoes. Conditional edge stops disqualified leads (→ log), sends qualified ones to draft. |
| `draft_node` | `research`, `qualification` | `draft` (`Draft`) | **Implemented (P2; 0.8.0 selection).** Writes free-prose outreach grounded only in first-party facts the model **selects by id**; `hooks_used` are those facts (grounded by construction). |
| `verify_node` | `draft`, `research` | `verification` (`VerificationResult`) | **Hardened (P3; 0.8.0 body judge).** Re-resolves hooks to first-party facts (structural), then an LLM judge rates the **body**'s claims `faithful`/`overreach`/`unsupported` against the selected facts. Network-free except the judge call. |
| `log_node` | whole `PipelineState` | persisted `Lead` | **Implemented (P2).** Saves `ready` / enqueues `review` / saves `disqualified` via the Store. Never auto-sends. |
| `discover_node` | — (seed source) | candidate domains | Future seam (P6) that sources new candidate domains to seed runs. |

## The research node (P1)

The research node turns a `Company` (really just a domain) into a
[`ResearchResult`](../data-models.md) full of source-tagged
[`Fact`](../data-models.md)s. It has two entry points:

- **`run_research(company, llm, search, settings) -> ResearchResult`** — the pure
  function that does the work. It takes its clients as arguments, so it is trivial
  to test offline with fakes.
- **`research_node(state) -> dict`** — the thin graph adapter. It builds the
  configured clients and calls `run_research`, returning `{"research": ...}` for
  the pipeline to merge into the state. This is the seam the outer graph plugs
  into in a later phase.

### The agentic loop

What makes the node *agentic* is that the **LLM chooses each next search query**;
the control flow is not a fixed query list. One run proceeds as:

1. **Seed.** `fetch_page(company.domain)` and run the extractor on the result
   (the site URL is the source). These are the first facts.
2. **Plan.** Ask the `LLMClient` for the next move, given the target dimensions
   (`overview` / `news` / `hiring` / `tech`), a summary of what's been gathered and
   which dimensions are still thin, and the queries already run. It replies with
   `{"done": bool, "reason": str, "next_query": str | null}`.
3. **Stop?** Stop when the planner returns `done: true` (or no query), **or** when
   `len(queries_run) >= RESEARCH_MAX_QUERIES`. The budget is a hard cap that
   overrides the planner — so the loop is agentic *within* a predictable bound.
4. **Search + extract.** Run the chosen query through the `SearchClient`; for each
   result, run the extractor on its content (the result URL is the source).
5. **Reflect.** De-duplicate by claim, accumulate facts, record the query, loop.

**Research depth is tunable and leaned out by default** (cheaper, faster, and
fits free-tier token caps — see [ADR-0012](../decisions.md)):
`RESEARCH_MAX_QUERIES` (3), `RESEARCH_MAX_PAGE_CHARS` (3500 — the biggest token
lever), and `RESEARCH_MAX_FACTS_PER_SOURCE` (5) are [`Settings`](../configuration.md)
that `run_research` passes through to the extractor. `RESEARCH_DIMENSIONS` and
`SEARCH_RESULTS_PER_QUERY` remain module constants; `MAX_TEXT_CHARS` /
`MAX_FACTS_PER_SOURCE` survive only as lean fallback defaults for direct
`extract_facts` calls.

### The extractor — the groundedness guard

`extract_facts(text, source_url, source_title, llm, company_domain=None, *,
max_page_chars=…, max_facts_per_source=…)` is where groundedness is enforced at
research time. It first **truncates the source text to `max_page_chars`**, then
prompts the model to return **only** claims that truncated text explicitly
supports, each with a verbatim `evidence` snippet, a `category`, and a
`confidence`. The system prompt forbids using outside knowledge. For every
candidate, the extractor checks that the `evidence` actually appears in **that same
truncated text** (a whitespace- and case-insensitive substring match) and
**drops** — and logs — any candidate that fails: the model can only ground claims
in the text we verify against, so truncation never weakens the guarantee. Surviving
facts are tagged with a `source_tier` via `classify_source_tier(url,
company_domain)`. Extraction stops after `max_facts_per_source` facts so one page
can't dominate. See [groundedness.md](../groundedness.md).

### Robustness

The node never crashes on a bad page or a flaky provider. A failed seed fetch, an
empty search, a search backend error, or an extraction error is appended to
`ResearchResult.errors` and the loop continues. The run is fully synchronous,
matching the rest of the codebase.

### Trying it

The [CLI](../getting-started.md) exposes the research node directly:

    python -m pitch_pilot.cli research acme.com

It prints the grounded facts grouped by category — each with its `source_url` —
and a summary line (facts, distinct sources, and the LLM-chosen queries that ran).

## The qualify node (P2)

`run_qualification(research, icp, llm, settings) -> QualificationResult` is a
**hybrid** of model judgement and deterministic policy (see
[ADR-0009](../decisions.md)):

- The **LLM assesses**, for each ICP attribute (industry, region, employee count)
  and each positive/negative signal, whether the facts support it —
  `match` / `no_match` / `unknown`, citing the supporting fact. It does *not* decide
  qualification.
- **Deterministic code scores.** A weighted blend of industry / size / region /
  positive-signals yields a fit score in `[0, 1]`; *unknown* structural components
  are dropped and the weights renormalized, so a research gap never penalizes.
  Positive signals score as `matched / total`.
- **A matched negative signal is a hard veto** — it forces `qualified = False`
  regardless of score.
- The company qualifies iff `score >= QUALIFY_THRESHOLD` and nothing vetoed.

Unknowns are never guessed: an unknown signal appears in neither `matched_signals`
nor `missed_signals`.

## The draft node (Policy B since P3; selection since 0.8.0)

`run_draft(research, qualification, llm, settings) -> Draft` writes the outreach
email, grounding it by **fact-selection** (see [groundedness.md](../groundedness.md)
and [ADR-0014](../decisions.md)):

- **First-party claim pool.** Only `own_site` / `authoritative` facts are offered
  to the model as *claimable*, on **numbered** lines. `third_party_snippet` facts are
  passed in a separate *context-only* section — usable for tone or framing, never as
  a stated claim. (P2 gated only hard numerics; P3's Policy B extends this to **all**
  claims.)
- **Selection by id, not by copy.** The model writes the body as free prose (it may
  paraphrase) and returns the **ids** of the claimable facts it grounded the email
  in. `hooks_used` is the canonical claim text of those facts — a subset of the
  first-party research facts, grounded **by construction**. The draft layer does
  **not** substring- or fuzzy-match hook text against the source.

## The verify node (P3 hardening; 0.8.0 body-faithfulness)

`run_verification(draft, research, llm, settings) -> VerificationResult` audits the
draft in two parts (see the [Groundedness methodology](../groundedness.md)):

1. **Structural — the grounding facts.** Each `hook_used` is re-resolved to a
   first-party `Fact`; any that fails to resolve is a `structural` failure (an
   invariant violation — hooks are first-party by construction).
2. **Faithfulness — the body.** One LLM judge (`judge_body`) reads the draft **body**
   and the selected facts, extracts every factual claim the body makes about the
   company, and rates each `faithful` / `overreach` / `unsupported`. The judge fails
   closed (any error or malformed response fails the draft).

The draft **passes** iff it has a grounded hook, a non-empty body, the judge ran,
and no body claim is `unsupported` (and none is `overreach` under
`FAITHFULNESS_STRICT`). Each failure is recorded in `flagged_claims` with its reason
(`structural` / `overreach` / `unsupported` / `judge-error`); the per-body-claim
audit trail is returned in `VerificationResult.claim_verdicts`, alongside
`groundedness_score` (faithful body claims / total), `faithfulness_score`, and
`tier_breakdown` (the grounding hooks by tier). The node is **network-free except
for the judge call** — independent live re-verification of sources is an eval-time
metric (P4), not part of the per-run path.

## The log node (P2)

`log_lead(state, store) -> dict` is the terminal step and **never sends**. It
builds a self-contained [`Lead`](../data-models.md) (company + qualification +
draft + verification) and routes it via the [`Store`](storage.md): a passing draft
is saved as `ready`, a failing one is enqueued for `review`, and a disqualified
company is saved as `disqualified`.

### Trying the whole pipeline

    python -m pitch_pilot.cli run acme.com --icp examples/icp.sample.json

It runs research → qualify → draft → verify → log and prints the qualification
verdict, the draft, the verification score with any flagged claims, and where the
lead was logged.

## Where the full spec lives

This page documents the node contracts. The full pipeline specification —
conditional edges, the disqualified-stops-here branch, and the groundedness gate —
is documented in **[`../pipeline.md`](../pipeline.md)**. The signatures of each node
are generated into the API Reference (in the nav).
