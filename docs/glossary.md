# Glossary

> **Last updated:** 2026-06-05 · **Source files:** project-wide / conceptual

Definitions for terms used across the pitch-pilot docs. Where a term has a dedicated page, follow the link for the full treatment.

## A

**Agentic vs. Workflow**
Two execution styles pitch-pilot deliberately mixes. A *workflow* runs fixed steps in a known order with explicit gates — predictable and auditable. An *agentic* component lets the model decide its own next action in a loop. pitch-pilot keeps the outer pipeline as a deterministic workflow and confines agentic behavior to the research sub-loop, where open-ended exploration genuinely helps. See [Architecture](architecture.md).

## C

**Client / Provider**
A *client* is the small interface in `clients/` that fronts an external service: `LLMClient`, `SearchClient`, and `fetch_page`. A *provider* is a concrete vendor behind a client — Gemini or Groq for the LLM, Tavily for search. Swapping a provider is a configuration change (`LLM_PROVIDER`), never a pipeline change. See [Clients](components/clients.md).

**Conditional edge**
An edge whose target depends on the current state. pitch-pilot uses two gates: after `qualify_node`, a disqualified lead routes to `log` and stops while a qualified one proceeds to `draft`; after `verify_node`, a draft passes only if its groundedness score clears the threshold. See [Pipeline](pipeline.md).

## D

**Draft**
The outreach artifact produced by `draft_node` (the `Draft` model). It is written using *only* grounded facts pulled from the `ResearchResult`, so every claim it makes is traceable to a source before it is verified. See [Data Models](data-models.md) for the field-level contract.

**discover_node**
A future seam (P6) that sits *in front* of the pipeline and produces candidate domains to seed runs — instead of a single hand-picked domain — and feeds the existing pipeline `Company` objects. Nothing downstream changes. See [Architecture](architecture.md).

## E

**Edge**
A directed connection between two nodes in the LangGraph outer graph, defining what runs next. Most edges are unconditional; the qualify and verify gates are conditional edges. See [Graph](components/graph.md).

## F

**Fact**
The atomic, typed unit of research (the `Fact` model) and the keystone of the whole system: it **cannot be constructed without an `http(s)` source URL**. Outreach is drafted only from `Fact`s, which is what makes the system groundable and auditable. See [Data Models](data-models.md) and [Groundedness](groundedness.md).

**Fetch (`fetch_page`)**
The client function in `clients/fetch.py` that retrieves a URL and returns clean text. It never crashes the pipeline on a bad page — on any failure it returns `""` and logs rather than raising. See [Clients](components/clients.md).

## G

**Grounded / Groundedness**
*Grounded* means a claim is backed by a real `source_url`. *Groundedness* is pitch-pilot's hero feature and design thesis: no `Fact` exists without a source, drafts use only grounded facts, and every claim is re-checked against its source before a draft is allowed through. See [Groundedness](groundedness.md).

**Groundedness score**
The verification metric `groundedness_score = grounded_claims / total_claims`, in `[0, 1]`. A draft passes the verify gate only if the score is `>= GROUNDEDNESS_THRESHOLD` (default `0.9`). See [Groundedness](groundedness.md).

## H

**Hybrid architecture**
pitch-pilot's overall shape: a *deterministic outer graph* that wires the fixed business steps in a known, auditable order, plus an *agentic research sub-loop* inside the research step. Determinism where reproducibility matters; autonomy only where exploration pays off. See [Architecture](architecture.md).

## I

**ICP (Ideal Customer Profile)**
A declarative description of the kind of company you want to sell to (the `ICP` model). It is a seed input to a run and the yardstick `qualify_node` scores a company against. See [Data Models](data-models.md).

## L

**LangGraph**
The state-machine framework used to assemble the deterministic outer graph in `graph/pipeline.py`. It threads a single typed `PipelineState` through the nodes along explicit edges, including the conditional gates. See [Graph](components/graph.md).

**LLM (Large Language Model)**
The model used for the agentic research loop and for drafting. Reached through the `LLMClient` interface; the active provider is set by `LLM_PROVIDER` (default `gemini`, model `gemini-2.5-flash-lite`; `groq` with `llama-3.1-8b-instant` is the alternative). See [Clients](components/clients.md) and [Configuration](configuration.md).

## M

**MkDocs / mkdocstrings**
The documentation toolchain. *MkDocs* (Material theme) builds this site from Markdown. *mkdocstrings* auto-generates the API Reference (in the nav) from the source docstrings. The site is built with `--strict`, so broken links fail the build.

## N

**Node**
A single unit of work in the pipeline (in `nodes/`). Each node reads the shared state and fills its own slice of it. The planned nodes are `research_node`, `qualify_node`, `draft_node`, `verify_node`, and `log_node`. See [Nodes](components/nodes.md).

## O

**Outer graph**
The deterministic LangGraph that orchestrates the fixed business steps — `research → qualify → draft → verify → log` — with explicit gates. It is the "outer" layer relative to the agentic research sub-loop nested inside the research node. See [Architecture](architecture.md).

## P

**Pipeline**
The end-to-end flow `domain → research → qualify → draft → verify → log`, assembled by `build_pipeline()`. Each step fills its slice of the single `PipelineState`, which starts with only the seed inputs (`company` + `icp`). See [Pipeline](pipeline.md).

## Q

**Qualification**
The decision of whether a company fits the ICP, produced by `qualify_node` as a `QualificationResult`. It drives the first conditional gate: disqualified leads route straight to `log` and stop; qualified leads proceed to `draft`. See [Data Models](data-models.md).

## R

**ReAct**
The "Reason + Act" agent pattern the research sub-loop follows: the model reasons about what it needs, takes an action (search or fetch), observes the result, and repeats. In pitch-pilot it is bounded by `RESEARCH_MAX_QUERIES`. See [Architecture](architecture.md).

**Research sub-loop**
The agentic loop inside `research_node`: plan → search → fetch → extract `Fact`s, repeated until there is enough grounded evidence or the query budget (`RESEARCH_MAX_QUERIES`, default `4`) is exhausted. Its output is a `ResearchResult`. See [Pipeline](pipeline.md).

**Review queue**
The human-review queue that `log_node` enqueues each finished lead into via the `Store`. pitch-pilot **never auto-sends** — a human always approves outreach before it goes out. See [Storage](components/storage.md).

## S

**SDR (Sales Development Rep)**
The outbound sales role pitch-pilot automates: researching companies, qualifying them, and drafting first-touch outreach. pitch-pilot is an *autonomous SDR agent* — but one that hands off to a human at the final step.

**source_url**
The `http(s)` URL that backs a `Fact`. It is mandatory: a `Fact` cannot be constructed without one, which is the mechanism that makes the system grounded. See [Groundedness](groundedness.md).

## T

**Tavily**
The search provider behind the `SearchClient` interface (`TAVILY_API_KEY` is required). The research sub-loop uses it to find candidate pages before fetching and extracting facts. See [Clients](components/clients.md).

## V

**Verification / VerificationResult**
The check performed by `verify_node`, producing a `VerificationResult`. It re-checks each claim in the draft against its source and computes the groundedness score; the draft passes only if the score is `>= GROUNDEDNESS_THRESHOLD`. See [Groundedness](groundedness.md).
