# pitch-pilot

**An SDR agent that turns a company domain into source-cited outreach — every claim
is first-party-sourced and faithfulness-judged before a human sees it, so there's no
hallucinated personalization.**

Give it a domain and pitch-pilot researches the company, qualifies it against your
Ideal Customer Profile, drafts outreach grounded only in cited facts, verifies every
claim against its source, and queues the result for human approval. **It never
auto-sends.**

_Last updated: 2026-06-14 · Full documentation: [`docs/`](docs/index.md)_

## Results

> `cerebras/gpt-oss-120b`, gate-critical calls at **temperature 0**, 2026-06-14. The
> qualifier fix ([ADR-0015](docs/decisions.md)) was developed on the development set
> (n=17) and validated on a held-out set it never saw. Every number is from
> [`docs/evals.md`](docs/evals.md), the source of truth.

**Qualifier F1 0.947 on the development set (n=17), generalizing cleanly to 8 unseen
held-out companies (4/4 qualified, 4/4 rejected).**

Primary result — qualification on the **development set (n=17)**:

| Metric (n=17) | Result |
| --- | --- |
| **F1** | **0.947** |
| Accuracy / Precision / Recall | 0.941 / 1.0 / 0.90 |
| Confusion — TP / FP / TN / FN | 9 / 0 / 7 / 1 |
| Draft-gate pass-rate | 0.778 (7 of 9 drafted) |
| Mean groundedness ¹ | 0.95 |

How it got there — before → after on the same 17 (temp-0; only the qualifier changed):

| Metric (n=17) | Before | After |
| --- | --- | --- |
| **F1** | 0.769 | **0.947** |
| Precision / Recall | 0.625 / 1.0 | 1.0 / 0.90 |
| Confusion TP/FP/TN/FN | 10/6/1/0 | 9/0/7/1 |

The fix eliminated **all six** false positives (precision 0.625 → 1.0) via a
reliably-firing negative-signal veto plus a required-industry penalty. The cost, stated
honestly: one new false negative — `ramp.com`, when the assessor flakily returned
`industry=unknown` for a real fintech (recall 1.0 → 0.90).

Generalization check — **held-out set (n=8)**, never seen while developing the fix:

| Metric (n=8) | Result |
| --- | --- |
| F1 / Precision / Recall | 1.0 / 1.0 / 1.0 |
| Confusion — TP / FP / TN / FN | 4 / 0 / 4 / 0 |
| Draft-gate pass-rate | 0.75 (3 of 4 drafted) |
| Mean groundedness ¹ | 0.958 |

All eight unseen companies landed correctly — three good-fit fintechs and a crypto
exchange qualified; two non-fintech tools, an incumbent bank, and a commerce platform
rejected. F1 1.0 on eight companies is encouraging, not conclusive — a larger held-out
set is the next step. Full provenance, the live-re-verifiability caveat, and the
draft-gate overreach finding are in [Evaluation](docs/evals.md).

¹ Groundedness = faithful body-claims ÷ total body-claims; under the strict gate it
equals the faithfulness score (same numerator) — one signal, not two.

## Demo

Verbatim CLI output (trimmed only where marked `[...]`) at the default depth —
`cerebras/gpt-oss-120b`, `RESEARCH_MAX_QUERIES=4`, gate-critical calls at temperature 0,
eval ICP (`examples/eval_icp.json`). Research is reused from the depth-4 eval cache
(deterministic); qualify/draft/verify run live, and Cerebras is not bit-deterministic
even at temperature 0, so the gate outcome is one fresh sample. (In the verification
block, `claims by source tier` counts the facts the draft was grounded in, while
`N/M verified` counts the body claims the judge audited — the two can differ.)

**1. A qualified lead that clears the gate — `checkout.com`:**

