# Nodes

> **Last updated:** 2026-06-05 · **Source files:** `src/pitch_pilot/nodes/research.py`, `src/pitch_pilot/graph/state.py`

**Status: research node implemented (P1).** The `research_node` and its agentic loop live in `src/pitch_pilot/nodes/research.py`. The remaining nodes (`qualify`, `draft`, `verify`, `log`) and the outer-graph wiring land in later phases (see [`../roadmap.md`](../roadmap.md)).

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
| `research_node` | `company` | `research` (`ResearchResult`) | **Implemented (P1).** Runs the agentic research loop (seed → plan → search → extract Facts), bounded by `RESEARCH_MAX_QUERIES`. |
| `qualify_node` | `company`, `research`, `icp` | `qualification` (`QualificationResult`) | Planned. Scores the company against the ICP. A conditional edge stops disqualified leads (→ log) and sends qualified ones to draft. |
| `draft_node` | `company`, `research`, `qualification` | `draft` (`Draft`) | Planned. Writes the outreach draft using only grounded facts. |
| `verify_node` | `draft`, `research` | `verification` (`VerificationResult`) | Planned. Gate: `groundedness_score = grounded_claims / total_claims` must be `>= GROUNDEDNESS_THRESHOLD` (default `0.9`). |
| `log_node` | `company`, `research`, `qualification`, `draft`, `verification` | persisted `Lead` | Planned. Persists the lead and enqueues it for human review via the Store. Never auto-sends. |
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

The dimensions and caps are module constants in `nodes/research.py`:
`RESEARCH_DIMENSIONS`, `MAX_FACTS_PER_SOURCE`, `SEARCH_RESULTS_PER_QUERY`, and
`MAX_TEXT_CHARS`.

### The extractor — the groundedness guard

`extract_facts(text, source_url, source_title, llm)` is where groundedness is
enforced at research time. It prompts the model to return **only** claims the
provided text explicitly supports, each with a verbatim `evidence` snippet, a
`category`, and a `confidence`. The system prompt forbids using outside knowledge.
Then, for every candidate, the extractor checks that the `evidence` actually
appears in the source text (a whitespace- and case-insensitive substring match)
and **drops** — and logs — any candidate that fails. This is a cheap but effective
anti-hallucination layer; see [groundedness.md](../groundedness.md). Extraction is
capped at `MAX_FACTS_PER_SOURCE` per source so one page can't dominate.

### Robustness

The node never crashes on a bad page or a flaky provider. A failed seed fetch, an
empty search, a search backend error, or an extraction error is appended to
`ResearchResult.errors` and the loop continues. The run is fully synchronous,
matching the rest of the codebase.

### Trying it

The [CLI](../getting-started.md) exposes the node directly:

    python -m pitch_pilot.cli research acme.com

It prints the grounded facts grouped by category — each with its `source_url` —
and a summary line (facts, distinct sources, and the LLM-chosen queries that ran).

## Where the full spec lives

This page documents the node contracts and the implemented research node. The full
pipeline specification — conditional edges, the disqualified-stops-here branch, and
the groundedness gate — is documented in **[`../pipeline.md`](../pipeline.md)**. The
signatures of each node are generated into the API Reference (in the nav).
