# Data Models

> **Last updated:** 2026-06-13 · **Source files:** `src/pitch_pilot/models/`

pitch-pilot's data contracts are a set of [pydantic](https://docs.pydantic.dev/) models — one model per artifact that flows through the pipeline. They are the typed boundary between nodes: each node reads some models off the shared state and writes others back. This page documents every model and field exactly as defined in `src/pitch_pilot/models/`.

All models are re-exported from the package root, so callers use a single import:

```python
from pitch_pilot.models import (
    Fact, SearchResult, ICP, Company, Lead,
    ResearchResult, QualificationResult, Draft, VerificationResult, ClaimVerdict,
)
```

These artifacts are produced and consumed as a run moves through the [pipeline](pipeline.md), and together they make up the `PipelineState` that the outer graph threads between nodes (see [components/graph.md](components/graph.md)). For the auto-generated, always-current field detail, see the API Reference (in the nav).

## Fact

`Fact` is the atomic unit of grounded research and the keystone of the whole package. **A `Fact` cannot be constructed without a `source_url` that points at a real web page.** A `field_validator` on `source_url` strips the value and rejects it if it is empty or does not start with `http://` or `https://`, raising a `ValueError` in either case. Because validation runs at construction, an ungrounded `Fact` is unrepresentable: groundedness is enforced at the type boundary rather than bolted on by a later step. This is the structural foundation of the hero guarantee described in [groundedness.md](groundedness.md).

Facts produced by the [research node](components/nodes.md) also carry an `evidence` snippet — a short verbatim excerpt from the source text that supports the claim. The extractor verifies the snippet actually appears in the source before building the `Fact`, so `evidence` is the anchor for the substring grounding check (see [groundedness.md](groundedness.md)).

