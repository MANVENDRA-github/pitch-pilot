"""The `SearchResult` model — one hit returned by a ``SearchClient``."""

from __future__ import annotations

from pydantic import BaseModel


class SearchResult(BaseModel):
    """One result from a web-search provider.

    This is the provider-neutral shape every `SearchClient`
    normalizes to, so the rest of the pipeline never sees a vendor's raw payload.

    Attributes:
        title: The page/result title.
        url: The result URL. Downstream this becomes a `source_url`.
        content: A snippet or extracted content for the result.
    """

    title: str
    url: str
    content: str
