# Graph

> **Last updated:** 2026-06-05 · **Source files:** `src/pitch_pilot/graph/`

The graph layer holds two things: the **state contract** that is threaded through every pipeline node, and the **graph assembly** that wires those nodes into the deterministic outer graph. pitch-pilot uses a hybrid architecture — a deterministic LangGraph outer graph for the fixed business steps, with an agentic ReAct research sub-loop living *inside* the research node. This page documents the state object and the graph builder. For the per-node flow see [the pipeline page](../pipeline.md); for the individual node responsibilities see the [nodes page](nodes.md).

## `PipelineState`

`PipelineState` (in `state.py`) is the single, fully-typed object passed from node to node. Each node reads the fields it needs and fills in its own slice of the state, so the artifacts accumulate as the run progresses. It is a Pydantic `BaseModel`.

The two seed inputs (`company`, `icp`) are required. The four artifact fields are `None` until their producing node runs, which makes the state safe to construct at the very start of a run with only the seed inputs:

| Field | Type | Default | Produced by | Meaning |
| --- | --- | --- | --- | --- |
| `company` | `Company` | required | seed input | The company being processed. |
| `icp` | `ICP` | required | seed input | The Ideal Customer Profile to qualify against. |
| `research` | `ResearchResult \| None` | `None` | research node | Grounded facts gathered for the company. |
| `qualification` | `QualificationResult \| None` | `None` | qualify node | The ICP verdict. |
| `draft` | `Draft \| None` | `None` | draft node | The outreach draft. |
| `verification` | `VerificationResult \| None` | `None` | verify node | The groundedness audit of the draft. |
| `status` | `str` | `"pending"` | nodes | Coarse run status. |
| `errors` | `list[str]` | `[]` (empty list) | nodes | Accumulated, non-fatal error messages. |

The `status` field is a coarse, free-form string the nodes advance as the run moves through its stages — e.g. `"pending"`, `"running"`, `"qualified"`, `"disqualified"`, `"done"`, `"error"`. The `errors` list accumulates non-fatal error messages from nodes (its default is built via `Field(default_factory=list)`, so each state instance gets its own list).

The artifact types (`ResearchResult`, `QualificationResult`, `Draft`, `VerificationResult`) and the input types (`Company`, `ICP`) are defined in the models package and documented on [the data models page](../data-models.md). Their full field listings are in the API Reference (in the nav).

### How the state is threaded

A run starts with a `PipelineState` holding only `company` and `icp`; every other artifact field is `None`. As each node runs it populates its slice, so the state fills out top to bottom:

```text
company + icp                 (seed / inputs)
    → research                (filled by the research node)
    → qualification           (filled by the qualify node)
    → draft                   (filled by the draft node)
    → verification            (filled by the verify node)
```

Because optional fields are `None` until their node runs, a downstream node can always tell whether an upstream artifact is present. The exact read/write contract for each node, and the conditional edges between them, are covered on [the pipeline page](../pipeline.md) and summarized per node on the [nodes page](nodes.md).

## `build_pipeline()`

`build_pipeline()` (in `pipeline.py`) is the entry point that assembles and compiles the deterministic LangGraph outer graph on top of `PipelineState`.

**In P0 it is a documented stub.** Calling it always raises `NotImplementedError`:

```python
from pitch_pilot.graph.pipeline import build_pipeline

build_pipeline()  # raises NotImplementedError in P0
```

The deterministic outer graph — wiring the fixed business steps `research → qualify → draft → verify → log` in a known, auditable, reproducible order — is constructed in P1. In P0 the state contract is the only graph artifact that exists. Its signature returns `Any` (a compiled LangGraph application, in P1).

The package `__init__` (`__init__.py`) documents this same split: in P0 the graph package holds only the typed state contract `PipelineState`, with `build_pipeline()` arriving in P1.

## See also

- [Pipeline](../pipeline.md) — the end-to-end node flow, conditional edges, and gates.
- [Nodes](nodes.md) — per-node read/write summaries.
- [Data models](../data-models.md) — the `Company`, `ICP`, `ResearchResult`, `QualificationResult`, `Draft`, and `VerificationResult` types referenced by the state.
- The API Reference (in the nav) — generated signatures for `PipelineState` and `build_pipeline()`.
