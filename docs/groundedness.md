# Groundedness — methodology

> **Last updated:** 2026-06-14 · **Source files:** `src/pitch_pilot/models/fact.py`, `src/pitch_pilot/nodes/research.py`, `src/pitch_pilot/nodes/draft.py`, `src/pitch_pilot/nodes/verify.py`, `src/pitch_pilot/models/verification.py`

Groundedness is the hero feature. **No fact exists without a `source_url`; every
fact is substring-anchored to verbatim source text at the moment it is born; an
outreach draft may be grounded only in first-party facts; and the draft's body is
independently judged faithful to those facts before it reaches a human.** This page
is the methodology: exactly what is checked, where, and what each reported number
means — so the headline groundedness claim is defensible rather than a vibe.

!!! note "0.8.0 — grounding decoupled from phrasing"
    Source-text grounding (the verbatim-substring check) lives at **extraction**,
    where a fact is matched against its source. The **draft** layer grounds outreach
    by *selecting which facts* to stand on (by id), and the **verify** layer judges
    the body's *faithfulness* to those facts. The draft no longer re-substring-checks
    (or fuzzy-matches) paraphrased hook text against the source — a brittle check
    that discarded faithful paraphrases. See [ADR-0014](decisions.md).

## Why it matters

Generic "AI SDR" tools optimize for fluent volume and routinely fabricate
specifics — a funding round that never happened, a quote no one said, a headcount
off by 10×. The cost lands on the sender: burned credibility, spam complaints,
and replies that start with "we never said that." pitch-pilot inverts the
priority: **trustworthy and auditable beats voluminous.** Every claim in a draft
should trace to a real page a reviewer can click through and confirm.

## The four checks

Groundedness is enforced in four layers, at two different times — three by
construction (extraction, tiering, draft fact-selection), one by judgement at
verification:

### Layer 1 — Extraction-time substring guard

The atomic unit of research is the [`Fact`](data-models.md), which **cannot be
constructed without a non-empty `http(s)` `source_url`** — an ungrounded fact is
unrepresentable (see [ADR-0001](decisions.md)). Beyond that, when the research
[extractor](components/nodes.md) turns page text into facts it requires a short
**verbatim `evidence` snippet** for each claim and **drops any candidate whose
evidence is not literally present in the source** (a whitespace- and
case-insensitive substring match). This filters claims the model tried to smuggle
in from prior knowledge, at the moment facts are born (see
[ADR-0007](decisions.md)). The source text is truncated to
`RESEARCH_MAX_PAGE_CHARS` **before** extraction, and the substring check runs
against that *same* truncated text — so leaning out research depth ([ADR-0012](decisions.md))
shrinks tokens without weakening the guarantee: the model can only ground claims in
the text we verify against.

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

### Layer 3 — Draft fact-selection (grounding by construction)

The [draft node](components/nodes.md) offers the model only the *claimable*
(first-party) facts, **numbered**, and the model returns the **ids** of the facts it
grounded the email in. `Draft.hooks_used` is the canonical claim text of those
selected facts. Because each id resolves to a real first-party `Fact` that already
passed Layer 1, every hook is grounded **by construction** — no substring or fuzzy
re-check of hook text is performed (that check belongs at extraction, not here). The
verify node re-resolves the hooks and treats any that fail to map to a first-party
fact as a `structural` failure (an invariant violation that should never occur).

### Layer 4 — LLM body-faithfulness judge

A grounded set of facts is necessary but not sufficient: the free-prose **body**
could still overstate or invent. So the verify node runs a single **LLM judge**
(`judge_body`) over the draft body against the selected facts. It extracts every
factual claim the body makes *about the company* and rates each:

- **faithful** — a selected fact directly supports the claim as stated;
- **overreach** — a fact partially supports it but the claim
  generalizes/exaggerates beyond it;
- **unsupported** — no selected fact supports the claim.

The judge **fails closed**: any judge error (or a malformed response) is treated as
a failure and the draft does not pass. This is the one network call the verify node
makes; it does **not** re-fetch sources.

## The gate

The draft **passes** iff *all* of the following hold:

```text
at least one grounded hook (first-party fact)  AND  non-empty body
   AND  the faithfulness judge ran successfully
   AND  no body claim is "unsupported"
        (and none is "overreach" when FAITHFULNESS_STRICT)
```

Each failing claim is recorded in `flagged_claims` with its specific reason —
`structural`, `overreach`, `unsupported`, or `judge-error` — and the full
per-body-claim audit trail is returned in
[`VerificationResult.claim_verdicts`](data-models.md). An ungrounded or empty-body
draft does not pass.

## The metrics

| Metric | Definition | Where computed |
| --- | --- | --- |
| `groundedness_score` | `verified_claims / total_body_claims` — the fraction of body claims that count as verified, where verified means the judge rated the claim `faithful` (or `overreach` when `FAITHFULNESS_STRICT` is off). Under the default strict mode this equals `faithful_claims / total_body_claims`. `1.0` for a grounded body with no checkable company claim; `0.0` for an ungrounded or empty-body draft. | Per run, in the verify node |
| `faithfulness_score` | `faithful_claims / total_body_claims` — the fraction of body claims the judge rated `faithful` (an `overreach` claim that passes under lenient mode is *not* counted here). | Per run, in the verify node |
| `tier_breakdown` | Count of the draft's **grounding facts** (hooks) by source tier, e.g. `{own_site: 2}`. | Per run, in the verify node |
| **independent live re-verification** | For each used grounding fact, re-fetch the `source_url` and re-confirm the evidence is still present on the live page — reported **by tier**. This is an **eval-time** metric (P4), measured offline against a labeled set; it is deliberately **not** in the per-run hot path (see [ADR-0010](decisions.md)). | Offline, [Evaluation](evals.md) (P4) |

## The honest headline

What pitch-pilot guarantees per run is precise and bounded: **a passing draft is
grounded only in first-party facts that are each substring-anchored to verbatim
source text (at extraction), and every factual claim its body makes about the
company is judged faithful to those facts.** What it does *not* claim per run is that
every cited page is still live and unchanged — *live re-verifiability* is reported
**separately, by tier, at eval time**, because the honest finding from P1 is that
first-party sources re-verify far more reliably than third-party ones. Stating the
two separately is the point: we do not launder a faithfulness number into a
durability promise.

!!! note "Status"
    Layers 1 (P1) and 2 (P2) and the draft-side of Policy B (P2) shipped earlier.
    The original Layer 4 faithfulness judge, verify-side Policy B enforcement, and
    enriched metrics were **P3**. The eval-time independent live re-verification is
    **P4**. **0.8.0** decoupled grounding from phrasing: draft grounding became
    fact-selection (Layer 3), the faithfulness judge moved to the draft **body**
    against the selected facts (Layer 4), and the brittle draft-time substring
    re-check was removed (see [ADR-0014](decisions.md)).

## What the gate does — and doesn't — do

- A **passing** draft is saved as *ready* for human approval (still never
  auto-sent).
- A **failing** draft is **not silently dropped**: it is enqueued for *review* with
  every failing claim and its reason, so a person can fix or cut it. The gate
  informs the human; it does not replace them.
