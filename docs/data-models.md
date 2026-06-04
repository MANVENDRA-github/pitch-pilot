# Data Models

> **Last updated:** 2026-06-05 · **Source files:** `src/pitch_pilot/models/`

pitch-pilot's data contracts are a set of [pydantic](https://docs.pydantic.dev/) models — one model per artifact that flows through the pipeline. They are the typed boundary between nodes: each node reads some models off the shared state and writes others back. This page documents every model and field exactly as defined in `src/pitch_pilot/models/`.

All models are re-exported from the package root, so callers use a single import:

```python
from pitch_pilot.models import (
    Fact, SearchResult, ICP, Company, Lead,
    ResearchResult, QualificationResult, Draft, VerificationResult,
)
```

These artifacts are produced and consumed as a run moves through the [pipeline](pipeline.md), and together they make up the `PipelineState` that the outer graph threads between nodes (see [components/graph.md](components/graph.md)). For the auto-generated, always-current field detail, see the API Reference (in the nav).

## Fact

`Fact` is the atomic unit of grounded research and the keystone of the whole package. **A `Fact` cannot be constructed without a `source_url` that points at a real web page.** A `field_validator` on `source_url` strips the value and rejects it if it is empty or does not start with `http://` or `https://`, raising a `ValueError` in either case. Because validation runs at construction, an ungrounded `Fact` is unrepresentable: groundedness is enforced at the type boundary rather than bolted on by a later step. This is the structural foundation of the hero guarantee described in [groundedness.md](groundedness.md).

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `claim` | `str` | required | A short factual statement, e.g. `"Acme raised a $20M Series B"`. |
| `source_url` | `str` | required | URL backing the claim. Validated to be a non-empty `http(s)` URL; the leading/trailing whitespace is stripped. |
| `source_title` | `str \| None` | `None` | Human-readable title of the source page, if known. |
| `category` | `str \| None` | `None` | Coarse bucket for the fact, e.g. `"overview"`, `"news"`, `"hiring"`, `"tech"`. |
| `confidence` | `float` | `0.5` | Model/heuristic confidence in the claim. Constrained to `[0.0, 1.0]`. |

## SearchResult

`SearchResult` is the provider-neutral shape every `SearchClient` normalizes to (see [components/clients.md](components/clients.md)), so the rest of the pipeline never sees a vendor's raw payload. Downstream, a result's `url` becomes a `Fact.source_url`.

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `title` | `str` | required | The page/result title. |
| `url` | `str` | required | The result URL. Becomes a `source_url` downstream. |
| `content` | `str` | required | A snippet or extracted content for the result. |

## ICP

`ICP` (Ideal Customer Profile) is the declarative rubric the `qualify_node` scores a company against. It is a configuration object: every field is required, so a run is always evaluated against a fully specified profile.

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `industries` | `list[str]` | required | Target industries, e.g. `["fintech", "devtools"]`. |
| `min_employees` | `int` | required | Lower bound of the target headcount band (inclusive). |
| `max_employees` | `int` | required | Upper bound of the target headcount band (inclusive). |
| `regions` | `list[str]` | required | Target geographies, e.g. `["US", "EU"]`. |
| `positive_signals` | `list[str]` | required | Signals indicating a good fit, e.g. `["hiring SDRs", "recent funding"]`. |
| `negative_signals` | `list[str]` | required | Signals indicating a poor fit, e.g. `["non-profit", "direct competitor"]`. |

## Company

`Company` is the subject of a run. Its `domain` is the single required seed input for an entire pipeline run; `name` may be unknown at the start and resolved during research.

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `domain` | `str` | required | The company's primary domain, e.g. `"acme.com"`. The single seed input for a run. |
| `name` | `str \| None` | `None` | Display name, if known or resolved during research. |

## Lead

`Lead` wraps the `Company` a run is about. It is intentionally thin: it carries only the `Company`. The artifacts produced for it (research, qualification, draft, verification) live on the pipeline state rather than being mutated onto the `Lead` in place. The store persists the `Lead` together with those artifacts at the end of a run (see [components/storage.md](components/storage.md)).

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `company` | `Company` | required | The company this lead is about. |

## ResearchResult

`ResearchResult` holds everything the agentic research sub-loop learned about a company. Every entry in `facts` is a `Fact`, so the whole result is grounded by construction.

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `company` | `Company` | required | The company the research is about. |
| `facts` | `list[Fact]` | `[]` (empty list) | The grounded facts discovered. |
| `queries_run` | `list[str]` | `[]` (empty list) | The search queries actually executed, kept for transparency and for debugging the research loop. |

`ResearchResult` also exposes a computed read-only property:

| Property | Type | Purpose |
| --- | --- | --- |
| `source_count` | `int` | Number of **distinct** `source_url` values across all `facts`. Counts unique sources (not facts), since several facts can cite the same page. A higher count means the research draws on more independent evidence. |

## QualificationResult

`QualificationResult` is the verdict of scoring a `Company` against an `ICP`. Its `qualified` flag drives the conditional edge in the pipeline: disqualified routes straight to logging, qualified continues to drafting (see [pipeline.md](pipeline.md)).

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `qualified` | `bool` | required | Whether the company passed the qualification gate. |
| `score` | `float` | required | Fit score, constrained to `[0.0, 1.0]`. |
| `reason` | `str` | required | Short human-readable justification for the verdict. |
| `matched_signals` | `list[str]` | `[]` (empty list) | ICP signals the company satisfied. |
| `missed_signals` | `list[str]` | `[]` (empty list) | ICP signals the company failed or lacked. |

## Draft

`Draft` is the outreach email produced for a lead. It is written only from grounded `Fact` objects and is then checked by the verification step before a human reviews it. pitch-pilot never sends a `Draft` automatically.

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `subject` | `str` | required | The email subject line. |
| `body` | `str` | required | The email body. |
| `hooks_used` | `list[str]` | `[]` (empty list) | The angles/hooks the draft leaned on, e.g. `["recent funding", "open SDR roles"]` — useful for review and for tracing each hook back to a source. |

## VerificationResult

`VerificationResult` is the groundedness audit of a `Draft` and the enforcement point for the hero guarantee. A draft passes only if its `groundedness_score` clears the configured `GROUNDEDNESS_THRESHOLD` (default `0.9`; see [configuration.md](configuration.md)). The score is computed as `grounded_claims / total_claims`; details of the gate live in [groundedness.md](groundedness.md).

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `groundedness_score` | `float` | required | Fraction of claims that are grounded, constrained to `[0.0, 1.0]`. |
| `total_claims` | `int` | required | Total number of factual claims detected in the draft. |
| `grounded_claims` | `int` | required | Number of claims successfully traced to a source. |
| `flagged_claims` | `list[str]` | `[]` (empty list) | The specific claims that could **not** be grounded. |
| `passed` | `bool` | required | Whether the draft cleared the groundedness threshold. |
