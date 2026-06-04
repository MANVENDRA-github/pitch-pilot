# Storage

> **Last updated:** 2026-06-05 · **Source files:** `src/pitch_pilot/storage/store.py`

Storage is the seam where pitch-pilot persists a finished `Lead` and places it in the **human-review queue**. It is the final step of the pipeline (`log_node`, see [../pipeline.md](../pipeline.md)) and the boundary that enforces a core promise: pitch-pilot **never auto-sends**. A person approves every lead before anything goes out (see [../limitations.md](../limitations.md)).

The layer is intentionally small. A `Store` protocol defines two operations, and the pipeline depends only on that protocol — never on a concrete backend. P0 ships one implementation, `JsonStore`, so the pipeline is runnable end-to-end against local files. Production backends arrive in P5.

## The `Store` protocol

`Store` is a `runtime_checkable` `Protocol`. Any object that provides these two methods satisfies it; there is no base class to inherit from.

| Method | Signature | Responsibility |
| --- | --- | --- |
| `save_lead` | `save_lead(self, lead: Lead) -> None` | Persist a processed lead. |
| `enqueue_for_review` | `enqueue_for_review(self, lead: Lead) -> None` | Place a lead in the human-review queue. Nothing sends without approval. |

Both methods return `None`. The protocol deliberately says nothing about idempotency or durability: implementations decide their own guarantees, and the pipeline relies only on these two operations. The `Lead` argument is the pitch-pilot domain model documented in [../data-models.md](../data-models.md).

`Store` and `JsonStore` are both re-exported from the `pitch_pilot.storage` package, so callers can import either directly from `pitch_pilot.storage`.

## The human-review queue

The two methods are separate on purpose. `save_lead` is the durable record of what the pipeline produced; `enqueue_for_review` is the work item a human picks up to approve or reject. `log_node` calls both: it persists the `Lead` and then enqueues it for review.

This split is how the no-auto-send guarantee is structured at the storage layer. The pipeline finishes by writing to a queue, not by sending an email. Approval (and any subsequent send) is an out-of-band, human-driven action that lives outside this layer.

## `JsonStore` (P0 dev store)

`JsonStore` is a minimal, file-backed implementation that writes **JSON Lines** — one JSON object per line. It exists so P0 is runnable, not to be a production datastore.

### Paths

The constructor takes a single `path` argument:

```python
JsonStore(path: str | Path = "pitch_pilot_store.jsonl")
```

| Attribute | Value | Holds |
| --- | --- | --- |
| `path` | the `path` argument (default `pitch_pilot_store.jsonl`) | leads written by `save_lead` |
| `review_path` | `<stem>.review.jsonl` next to `path` | leads written by `enqueue_for_review` |

The review queue is derived from the leads path using its stem. With the default, leads land in `pitch_pilot_store.jsonl` and the review queue in `pitch_pilot_store.review.jsonl` in the same directory.

### Write behavior

Both `save_lead` and `enqueue_for_review` delegate to a shared `_append` helper, which:

1. Creates the target file's parent directory if needed (`mkdir(parents=True, exist_ok=True)`), unless the path has no parent.
2. Opens the file in append mode (`"a"`) with **utf-8** encoding.
3. Serializes the lead with the Pydantic model's `model_dump_json()` and writes it followed by a newline.

Because writes are append-only, `JsonStore` keeps a full history rather than overwriting; it performs no de-duplication.

## Adding a backend

To swap in a different store, implement the `Store` protocol — provide `save_lead(self, lead: Lead) -> None` and `enqueue_for_review(self, lead: Lead) -> None`. No subclassing is required; `Store` is a `Protocol`, so structural typing is enough, and `runtime_checkable` means `isinstance(obj, Store)` works for the method-presence check.

A sketch of a custom backend:

```python
from pitch_pilot.models.lead import Lead


class MyStore:
    def save_lead(self, lead: Lead) -> None:
        ...  # persist the lead in your system of record

    def enqueue_for_review(self, lead: Lead) -> None:
        ...  # create a review task for a human
```

Pass an instance wherever the pipeline expects a `Store`. Pipeline code stays unchanged because it only ever calls these two methods.

## Planned production backends (P5)

P0 is the file store only. The same `Store` protocol is the target for production backends planned in P5 (Storage & review app, see [../roadmap.md](../roadmap.md)):

- **HubSpot** — persist leads into a CRM as the system of record.
- **Google Sheets** — a lightweight shared review surface.
- **A review UI** — a real human-review app for the approval workflow.

Each will implement `Store` and slot in behind the same two-method contract, so the pipeline does not need to know which backend is active.

## Related pages

- [../data-models.md](../data-models.md) — the `Lead` model that `Store` persists.
- [../pipeline.md](../pipeline.md) — `log_node`, the pipeline step that calls `save_lead` and `enqueue_for_review`.
- [../limitations.md](../limitations.md) — the no-auto-send guarantee this layer enforces.

API-level details for `Store` and `JsonStore` are in the API Reference (in the nav).
