# Groundedness — methodology

> **Last updated:** 2026-06-13 · **Source files:** `src/pitch_pilot/models/fact.py`, `src/pitch_pilot/nodes/research.py`, `src/pitch_pilot/nodes/draft.py`, `src/pitch_pilot/nodes/verify.py`, `src/pitch_pilot/models/verification.py`

Groundedness is the hero feature. **No fact exists without a `source_url`, and no
claim reaches a human without being first-party-sourced, substring-anchored, and
judged to be faithfully supported by its evidence.** This page is the methodology:
exactly what is checked, where, and what each reported number means — so the
headline groundedness claim is defensible rather than a vibe.

## Why it matters

Generic "AI SDR" tools optimize for fluent volume and routinely fabricate
specifics — a funding round that never happened, a quote no one said, a headcount
off by 10×. The cost lands on the sender: burned credibility, spam complaints,
and replies that start with "we never said that." pitch-pilot inverts the
priority: **trustworthy and auditable beats voluminous.** Every claim in a draft
should trace to a real page a reviewer can click through and confirm.

## The four checks

Groundedness is enforced in four layers, at two different times — three at
research/draft time, one at verification:

### Layer 1 — Extraction-time substring guard

The atomic unit of research is the [`Fact`](data-models.md), which **cannot be
constructed without a non-empty `http(s)` `source_url`** — an ungrounded fact is
unrepresentable (see [ADR-0001](decisions.md)). Beyond that, when the research
[extractor](components/nodes.md) turns page text into facts it requires a short
**verbatim `evidence` snippet** for each claim and **drops any candidate whose
evidence is not literally present in the source** (a whitespace- and
case-insensitive substring match). This filters claims the model tried to smuggle
in from prior knowledge, at the moment facts are born (see
[ADR-0007](decisions.md)).

### Layer 2 — Source tiering

The P1 live validation surfaced a second axis: how *durable* a source is. Facts
from the company's own site re-verified at 100% on a live re-fetch, while facts
grounded only against a search snippet (aggregators, blogs, news indexes) were
routinely bot-blocked or stale. So every `Fact` carries a **`source_tier`**,
assigned structurally from the URL by the research node (see
[ADR-0008](decisions.md)):

| Tier | Meaning |
| --- | --- |
| `own_site` | The company's own domain (incl. sub-pages and subdomains) |
| `authoritative` | A recognized primary source (e.g. an official filing/registry) |
| `third_party_snippet` | Grounded only against a search snippet (the default) |

**Policy B (first-party-only for claims).** A stated draft claim may rest **only**
on an `own_site` or `authoritative` fact. The [draft node](components/nodes.md)
enforces this on the way in — only first-party facts are offered as *claimable*;
`third_party_snippet` facts are passed as *context only* and can never become a
hook — and the verify node enforces it again on the way out (see
[ADR-0010](decisions.md)).

### Layer 3 — Substring re-check at verify

At verification each draft claim (a `Draft` hook) is resolved back to its backing
`Fact`, and `substring_ok` records whether that fact carries the verbatim evidence
snippet that Layer 1 proved was present in the live source. A claim whose backing
fact has no such evidence fails as `not-substring`. This re-affirms — at gate time,
without re-fetching — that the claim is anchored to verbatim source text, not just
loosely associated with a URL.

### Layer 4 — LLM faithfulness judge

Presence is not support: evidence can be a genuine verbatim snippet and still not
back the claim, or back only a weaker version of it. So the verify node runs an
**LLM judge** on every claim↔evidence pair (`judge_faithfulness`), which returns:

- **faithful** — the evidence directly supports the claim as stated;
- **overreach** — the evidence partially supports it but the claim
  generalizes/exaggerates beyond it;
- **unsupported** — the evidence does not support the claim.

The judge **fails closed**: any judge error is treated as `unsupported`. This is
the one network call the verify node makes; it does **not** re-fetch sources.

## The gate

A claim is **verified** iff *all* of the following hold:

```text
backed by a Fact  AND  tier in {own_site, authoritative}  AND  substring_ok
                  AND  faithfulness == "faithful"
                       (or "overreach" when FAITHFULNESS_STRICT is false)
```

The draft **passes only if every claim is verified.** Each failing claim is
recorded in `flagged_claims` with its specific reason — `unbacked`,
`volatile-source`, `not-substring`, `overreach`, or `unsupported` — and the full
per-claim audit trail is returned in
[`VerificationResult.claim_verdicts`](data-models.md). An empty draft (no claims)
does not pass.

## The metrics

| Metric | Definition | Where computed |
| --- | --- | --- |
| `groundedness_score` | `verified_claims / total_claims` — the fraction of claims that cleared **all four** checks. Reported even when the draft passes. | Per run, in the verify node |
| `faithfulness_score` | `faithful_claims / total_claims` — the fraction the judge rated `faithful` (an `overreach` claim that passes under lenient mode is *not* counted here). | Per run, in the verify node |
| `tier_breakdown` | Count of claims by backing source tier (e.g. `{own_site: 2}`), `unbacked` for claims with no fact. | Per run, in the verify node |
| **independent live re-verification** | For a sample of facts, re-fetch the `source_url` and re-confirm the evidence is still present on the live page — reported **by tier**. This is an **eval-time** metric (P4), measured offline against a labeled set; it is deliberately **not** in the per-run hot path (see [ADR-0010](decisions.md)). | Offline, [Evaluation](evals.md) (P4) |

## The honest headline

What pitch-pilot guarantees per run is precise and bounded: **every claim in a
passing draft is first-party-sourced, substring-anchored to verbatim evidence, and
judged faithful to that evidence.** What it does *not* claim per run is that every
cited page is still live and unchanged — *live re-verifiability* is reported
**separately, by tier, at eval time**, because the honest finding from P1 is that
first-party sources re-verify far more reliably than third-party ones. Stating the
two separately is the point: we do not launder a faithfulness number into a
durability promise.

!!! note "Status"
    Layers 1 (P1) and 2 (P2) and the draft-side of Policy B (P2) shipped earlier.
    Layer 3, Layer 4, the verify-side Policy B enforcement, and the enriched
    metrics are **P3**. The eval-time independent live re-verification is **P4**
    (see [Evaluation](evals.md)).

## What the gate does — and doesn't — do

- A **passing** draft is saved as *ready* for human approval (still never
  auto-sent).
- A **failing** draft is **not silently dropped**: it is enqueued for *review* with
  every failing claim and its reason, so a person can fix or cut it. The gate
  informs the human; it does not replace them.
