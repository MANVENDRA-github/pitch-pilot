# Pipeline

> **Last updated:** 2026-06-05 · **Source files:** `src/pitch_pilot/graph/`, `src/pitch_pilot/nodes/`

The pipeline is the deterministic outer graph that orchestrates a run. It is
assembled with [LangGraph](glossary.md) on top of the typed
[`PipelineState`](components/graph.md) contract.

!!! note "Status"
    P0 ships the **state contract** and a documented `build_pipeline()` stub that
    raises `NotImplementedError`. The graph wiring and node logic land in
    **[P1](roadmap.md)**. This page is the design spec the implementation follows,
    so it is intentionally written ahead of the code.

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

### `research_node` — gather grounded facts

- **Reads:** `company`
- **Writes:** `research: ResearchResult`
- **What it does:** Runs the [agentic research sub-loop](#the-agentic-research-sub-loop)
  to produce a set of [`Fact`](data-models.md)s, each carrying a `source_url`.
- **Uses:** [`SearchClient`](components/clients.md) (Tavily) and
  [`fetch_page`](components/clients.md); the [`LLMClient`](components/clients.md)
  to plan queries and extract facts.

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
this is the one place pitch-pilot is genuinely *agentic*:

```text
  plan ──▶ search ──▶ fetch ──▶ extract facts ──▶ enough?
   ▲                                                  │ no
   └──────────────── refine query ◀───────────────────┘
                          │ yes
                          ▼
                   ResearchResult
```

1. **Plan** — propose the next search query from what's known so far.
2. **Search** — call the [`SearchClient`](components/clients.md) for results.
3. **Fetch** — pull candidate pages to clean text with
   [`fetch_page`](components/clients.md).
4. **Extract** — turn supported statements into [`Fact`](data-models.md)s, each
   bound to the `source_url` it came from.
5. **Decide** — stop when there's enough grounded evidence, or loop again.

The loop is **bounded by `RESEARCH_MAX_QUERIES`** ([Configuration](configuration.md))
so cost and latency stay predictable. Open-ended exploration lives inside this
box; the box itself is wired deterministically — see
[Decisions → ADR-0003](decisions.md).
