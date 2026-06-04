# Limitations

> **Last updated:** 2026-06-05 · **Source files:** project-wide

Honest scope boundaries. Several of these are **deliberate design choices**, not
gaps to be closed — knowing the difference is part of the project's thesis.

## By design

### No auto-send — ever

pitch-pilot drafts and verifies, then **enqueues for human review**. It does not
send email, manage inboxes, or run sequences. A person approves every outbound
message. This is a feature ([ADR-0004](decisions.md)), and it caps throughput on
purpose.

### No LinkedIn (or social) scraping

LinkedIn scraping violates platform terms, invites account bans, and relies on
brittle anti-bot evasion. pitch-pilot excludes it entirely. Research draws on the
openly fetchable web only.

### Groundedness over coverage

If a claim can't be tied to a `source_url`, it is dropped, not guessed. Research
may therefore be **sparser** than a tool that fabricates plausible detail — by
design. We would rather say less and be right.

## Constraints of a $0 build

pitch-pilot is built to run on **free tiers** (Gemini/Groq for the LLM, Tavily for
search) and **no paid data brokers**. That implies:

- **Rate limits and quotas** on the free APIs bound throughput and concurrency.
- **A quality ceiling** versus paid enrichment/intent data — we trade breadth of
  signal for $0 cost and full auditability.
- **Web-only signal** — only what's publicly retrievable; no firmographic
  databases, no gated/paywalled content, and limited extraction from heavily
  JavaScript-rendered single-page apps (`fetch_page` extracts server-delivered
  HTML, not client-rendered DOM).

## Current (P0) limitations

These are **temporary**, tied to the current phase:

- **No live pipeline yet.** P0 ships the contracts and clients; `build_pipeline()`
  is a stub. Research, qualification, drafting, and verification arrive in
  [P1–P3](roadmap.md).
- **`JsonStore` only.** Persistence is a local JSON-Lines file; production
  backends (HubSpot, Sheets) and the review UI come in [P5](roadmap.md).
- **No evaluation numbers yet.** The harness and baseline land in
  [P4](roadmap.md); the [evals](evals.md) table currently shows targets only.
- **English-centric.** Prompts and extraction assume English-language sources.
- **Single-domain runs.** A run is seeded by one domain; automated discovery is a
  future seam ([P6](roadmap.md)).

## Not in scope

- An email-sending / deliverability / inbox-warmup platform.
- A CRM — pitch-pilot *writes into* one (later), it isn't one.
- A general web crawler — fetching is per-source and bounded.
