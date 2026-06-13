# Pipeline

> **Last updated:** 2026-06-13 · **Source files:** `src/pitch_pilot/graph/`, `src/pitch_pilot/nodes/`

The pipeline is the deterministic outer graph that orchestrates a run. It is
assembled with [LangGraph](glossary.md) on top of the typed
[`PipelineState`](components/graph.md) contract.

!!! note "Status"
    **Implemented end-to-end in [P2](roadmap.md).** `build_pipeline()` wires all
    five nodes (`research` → `qualify` → `draft` → `verify` → `log`) over
    `PipelineState` with the two conditional gates below, and the
    `python -m pitch_pilot.cli run <domain>` command runs the whole thing. The
    `research_node` and its [agentic sub-loop](#the-agentic-research-sub-loop) were
    delivered in [P1](roadmap.md), and [P3](roadmap.md) hardened the `verify` node
    into the real groundedness gate (first-party tier + substring + LLM faithfulness
    judge — see [Groundedness](groundedness.md)). The graph shape is stable.

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

### `qualify_node` — score against the ICP ✅ implemented (P2)

- **Reads:** `research`, `icp`
- **Writes:** `qualification: QualificationResult`; sets `status` to `qualified` /
  `disqualified`.
- **What it does:** A **hybrid** judgement — the LLM semantically matches each ICP
  attribute/signal against the facts (`match`/`no_match`/`unknown`, citing a fact),
  then deterministic code computes a weighted fit score, applies a **hard veto** on
  any matched negative signal, and decides against `QUALIFY_THRESHOLD`. Unknowns are
  never guessed. Records `matched_signals`, `missed_signals`, and a `reason`. See
  [Decisions → ADR-0009](decisions.md).
- **Conditional edge:** if `qualified` is `False` → **`log_node`** (stop early);
  if `True` → **`draft_node`**.

### `draft_node` — write grounded outreach ✅ implemented (P2)

- **Reads:** `research`, `qualification`
- **Writes:** `draft: Draft`
- **What it does:** Composes a subject + body **only from grounded facts**.
  Hard-numeric claims from `third_party_snippet` facts are withheld, and every hook
  the model returns is validated back against the facts — so `hooks_used` is always
  a subset of the research facts. See [Groundedness → Layer 3](groundedness.md).

### `verify_node` — the groundedness gate ✅ hardened (P3)

- **Reads:** `draft`, `research`
- **Writes:** `verification: VerificationResult`
- **What it does:** Audits each claim (the draft's `hooks_used`) through four
  checks — backed, first-party tier (Policy B), substring-anchored, and an **LLM
  faithfulness judge**. The draft **passes only if every claim verifies.** Failures
  are recorded by reason (`unbacked` / `volatile-source` / `not-substring` /
  `overreach` / `unsupported`) with a per-claim audit trail. Network-free except
  the judge call. See the [Groundedness methodology](groundedness.md).
- **Edge:** always proceeds to `log_node`, which decides the outcome from the
  verification verdict.

### `log_node` — persist and queue for review ✅ implemented (P2)

- **Reads:** the whole `PipelineState`
- **Writes:** persistence side effects; sets the terminal `status`.
- **What it does:** Builds a self-contained [`Lead`](data-models.md) (company +
  qualification + draft + verification) and routes it via the
  [`Store`](components/storage.md): a passing draft is **saved as `ready`**, a
  failing one is **enqueued for `review`**, and a disqualified company is saved as
  `disqualified`. pitch-pilot **never auto-sends** — a human approves first.

### `discover_node` — future seam

A future node ([P6](roadmap.md)) that *produces* candidate domains to seed runs
(inbound lists, look-alikes, market maps). It sits in front of `research_node`
and emits `Company` objects; nothing downstream changes.

## Conditional edges (gates)

| Gate | Condition | True | False |
| --- | --- | --- | --- |
| Qualification (graph edge) | `qualification.qualified` | → `draft_node` | → `log_node` (logged `disqualified`) |
| Groundedness (in `log_node`) | `verification.passed` | saved as `ready` | enqueued for `review` |

The qualification gate is a real conditional edge in the graph. The groundedness
gate is realized inside the single `log_node` (both the draft and verify paths lead
there, and it decides `ready` vs `review` from `verification.passed`) — equivalent
to a conditional edge but keeping one terminal node. Both gates are deterministic
functions of the typed state, which keeps the run auditable.

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