Each fact is also tagged with a **`source_tier`** that records how trustworthy its source is. This is set by the research node from the source URL and is consumed downstream: drafting prefers the higher tiers and refuses `third_party_snippet` facts for hard numerics, and verification flags claims backed only by that tier as *volatile*. See [Groundedness → source tiers](groundedness.md) and [Decisions → ADR-0008](decisions.md).

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `claim` | `str` | required | A short factual statement, e.g. `"Acme raised a $20M Series B"`. |
| `source_url` | `str` | required | URL backing the claim. Validated to be a non-empty `http(s)` URL; the leading/trailing whitespace is stripped. |
| `source_title` | `str \| None` | `None` | Human-readable title of the source page, if known. |
| `category` | `str \| None` | `None` | Coarse bucket for the fact, e.g. `"overview"`, `"news"`, `"hiring"`, `"tech"`. |
| `confidence` | `float` | `0.5` | Model/heuristic confidence in the claim. Constrained to `[0.0, 1.0]`. |
| `evidence` | `str` | `""` (empty) | Short verbatim snippet (`<= 200` chars) from the source text supporting the `claim`. Populated for facts produced by the research extractor, which drops any candidate whose evidence is not found in the source. |
| `source_tier` | `Literal` | `"third_party_snippet"` | Trust tier of the source. One of `"own_site"` (the company's own domain, incl. sub-pages/subdomains), `"authoritative"` (a recognized primary source), or `"third_party_snippet"` (search-snippet sources; the conservative default). |

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

`Lead` is the `Company` plus the artifacts a run produced for it — it is what the [`Store`](components/storage.md) persists at the end of a run. The artifact fields are optional because a lead can be logged at different stages: a disqualified company carries only its `qualification`, while a fully processed one carries the `draft` and `verification` too. During the run these same artifacts live on the [`PipelineState`](components/graph.md); the [`log_node`](pipeline.md) copies the final ones onto the `Lead` so the persisted record is self-contained.

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `company` | `Company` | required | The company this lead is about. |
| `qualification` | `QualificationResult \| None` | `None` | The ICP verdict, if the qualify node ran. |
| `draft` | `Draft \| None` | `None` | The outreach draft, if the draft node ran. |
| `verification` | `VerificationResult \| None` | `None` | The groundedness audit, if the verify node ran. |
| `status` | `str` | `"pending"` | Terminal outcome — `"ready"`, `"review"`, or `"disqualified"`. |

## ResearchResult

`ResearchResult` holds everything the agentic research sub-loop learned about a company. Every entry in `facts` is a `Fact`, so the whole result is grounded by construction.

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `company` | `Company` | required | The company the research is about. |
| `facts` | `list[Fact]` | `[]` (empty list) | The grounded facts discovered. |
| `queries_run` | `list[str]` | `[]` (empty list) | The search queries actually executed, kept for transparency and for debugging the research loop. |
| `errors` | `list[str]` | `[]` (empty list) | Non-fatal problems hit during research (a failed fetch, an empty search, an extraction error). The research node records these and continues rather than crashing. |

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

`Draft` is the outreach email produced for a lead. The [`draft_node`](pipeline.md) writes it only from grounded `Fact` objects, and every hook it claims to use is validated back against the facts — so `hooks_used` is always a subset of the real research facts. Hard-numeric claims (funding, headcount, …) from `third_party_snippet` facts are withheld from drafting entirely. It is then checked by the verification step before a human reviews it. pitch-pilot never sends a `Draft` automatically.

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `subject` | `str` | required | The email subject line. |
| `body` | `str` | required | The email body. |
| `hooks_used` | `list[str]` | `[]` (empty list) | The exact `Fact` claims the draft referenced — each traces back to a grounded source. Validated against the research facts, so a hook the model invents is discarded. |

## VerificationResult

`VerificationResult` is the groundedness audit of a `Draft` and the enforcement point for the hero guarantee. The [`verify_node`](pipeline.md) checks each claim (the draft's `hooks_used`) and **passes the draft only if every claim is *verified*** — backed by a first-party (`own_site`/`authoritative`) `Fact`, substring-anchored, and judged `faithful` by the LLM (or `overreach` when `FAITHFULNESS_STRICT` is off). The full methodology, and how each score is defined, lives in [groundedness.md](groundedness.md).

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `groundedness_score` | `float` | required | Fraction of claims fully verified (`grounded_claims / total_claims`), in `[0.0, 1.0]`. Reported even when the draft passes. |
| `faithfulness_score` | `float` | `0.0` | Fraction of claims the judge rated `faithful` (`faithful_claims / total_claims`), in `[0.0, 1.0]`. |
| `total_claims` | `int` | required | Total number of claims checked (the draft's hooks). |
| `grounded_claims` | `int` | required | Number of claims that are fully verified (the numerator of `groundedness_score`). |
| `tier_breakdown` | `dict[str, int]` | `{}` | Count of claims per backing source tier, e.g. `{"own_site": 2, "unbacked": 1}`. |
| `claim_verdicts` | `list[ClaimVerdict]` | `[]` (empty list) | The per-claim audit trail (see `ClaimVerdict` below). |
| `flagged_claims` | `list[str]` | `[]` (empty list) | Failure lines for claims that did not verify, each prefixed with the reason: `unbacked:` / `volatile-source:` / `not-substring:` / `overreach:` / `unsupported:`. |
| `passed` | `bool` | required | True only if there is at least one claim and **every** claim is verified. |

## ClaimVerdict

`ClaimVerdict` is the per-claim audit trail produced by the verify node — one per claim the draft stands on, pass or fail — so a reviewer can see exactly why each claim was accepted or rejected.

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `claim` | `str` | required | The draft claim under audit (a `Draft` hook). |
| `fact_used` | `str \| None` | `None` | The claim text of the backing `Fact` chosen to support it; `None` if unbacked. |
| `source_url` | `str \| None` | `None` | The backing fact's source URL; `None` if unbacked. |
| `tier` | `str \| None` | `None` | The backing fact's source tier; `None` if unbacked. |
| `substring_ok` | `bool` | `False` | Whether the backing fact carries a verbatim `evidence` snippet (the extraction-time substring guard held). |
| `faithfulness` | `Literal \| None` | `None` | The judge verdict (`faithful`/`overreach`/`unsupported`), or `None` when the claim failed an earlier check and was not judged. |
