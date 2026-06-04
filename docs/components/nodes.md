# Nodes

> **Last updated:** 2026-06-05 · **Source files:** `src/pitch_pilot/nodes/`, `src/pitch_pilot/graph/state.py`

**Status: P1+.** The `src/pitch_pilot/nodes/` package is intentionally empty in P0. Today it contains only a docstring listing the planned nodes; the implementations land in P1 and later phases.

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

## Planned nodes (P1+)

| Node | Reads | Writes | Notes |
| --- | --- | --- | --- |
| `research_node` | `company` | `research` (`ResearchResult`) | Runs the agentic research sub-loop (plan → search → fetch → extract Facts), bounded by `RESEARCH_MAX_QUERIES`. |
| `qualify_node` | `company`, `research`, `icp` | `qualification` (`QualificationResult`) | Scores the company against the ICP. A conditional edge stops disqualified leads (→ log) and sends qualified ones to draft. |
| `draft_node` | `company`, `research`, `qualification` | `draft` (`Draft`) | Writes the outreach draft using only grounded facts. |
| `verify_node` | `draft`, `research` | `verification` (`VerificationResult`) | Gate: `groundedness_score = grounded_claims / total_claims` must be `>= GROUNDEDNESS_THRESHOLD` (default `0.9`). |
| `log_node` | `company`, `research`, `qualification`, `draft`, `verification` | persisted `Lead` | Persists the lead and enqueues it for human review via the Store. Never auto-sends. |
| `discover_node` | — (seed source) | candidate domains | Future seam (P6) that sources new candidate domains to seed runs. |

## Where the full spec lives

This page is a summary of the node contracts only. The full pipeline specification — conditional edges, the disqualified-stops-here branch, the agentic research sub-loop, and the groundedness gate — is documented in **[`../pipeline.md`](../pipeline.md)**. The signatures of each node, once implemented, are generated into the API Reference (in the nav).
