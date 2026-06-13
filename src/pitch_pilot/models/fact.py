"""The `Fact` model — the atomic unit of grounded research.

A ``Fact`` cannot be constructed without a ``source_url`` that points at a real
web page. This is the structural core of pitch-pilot's *groundedness* guarantee:
every claim the agent ever makes is carried by a ``Fact``, and a ``Fact`` refuses
to exist without a citable source. Groundedness is therefore enforced at the type
boundary — not bolted on by a later verification step.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

#: How much a fact's source can be trusted, used by drafting and verification.
#:
#: * ``"own_site"`` — a page on the company's own domain (the seed page or any
#:   sub-page/subdomain found later). The company speaking about itself: the most
#:   trustworthy tier, and the only one the validation harness re-verified at
#:   100%.
#: * ``"authoritative"`` — a recognized primary source (e.g. an official filing
#:   or registry). Trusted, but not the company's own site.
#: * ``"third_party_snippet"`` — anything grounded only against a search-result
#:   snippet (aggregators, blogs, news indexes). The default tier; the P1
#:   validation showed these are often unreachable or stale on live re-fetch, so
#:   downstream steps treat them as volatile (see ADR-0008).
SourceTier = Literal["own_site", "authoritative", "third_party_snippet"]


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
        evidence: A short verbatim-ish snippet (``<= 200`` chars) copied from the
            source text that supports the ``claim``. Required for facts produced
            by the research extractor, which verifies that the snippet actually
            appears in the source before constructing the fact (see the research
            node). Defaults to an empty string for facts built from structured
            data that carries no free-text excerpt.
        source_tier: How trustworthy the source is — ``"own_site"``,
            ``"authoritative"``, or ``"third_party_snippet"`` (the default). The
            research node assigns this from the source URL; drafting prefers the
            higher tiers and refuses ``"third_party_snippet"`` facts for hard
            numerics, and verification flags claims backed only by that tier as
            volatile. See `SourceTier`.
    """

    claim: str
    source_url: str
    source_title: str | None = None
    category: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: str = Field(
        default="",
        max_length=200,
        description="Short verbatim snippet from the source text supporting the claim.",
    )
    source_tier: SourceTier = Field(
        default="third_party_snippet",
        description="Trust tier of the source: own_site, authoritative, or third_party_snippet.",
    )

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
