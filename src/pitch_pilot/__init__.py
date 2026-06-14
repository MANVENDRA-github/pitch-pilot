"""pitch-pilot — an autonomous SDR (Sales Development Rep) agent.

Given a single company **domain**, pitch-pilot:

    research → qualify → draft → verify → log

It researches the company, qualifies it against an Ideal Customer Profile (ICP),
drafts grounded outreach, verifies every claim against a real source, and logs
the result to a store plus a human-review queue.

Hero guarantee — **groundedness**:
    * No fact exists without a ``source_url`` (enforced at the type boundary by
      `Fact`).
    * Nothing is ever auto-sent — qualified leads land in a human-review queue.
    * LinkedIn scraping is deliberately out of scope.
"""

__version__ = "0.9.0"
