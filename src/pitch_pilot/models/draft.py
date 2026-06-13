"""The `Draft` model — the outreach message produced for a lead."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Draft(BaseModel):
    """A drafted outreach email. Always grounded; never auto-sent.

    A ``Draft`` is written only from grounded `Fact`
    objects and is then checked by the verification step before a human reviews
    it. pitch-pilot never sends a ``Draft`` automatically.

    Attributes:
        subject: The email subject line.
        body: The email body.
        hooks_used: The canonical claim text of the first-party `Fact` objects the
            draft was grounded in — the facts the model selected by id, e.g.
            ``["Acme raised a $20M Series B", "Acme is hiring SDRs"]``. Grounded by
            construction (each is a real own_site/authoritative fact), so each hook
            traces straight back to a source.
    """

    subject: str
    body: str
    hooks_used: list[str] = Field(default_factory=list)
