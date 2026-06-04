"""The `ICP` model — the Ideal Customer Profile used to qualify a company."""

from __future__ import annotations

from pydantic import BaseModel


class ICP(BaseModel):
    """A declarative description of who is (and isn't) a good-fit customer.

    The ICP is the rubric the ``qualify`` step scores a company against. It is a
    configuration object — every field is required so a run is always evaluated
    against a fully-specified profile.

    Attributes:
        industries: Target industries, e.g. ``["fintech", "devtools"]``.
        min_employees: Lower bound of the target headcount band (inclusive).
        max_employees: Upper bound of the target headcount band (inclusive).
        regions: Target geographies, e.g. ``["US", "EU"]``.
        positive_signals: Signals that indicate a good fit, e.g.
            ``["hiring SDRs", "recent funding"]``.
        negative_signals: Signals that indicate a poor fit, e.g.
            ``["non-profit", "direct competitor"]``.
    """

    industries: list[str]
    min_employees: int
    max_employees: int
    regions: list[str]
    positive_signals: list[str]
    negative_signals: list[str]