```text
PS> python -m pitch_pilot.cli run checkout.com --icp examples/eval_icp.json

Running pipeline for checkout.com (provider = cerebras, icp = examples/eval_icp.json) ...

Research: 56 grounded facts from 17 sources (4 queries).

== Qualification ==
  QUALIFIED — fit score 0.62
  Fit score 0.62 >= threshold 0.50; industry=match, size=no_match, region=match; matched 2/4 positive signal(s).
  matched: processes online payments or transactions at scale, hiring risk, fraud, or security roles

== Draft ==
  Subject: Exploring Checkout.com’s modular payments

  Hi, I’m impressed by Checkout.com’s modular approach that lets merchants use individual products such as acquiring and authentication. Modularity is clearly at the core of your payments strategy. The Unified Payments API’s single‑point integration aligns well with teams looking to streamline checkout experiences. Your built‑in fraud detection can help businesses strengthen risk strategies. Would you be open to a brief call to explore how we might complement your stack?

  Grounded hooks: Checkout.com’s modular approach lets merchants use all its products, like acquiring and authentication, separately. | Modularity is at the core of Checkout.com's approach to payments. | [...]

== Verification ==
  groundedness 1.00 (4/4 verified) · faithfulness 1.00 — PASS
  claims by source tier: own_site=4
    - tier=own_site substring_ok=yes faithfulness=faithful
      claim: Checkout.com’s modular approach lets merchants use individual products such as acquiring and authentication.
      source: https://www.checkout.com/blog/modular-payments
    - tier=own_site substring_ok=yes faithfulness=faithful
      claim: Modularity is clearly at the core of your payments strategy.
      source: https://www.checkout.com/blog/modular-payments
    - tier=own_site substring_ok=yes faithfulness=faithful
      claim: The Unified Payments API’s single‑point integration aligns well with teams looking to streamline checkout experiences.
      source: https://checkout.com
    [... 1 more faithful own_site claim ...]

== Logged ==
  outcome: ready
  written to: pitch_pilot_store.jsonl
  (pitch-pilot never auto-sends — a human approves before anything goes out.)
```

**2. The groundedness gate rejecting a bad claim — `mercury.com`:**

Mercury qualifies, and four of its draft's claims check out — but it combines two real
capabilities ("AI‑driven tools" and "instant card issuance") into a benefit the cited
facts never state: that together they "reduce time spent on money management." The judge
rates that claim `overreach`, the draft **fails**, and the lead is routed to human
review instead of `ready` — the gate catching exactly the kind of over-claim it is built
for.

```text
PS> python -m pitch_pilot.cli run mercury.com --icp examples/eval_icp.json

Running pipeline for mercury.com (provider = cerebras, icp = examples/eval_icp.json) ...

Research: 64 grounded facts from 17 sources (4 queries).

== Qualification ==
  QUALIFIED — fit score 0.83
  Fit score 0.83 >= threshold 0.50; industry=match, size=unknown, region=match; matched 2/4 positive signal(s).
  matched: processes online payments or transactions at scale, recently raised growth funding

== Draft ==
  Subject: A quick thought on Mercury’s finance stack

  I noticed Mercury provides free checking and savings accounts with zero minimums and competitive yields, plus fee‑free worldwide USD payments. With those capabilities, a tightly integrated finance stack can further automate background tasks and surface the insights your team needs. I’d love to share how our solution can complement Mercury’s AI‑driven tools and instant card issuance to reduce time spent on money management.

  Grounded hooks: Mercury offers free checking and savings accounts with zero minimums and up to 3.60% yield through Treasury by Mercury Advisory. | Mercury payments allow sending money worldwide with no fees on USD payments. | [...]

== Verification ==
  groundedness 0.80 (4/5 verified) · faithfulness 0.80 — FAIL
  claims by source tier: own_site=5
    - tier=own_site substring_ok=yes faithfulness=faithful
      claim: Mercury provides free checking and savings accounts with zero minimums and competitive yields
      source: https://mercury.com
    - tier=own_site substring_ok=yes faithfulness=faithful
      claim: Mercury provides fee‑free worldwide USD payments
      source: https://mercury.com
    [... 2 more faithful own_site claims ...]
    - tier=own_site substring_ok=yes faithfulness=overreach
      claim: Mercury’s AI‑driven tools and instant card issuance reduce time spent on money management
      source: https://mercury.com/releases
  failures:
    ❌ overreach: Mercury’s AI‑driven tools and instant card issuance reduce time spent on money management

== Logged ==
  outcome: review
  written to: pitch_pilot_store.review.jsonl
  (pitch-pilot never auto-sends — a human approves before anything goes out.)
```

## What it does

A deterministic five-step loop over a single domain:

`research → qualify → draft → verify → log`

**Research** runs an agentic, RAG-style retrieval sub-loop (the LLM plans queries →
search → fetch → extract cited facts). **Qualify** scores the company against a
declarative ICP. **Draft** writes outreach grounded only in first-party facts.
**Verify** audits that draft against its sources. **Log** files the lead for a human
as `ready`, `review` (needs edits), or `disqualified` — never sending anything.

