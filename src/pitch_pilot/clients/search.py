"""Web-search client abstraction.

A small `SearchClient` interface that normalizes any provider's payload to
our own `SearchResult`. Tavily is the P0
implementation; the vendor SDK is imported lazily so importing this module never
requires ``tavily-python`` to be installed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pitch_pilot.config import Settings, get_settings
from pitch_pilot.models.search import SearchResult


@runtime_checkable
class SearchClient(Protocol):
    """The provider-neutral search interface the pipeline depends on."""

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Run a web search and return normalized results."""
        ...


class TavilyClient:
    """`SearchClient` backed by the Tavily API (``tavily-python``).

    Tavily's ``.search()`` returns a plain dict with a ``"results"`` list whose
    items carry ``title`` / ``url`` / ``content`` — we map each to a
    `SearchResult` so callers never see the raw vendor payload.
    """

    def __init__(self, api_key: str) -> None:
        """Store the API key; the Tavily SDK client is built on first use.

        Args:
            api_key: Tavily API key (keys are prefixed ``tvly-``).
        """
        self._api_key = api_key
        self._client = None  # built lazily on first use

    def _ensure_client(self):
        """Lazily construct and cache the underlying ``tavily.TavilyClient``."""
        if self._client is None:
            # Imported lazily; aliased to avoid clashing with this wrapper's name.
            from tavily import TavilyClient as _TavilySDK

            self._client = _TavilySDK(api_key=self._api_key)
        return self._client

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Run a Tavily search and normalize the hits to `SearchResult`.

        Args:
            query: The search query.
            max_results: Maximum number of results to request (Tavily allows 0-20).

        Returns:
            A list of `SearchResult`; empty if the provider returns no results.
        """
        client = self._ensure_client()
        response = client.search(query, max_results=max_results)
        items = response.get("results", []) if isinstance(response, dict) else []
        return [
            SearchResult(
                title=item.get("title") or "",
                url=item.get("url") or "",
                content=item.get("content") or "",
            )
            for item in items
        ]


def get_search_client(settings: Settings | None = None) -> SearchClient:
    """Return the configured `SearchClient` (Tavily in P0)."""
    settings = settings or get_settings()
    return TavilyClient(api_key=settings.tavily_api_key)
