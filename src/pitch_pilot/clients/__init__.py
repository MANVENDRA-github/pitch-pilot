"""Swappable external-service clients (LLM, search, fetch).

Each provider sits behind a small interface so the rest of pitch-pilot stays
vendor-agnostic and the network can be mocked at the seam in tests.
"""

from __future__ import annotations

from pitch_pilot.clients.fetch import fetch_page
from pitch_pilot.clients.llm import (
    GeminiClient,
    GroqClient,
    LLMClient,
    LLMError,
    LLMJSONError,
    get_llm_client,
)
from pitch_pilot.clients.search import SearchClient, TavilyClient, get_search_client

__all__ = [
    "LLMClient",
    "GeminiClient",
    "GroqClient",
    "get_llm_client",
    "LLMError",
    "LLMJSONError",
    "SearchClient",
    "TavilyClient",
    "get_search_client",
    "fetch_page",
]