## The differentiator — groundedness

Most "AI SDR" tools generate fluent outreach that is confidently wrong: invented
funding rounds, misattributed quotes, hallucinated headcounts. pitch-pilot makes
that structurally hard, in four layers:

1. **Extraction-time grounding.** The atomic unit of research is a typed `Fact` that
   *cannot be constructed without an `http(s)` source URL*, and the extractor keeps
   only claims whose verbatim evidence is a literal substring of the fetched page.
   An ungrounded fact is unrepresentable — not caught after the fact, but impossible.
2. **Source tiering.** Every fact is tagged `own_site` / `authoritative` /
   `third_party_snippet` by how durable and trustworthy its source is.
3. **First-party-only drafting.** Outreach may be grounded *only* in `own_site` /
   `authoritative` facts. The model selects which facts to stand on **by id**, so the
   hooks are grounded by construction — it can paraphrase freely, but it cannot
   fabricate.
4. **LLM faithfulness judge.** A judge reads the drafted body against the selected
   facts and rates every claim `faithful` / `overreach` / `unsupported`. A draft
   passes only if nothing is unsupported (and nothing overreaches, under strict mode).

The payoff is outreach you can audit sentence by sentence. Deep dive:
[Groundedness methodology](docs/groundedness.md).

## Architecture

```mermaid
flowchart LR
    domain(["domain"]) --> research["research<br/>(agentic sub-loop)"]
    research --> qualify{"qualify"}
    qualify -- "disqualified" --> log["log"]
    qualify -- "qualified" --> draft["draft"]
    draft --> verify{"verify<br/>(groundedness gate)"}
    verify -- "pass / fail" --> log
    log --> out(["outcome: ready / review / disqualified"])
```

**Hybrid by design:** a *deterministic outer graph* runs the fixed business steps in
a known, auditable order, while an *agentic sub-loop* runs inside the research step —
where open-ended exploration actually helps. (See [ADR-0003](docs/decisions.md).)

**Stack:** Python 3.11+ · **LangGraph** (outer graph) · **pydantic v2** (typed
contracts) · pluggable LLMs — **Cerebras / Groq / Gemini** (swappable behind one
interface) · Tavily search · httpx + selectolax fetch. Runs entirely on free tiers
(**$0**). More in [Architecture](docs/architecture.md) and [Pipeline](docs/pipeline.md).

## Limitations

Deliberate scope, stated plainly:

- **Small samples.** Held-out n=8, development n=17, human-proposed labels, a single
  run each. F1 1.0 on eight companies is encouraging, not conclusive — a larger
  held-out set is the next step before the generalization claim is bankable.
- **One residual qualification miss + run-to-run variance.** Making `industry=unknown`
  count against a company (the fix) costs recall when the assessor *flakily* fails to
  confirm a real fintech's industry — it cost one false negative (`ramp.com`) on the
  dev set. More broadly, Cerebras is not bit-deterministic even at temperature 0, so a
  company's draft/verdict can vary between runs; these are single runs, not averages.
- **Draft-gate overreach (noted, not fixed this pass).** The gate rejects a real share
  of *qualified* drafts (≈44% pre-fix, ≈22–25% after) — the **drafter** over-claims
  beyond the cited facts and the gate correctly catches it. Tightening the draft prompt
  is the next step ([details](docs/evals.md)).
- **Human-in-the-loop.** It never auto-sends; every lead lands in a review queue.
- **No LinkedIn scraping** — out of scope by design.
- **Lead discovery is future work** — today you supply the domain.

## Quickstart (Windows / PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env      # then add GEMINI_API_KEY + TAVILY_API_KEY (other keys optional)
python -m pitch_pilot.cli smoke  # verifies search + LLM + fetch with your keys
python -m pitch_pilot.cli run ramp.com --icp examples/eval_icp.json
```

Unit tests are fully mocked — **no keys, no network**: `pytest`. Full setup and the
Windows `.env` gotcha: [Getting Started](docs/getting-started.md).

## Documentation

- **[Full docs site](docs/index.md)** — narrative guides + an API reference
  auto-generated from docstrings (`mkdocs serve`).
- **[Groundedness methodology](docs/groundedness.md)** — the hero guarantee in depth.
- **[Evaluation](docs/evals.md)** — dataset, labeling rubric, metrics, and the numbers above.
- **[Design decisions (ADRs)](docs/decisions.md)** — why it is built this way.

## License

MIT
