# Groundedness

> **Last updated:** 2026-06-05 · **Source files:** `src/pitch_pilot/models/fact.py`, `src/pitch_pilot/models/verification.py`

Groundedness is the hero feature. **No fact exists without a `source_url`, and no
claim reaches a human without being checked against one.** This page explains how
that guarantee is built and enforced.

## Why it matters

Generic "AI SDR" tools optimize for fluent volume and routinely fabricate
specifics — a funding round that never happened, a quote no one said, a headcount
off by 10×. The cost lands on the sender: burned credibility, spam complaints,
and replies that start with "we never said that."

pitch-pilot inverts the priority: **trustworthy and auditable beats voluminous.**
Every sentence in a draft should trace back to a real page, and a reviewer should
be able to click through and confirm it. That is only credible if groundedness is
*structural*, not a best-effort filter.

## Layer 1 — Groundedness by construction

The atomic unit of research is the [`Fact`](data-models.md):

```python
from pitch_pilot.models import Fact

Fact(claim="Acme raised a $20M Series B", source_url="https://acme.com/news/series-b")
# Fact(claim="Acme raised a $20M Series B", source_url="")  # ← ValidationError
```

`Fact` validates its `source_url` in the constructor: it must be non-empty and
start with `http://` or `https://`. An ungrounded fact is therefore
*unrepresentable* — you cannot build one. Because research emits `Fact`s and
drafting consumes them, every claim in the pipeline is born with a citation
attached. See [Decisions → ADR-0001](decisions.md).

## Layer 2 — Draft only from facts

The [`draft_node`](pipeline.md) composes outreach **only from grounded `Fact`
objects**. It does not free-associate from the model's parametric memory; the
hooks it uses (`Draft.hooks_used`) map back to facts, and therefore to sources.

## Layer 3 — Independent verification

After drafting, the [`verify_node`](pipeline.md) audits the draft and produces a
[`VerificationResult`](data-models.md):

| Field | Meaning |
| --- | --- |
| `total_claims` | Number of factual claims detected in the draft |
| `grounded_claims` | How many were traced to a supporting source |
| `flagged_claims` | The specific claims that could **not** be grounded |
| `groundedness_score` | Fraction grounded, in `[0, 1]` |
| `passed` | Whether the score clears the threshold |

### The score

```text
groundedness_score = grounded_claims / total_claims
```

A draft with no factual claims is trivially grounded (`score = 1.0`). The draft
**passes** when:

```text
groundedness_score ≥ GROUNDEDNESS_THRESHOLD      # default 0.9
```

`GROUNDEDNESS_THRESHOLD` is configurable ([Configuration](configuration.md)); the
default of `0.9` means at least 90% of claims must be source-backed. Any
ungrounded claim is recorded in `flagged_claims` so a reviewer sees exactly what
to check or cut.

!!! note "Status"
    The `Fact` contract (Layer 1) is implemented in P0. Claim extraction,
    source-checking, and scoring (Layer 3) are implemented in
    **[P3](roadmap.md)**; this page is the spec that implementation follows.

## What the gate does — and doesn't — do

- A **passing** draft is enqueued for human review (still never auto-sent).
- A **failing** draft is **not silently dropped**: it is logged and flagged so the
  reviewer can fix the ungrounded claims or reject it. The gate informs the human;
  it does not replace them.

## How we measure it

Groundedness rate is also the headline **evaluation** metric, measured offline
against a labeled set. See [Evaluation](evals.md).
