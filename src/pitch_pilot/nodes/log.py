"""The log node — the pipeline's terminal step. It never sends.

This is where a run ends: the lead and its artifacts are persisted, and — the
whole point of pitch-pilot — a human is left in the loop. **Nothing is ever sent
automatically.** The node only decides *where* the lead lands:

* **disqualified** — the company failed the ICP gate (it never reached drafting).
  Saved to the leads store as a record; there is nothing to review.
* **ready** — qualified and the draft passed verification. Saved to the leads
  store, marked ready for a human to approve and send.
* **review** — qualified but the draft did **not** pass verification (an unbacked
  claim, or groundedness below threshold). Enqueued for human review rather than
  marked ready, so a person looks before anything goes out.

The single terminal node decides the outcome from the state it is handed, which is
why both pipeline branches (the disqualified path and the verified path) route
here.
"""

from __future__ import annotations

import logging

from pitch_pilot.graph.state import PipelineState
from pitch_pilot.models.lead import Lead
from pitch_pilot.storage.store import JsonStore, Store

logger = logging.getLogger(__name__)


def _outcome(state: PipelineState) -> str:
    """Decide the terminal outcome (``disqualified`` / ``ready`` / ``review``)."""
    qualification = state.qualification
    if qualification is None or not qualification.qualified:
        return "disqualified"
    verification = state.verification
    if verification is not None and verification.passed:
        return "ready"
    return "review"


def log_lead(state: PipelineState, store: Store) -> dict:
    """Persist the lead + artifacts and route it by outcome. Never sends.

    Builds a self-contained `Lead` from the final state and writes it via the
    `Store`: a ``review`` outcome is enqueued for human review, everything else is
    saved to the leads store. The verdict mirrors the pipeline routing (see the
    module docstring).

    Args:
        state: The final pipeline state.
        store: The store to persist into / enqueue onto.

    Returns:
        A dict ``{"status": <outcome>}`` to merge into the state.
    """
    status = _outcome(state)
    lead = Lead(
        company=state.company,
        qualification=state.qualification,
        draft=state.draft,
        verification=state.verification,
        status=status,
    )
    # pitch-pilot never auto-sends: a qualified+passed lead is only *marked* ready
    # for a human to approve; an unverified one is queued for a human to inspect.
    if status == "review":
        store.enqueue_for_review(lead)
    else:
        store.save_lead(lead)
    logger.info("logged lead %s as %s", state.company.domain, status)
    return {"status": status}


def log_node(state: PipelineState, *, store: Store | None = None) -> dict:
    """Graph adapter: persist the lead and enqueue for review when not ready.

    The store defaults to a local `JsonStore` but can be injected so the pipeline
    can run against a fake store with no filesystem writes.

    Args:
        state: The final pipeline state.
        store: The store to use; a default `JsonStore` when omitted.

    Returns:
        A dict ``{"status": <outcome>}`` to merge into the state.
    """
    store = store or JsonStore()
    return log_lead(state, store)
