# Pipeline

> **Last updated:** 2026-06-05 · **Source files:** `src/pitch_pilot/graph/`, `src/pitch_pilot/nodes/`

The pipeline is the deterministic outer graph that orchestrates a run. It is
assembled with [LangGraph](glossary.md) on top of the typed
[`PipelineState`](components/graph.md) contract.

!!! note "Status"
    The **`research_node`** and its [agentic research sub-loop](#the-agentic-research-sub-loop)
    are implemented in **[P1](roadmap.md)** (plus a thin `research_node` graph
    adapter). The deterministic outer-graph wiring (`build_pipeline()`) and the
    remaining nodes (`qualify`, `draft`, `verify`, `log`) land in
    **[P2](roadmap.md)**; for those, this page is the design spec the
    implementation will follow, so it is written ahead of the code.

## The graph

```text
            ┌────────────┐
  state ──▶ │  research  │  (agentic sub-loop)
            └─────┬──────┘
                  │ research: ResearchResult
                  ▼
            ┌────────────┐
            │  qualify   │
            └─────┬──────┘
                  │ qualification: QualificationResult
          ┌───────┴────────┐
   disqualified          qualified
          │                 │
          ▼                 ▼
       ┌──────┐        ┌────────┐
       │ log  │◀──┐    │ draft  │
       └──────┘   │    └───┬────┘
          ▲       │        │ draft: Draft
          │       │        ▼
          │       │    ┌────────┐
          │       │    │ verify │
          │       │    └───┬────┘
          │       │  verification: VerificationResult
          │       └────────┤
          │   passed / failed (both are logged for review)
          └────────────────┘
```

Every node receives the `PipelineState`, reads what it needs, and returns the
state with its slice filled in. State accumulates; nothing is discarded.

## Nodes

### `research_node` — gather grounded facts ✅ implemented (P1)

- **Reads:** `company`
- **Writes:** `research: ResearchResult`
- **What it does:** Runs the [agentic research sub-loop](#the-agentic-research-sub-loop)
  to produce a set of [`Fact`](data-models.md)s, each carrying a `source_url` and
  a verbatim `evidence` snippet. Non-fatal failures are recorded on
  `ResearchResult.errors` rather than raised.
- **Uses:** [`SearchClient`](components/clients.md) (Tavily) and
  [`fetch_page`](components/clients.md); the [`LLMClient`](components/clients.md)
  to plan queries and extract facts.
- **Entry points:** the pure function `run_research(company, llm, search, settings)`
  does the work; the `research_node(state)` graph adapter calls it and returns
  `{"research": ResearchResult}`. See [components/nodes.md](components/nodes.md).

### `qualify_node` — score against the ICP

- **Reads:** `company`, `research`, `icp`
- **Writes:** `qualification: QualificationResult`
- **What it does:** Scores the company against the declarative
  [`ICP`](data-models.md) using the gathered facts; records `matched_signals` and
  `missed_signals` and a human-readable `reason`.
- **Conditional edge:** if `qualified` is `False` → **`log_node`** (stop early);
  if `True` → **`draft_node`**.

### `draft_node` — write grounded outreach

- **Reads:** `company`, `research`, `qualification`
- **Writes:** `draft: Draft`
- **What it does:** Composes a subject + body **only from grounded facts**, so
  every claim already has a citable source attached. Records `hooks_used`.

### `verify_node` — audit groundedness

- **Reads:** `draft`, `research`
- **Writes:** `verification: VerificationResult`
- **What it does:** Extracts the factual claims in the draft, checks each against
  a source, and computes a groundedness score. See [Groundedness](groundedness.md).
- **Conditional edge (gate):** the draft passes only if
  `groundedness_score ≥ GROUNDEDNESS_THRESHOLD`. Either way the run proceeds to
  `log_node`; a failed draft is logged and flagged for the reviewer rather than
  discarded.

### `log_node` — persist and queue for review

- **Reads:** the whole `PipelineState`
- **Writes:** persistence side effects (no state change)
- **What it does:** Persists the [`Lead`](data-models.md) and **enqueues it for
  human review** via the [`Store`](components/storage.md). pitch-pilot never
  auto-sends.

### `discover_node` — future seam

A future node ([P6](roadmap.md)) that *produces* candidate domains to seed runs
(inbound lists, look-alikes, market maps). It sits in front of `research_node`
and emits `Company` objects; nothing downstream changes.

## Conditional edges (gates)

| Gate | Condition | True | False |
| --- | --- | --- | --- |
| Qualification | `qualification.qualified` | → `draft_node` | → `log_node` (stop) |
| Groundedness | `verification.groundedness_score ≥ GROUNDEDNESS_THRESHOLD` | draft marked passing | draft flagged, still logged |

Both gates are deterministic functions of the typed state, which keeps the run
auditable.

## The agentic research sub-loop

Inside `research_node`, the model runs a bounded [ReAct](glossary.md)-style loop —
this is the one place pitch-pilot is genuinely *agentic*, because **the LLM picks
the next search query** instead of working through a fixed list:

```text
  seed-fetch ──▶ extract ──▶ ┌─ plan (LLM: next query or done?) ─┐
                             │                                   │ done / budget hit
                             ▼                                   ▼
                           search ──▶ extract ──▶ reflect    ResearchResult
                             ▲                        │
                             └────────────────────────┘
```

1. **Seed** — `fetch_page(company.domain)`; if it returns text, run the extractor
   on it (the site URL is the source). These are the first facts.
2. **Plan** — call the `LLMClient` with the target dimensions
   (overview / news / hiring / tech), a summary of facts gathered so far and which
   dimensions are still thin, and the queries already run. It returns
   `{"done": bool, "reason": str, "next_query": str | null}` — the model decides
   the next query *or* that coverage is sufficient.
3. **Stop conditions** — stop when the planner returns `done: true` (or no query),
   **or** when `len(queries_run) >= RESEARCH_MAX_QUERIES`. The budget is a **hard
   cap** that always overrides the planner's wish to keep going.
4. **Search** — run the chosen query through the [`SearchClient`](components/clients.md);
   for each relevant result, run the extractor on its content (the result URL is
   the source).
5. **Reflect** — de-duplicate and accumulate the new facts, record the query, and
   loop back to **Plan**.

The **extractor** is the groundedness guard (see
[components/nodes.md](components/nodes.md) and [groundedness.md](groundedness.md)):
it emits only claims the source text supports, each with a verbatim `evidence`
snippet, and drops any candidate whose evidence is not actually found in the
source. A failed fetch or an empty search is recorded on `ResearchResult.errors`
and the loop simply moves on.

The loop is **bounded by `RESEARCH_MAX_QUERIES`** ([Configuration](configuration.md))
so cost and latency stay predictable. Open-ended exploration lives inside this
box; the box itself is wired deterministically — see
[Decisions → ADR-0003](decisions.md) and [ADR-0006](decisions.md).
