"""Persistence + human-review queue for processed leads.

pitch-pilot never auto-sends. The terminal step of the pipeline persists the lead
and *enqueues it for human review* — a person approves before anything is sent.
`Store` is the seam that lets us swap the backing store without touching
pipeline code.

P0 ships `JsonStore`, which appends to local JSON-Lines files so the
pipeline is runnable end-to-end. Production backends (HubSpot, Google Sheets, a
real review UI) implement the same `Store` protocol later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pitch_pilot.models.lead import Lead


@runtime_checkable
class Store(Protocol):
    """A persistence + review-queue backend.

    Implementations decide their own idempotency and durability guarantees; the
    pipeline only relies on these two operations.
    """

    def save_lead(self, lead: Lead) -> None:
        """Persist a processed lead."""
        ...

    def enqueue_for_review(self, lead: Lead) -> None:
        """Place a lead in the human-review queue. Nothing sends without approval."""
        ...


class JsonStore:
    """A minimal file-backed `Store` for local development.

    Writes one JSON object per line (JSON Lines). ``save_lead`` appends to the
    leads file; ``enqueue_for_review`` appends to a sibling ``*.review.jsonl``
    file. This is deliberately simple — it exists so P0 is runnable, not to be a
    production datastore.

    Args:
        path: Path to the leads file. The review queue is written next to it as
            ``<stem>.review.jsonl``.
    """

    def __init__(self, path: str | Path = "pitch_pilot_store.jsonl") -> None:
        self.path = Path(path)
        self.review_path = self.path.with_name(f"{self.path.stem}.review.jsonl")

    def save_lead(self, lead: Lead) -> None:
        """Append the lead as one JSON line to the leads file."""
        self._append(self.path, lead)

    def enqueue_for_review(self, lead: Lead) -> None:
        """Append the lead as one JSON line to the review-queue file."""
        self._append(self.review_path, lead)

    @staticmethod
    def _append(path: Path, lead: Lead) -> None:
        """Serialize ``lead`` to JSON and append it as a line to ``path``."""
        if path.parent != Path(""):
            path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(lead.model_dump_json() + "\n")
