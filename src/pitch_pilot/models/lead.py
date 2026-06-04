"""The `Company` and `Lead` models — the subject of a run."""

from __future__ import annotations

from pydantic import BaseModel


class Company(BaseModel):
    """The company being researched and qualified.

    Attributes:
        domain: The company's primary domain, e.g. ``"acme.com"``. This is the
            single required seed input for an entire pipeline run.
        name: Display name, if known or resolved during research.
    """

    domain: str
    name: str | None = None


class Lead(BaseModel):
    """A lead wraps the `Company` that a run is about.

    For P0 a ``Lead`` is intentionally thin — it carries the ``Company``. The
    artifacts produced for it (research, qualification, draft, verification) live
    on the pipeline state rather than being mutated onto the ``Lead`` in place.
    The store persists the ``Lead`` together with those artifacts at the end of a
    run.
    """

    company: Company
