# Architecture

> **Last updated:** 2026-06-05 · **Source files:** `src/pitch_pilot/`

pitch-pilot turns a single company **domain** into a grounded, human-reviewable
outreach draft. This page covers the system design: the hybrid execution model,
component responsibilities, the directory layout, and the data flow.

## The hybrid model: deterministic graph + agentic loop

Two execution styles, each used where it's strongest:

- **The outer pipeline is deterministic.** The business steps —
  `research → qualify → draft → verify → log` — always run in the same order with
  explicit gates. Anything that produces outbound communication must be auditable
  and reproducible, so the control flow is a fixed [LangGraph](glossary.md) state
  machine, not an open-ended agent.
- **The research step is agentic.** Inside the research node, the model runs a
  bounded [ReAct](glossary.md)-style sub-loop: it proposes search queries (capped
  by `RESEARCH_MAX_QUERIES`), reads results, fetches pages, extracts
  [`Fact`](data-models.md)s, and decides whether it has enough grounded evidence
  or should search again.

> Open-ended autonomy is confined to the one place exploration genuinely helps;
> everything around it is a predictable state machine. See
> [Decisions → ADR-0003](decisions.md).

## Component responsibilities

| Layer | Package | Responsibility | Docs |
| --- | --- | --- | --- |
| Contracts | `models/` | Typed pydantic models for every artifact; `Fact` enforces groundedness | [Data Models](data-models.md) |
| State | `graph/state.py` | `PipelineState` — the single object threaded through the graph | [Graph](components/graph.md) |
| Orchestration | `graph/pipeline.py` | Assembles the deterministic LangGraph (P1) | [Pipeline](pipeline.md) |
| Work units | `nodes/` | One node per step; reads state, fills its slice (P1+) | [Nodes](components/nodes.md) |
| External I/O | `clients/` | Swappable LLM / search / fetch behind small interfaces | [Clients](components/clients.md) |
| Configuration | `config.py` | Typed, fail-loud settings from env / `.env` | [Configuration](configuration.md) |
| Persistence | `storage/` | `Store` protocol + the human-review queue | [Storage](components/storage.md) |
| Entry point | `cli.py` | The `smoke` command (P0 acceptance gate) | [Getting Started](getting-started.md) |

Every dependency points *inward*: nodes depend on contracts and clients; clients
depend on contracts and config; contracts depend on nothing. Vendors (Gemini,
Groq, Tavily) are reachable only through the `clients/` seam, so swapping one is a
configuration change, never a pipeline change.

## Directory layout

```text
pitch-pilot/
├── src/pitch_pilot/
│   ├── config.py             # Settings + get_settings (fail-loud)
│   ├── models/               # typed contracts (one file per model, re-exported)
│   │   ├── fact.py           #   Fact — atomic, source-backed claim (the keystone)
│   │   ├── search.py         #   SearchResult
│   │   ├── icp.py            #   ICP
│   │   ├── lead.py           #   Company, Lead
│   │   ├── research.py       #   ResearchResult (+ source_count)
│   │   ├── qualification.py  #   QualificationResult
│   │   ├── draft.py          #   Draft
│   │   └── verification.py   #   VerificationResult
│   ├── clients/              # swappable external services behind one interface
│   │   ├── llm.py            #   LLMClient protocol; Gemini + Groq; factory
│   │   ├── search.py         #   SearchClient protocol; Tavily
│   │   └── fetch.py          #   fetch_page(url) -> clean text
│   ├── graph/
│   │   ├── state.py          #   PipelineState (the typed contract)
│   │   └── pipeline.py       #   build_pipeline() (LangGraph outer graph — P1)
│   ├── storage/store.py      #   Store protocol + JsonStore
│   ├── nodes/                #   pipeline nodes (P1+)
│   └── cli.py                #   `python -m pitch_pilot.cli smoke`
├── evals/                    # offline evaluation harness (P4)
├── app/                      # human-review UI (P5)
├── tests/                    # unit tests (no network)
└── docs/                     # this site
```

## Data flow

Each step fills its slice of the single typed
[`PipelineState`](components/graph.md). The optional fields are `None` until
their producing node runs, so a run starts with only the seed inputs
(`company` + `icp`).

```text
                ┌─────────┐
   domain  ───▶ │ RESEARCH│  agentic sub-loop: choose queries, search, fetch,
                └────┬────┘  extract facts — each Fact carries a source_url
                     │  ResearchResult
                     ▼
                ┌─────────┐
                │ QUALIFY │  score company vs. ICP
                └────┬────┘
                     │  QualificationResult
                     ▼
                  (gate)     disqualified ──▶ log + stop
                     │ qualified
                     ▼
                ┌─────────┐
                │  DRAFT  │  write outreach using ONLY grounded facts
                └────┬────┘
                     │  Draft
                     ▼
                ┌─────────┐
                │ VERIFY  │  check each claim against its source;
                └────┬────┘  pass only if score ≥ GROUNDEDNESS_THRESHOLD
                     │  VerificationResult
                     ▼
                ┌─────────┐
                │   LOG   │  persist lead + enqueue for HUMAN REVIEW
                └─────────┘  (never auto-sends)
```

See [Pipeline](pipeline.md) for each node's inputs, outputs, and conditional edges.

## The discovery seam

Today a run is **seeded by a domain**. The architecture leaves a clean seam for a
future `discover_node` that *produces* domains to seed runs — from an inbound
list, "companies similar to our best customers," or a market-map search.
Discovery sits in *front* of the existing pipeline and feeds it `Company`
objects; nothing downstream changes. This is why `Company(domain=...)` is the
single required input and why qualification is explicit: the same pipeline serves
one hand-picked domain today and an automated discovery stream tomorrow
([roadmap P6](roadmap.md)).

## Testing & runtime posture

- **No network in unit tests.** Vendor SDKs are imported lazily inside each
  client, so importing the package and running the test suite never touches the
  network or requires a provider to be installed.
- **Fail loud, fail early.** Missing required configuration raises a clear
  `ConfigError` at startup (see [Configuration](configuration.md)), never halfway
  through a run.
- **Never crash the pipeline on a bad page.** `fetch_page` returns `""` and logs
  on any failure rather than raising.
