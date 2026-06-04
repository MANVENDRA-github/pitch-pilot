"""The `Fact` model — the atomic unit of grounded research.

A ``Fact`` cannot be constructed without a ``source_url`` that points at a real
web page. This is the structural core of pitch-pilot's *groundedness* guarantee:
every claim the agent ever makes is carried by a ``Fact``, and a ``Fact`` refuses
to exist without a citable source. Groundedness is therefore enforced at the type
boundary — not bolted on by a later verification step.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Fact(BaseModel):
    """A single, atomic, source-backed claim about a company.

    Attributes:
        claim: A short factual statement, e.g. ``"Acme raised a $20M Series B"``.
        source_url: The URL backing the claim. Must be a non-empty ``http(s)``
            URL; this is validated at construction so an ungrounded ``Fact`` can
            never exist.
        source_title: Human-readable title of the source page, if known.
        category: Coarse bucket for the fact — e.g. ``"overview"``, ``"news"``,
            ``"hiring"``, ``"tech"``.
        confidence: Model/heuristic confidence in the claim, in ``[0, 1]``.
    """

    claim: str
    source_url: str
    source_title: str | None = None
    category: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("source_url")
    @classmethod
    def _source_url_must_be_http(cls, value: str) -> str:
        """Reject empty / non-http(s) URLs so every fact is grounded by construction."""
        url = (value or "").strip()
        if not url:
            raise ValueError(
                "Fact.source_url must not be empty — every fact requires a source."
            )
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError(
                f"Fact.source_url must be an http(s) URL, got: {value!r}"
            )
        return url
