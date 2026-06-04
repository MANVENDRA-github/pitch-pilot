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
        hooks_used: The angles/hooks the draft leaned on, e.g.
            ``["recent funding", "open SDR roles"]`` — useful for review and
            for tracing each hook back to a source.
    """

    subject: str
    body: str
    hooks_used: list[str] = Field(default_factory=list)
