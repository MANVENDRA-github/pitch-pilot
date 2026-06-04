"""Unit tests for the client layer. No network access.

These exercise the pure logic in the client modules — JSON parsing, provider
selection, and HTML→text extraction — plus the fetch error path (mocked). No real
API calls are made; the vendor SDKs are never even imported, because the clients
build them lazily.
"""

from __future__ import annotations

import pytest

from pitch_pilot.clients.fetch import _html_to_text, fetch_page
from pitch_pilot.clients.llm import (
    GeminiClient,
    GroqClient,
    LLMJSONError,
    _loads_json_lenient,
    get_llm_client,
)
from pitch_pilot.config import Settings


def _settings(**overrides) -> Settings:
    """Build a Settings object directly (no .env, no network)."""
    values = {
        "gemini_api_key": "g",
        "tavily_api_key": "t",
        "groq_api_key": None,
        "llm_provider": "gemini",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class TestJsonParsing:
    def test_plain_json_object(self):
        assert _loads_json_lenient('{"a": 1}') == {"a": 1}

    def test_json_fence_is_stripped(self):
        assert _loads_json_lenient('```json\n{"a": 1}\n```') == {"a": 1}

    def test_bare_fence_is_stripped(self):
        assert _loads_json_lenient('```\n{"a": 1}\n```') == {"a": 1}

    def test_prose_before_fence_is_tolerated(self):
        assert _loads_json_lenient('Sure, here you go:\n```json\n{"a": 1}\n```') == {"a": 1}

    def test_empty_output_raises(self):
        with pytest.raises(LLMJSONError):
            _loads_json_lenient("   ")

    def test_invalid_json_raises(self):
        with pytest.raises(LLMJSONError):
            _loads_json_lenient("not json at all")

    def test_non_object_json_raises(self):
        with pytest.raises(LLMJSONError):
            _loads_json_lenient("[1, 2, 3]")

    def test_unterminated_fence_is_tolerated(self):
        # Truncated/missing closing fence — a very common real LLM output shape.
        assert _loads_json_lenient('```json\n{"a": 1}') == {"a": 1}

    def test_bare_json_with_triple_backticks_in_value_is_preserved(self):
        raw = '{"code": "run ```x``` now"}'
        assert _loads_json_lenient(raw) == {"code": "run ```x``` now"}

    def test_prose_then_bare_object_is_recovered(self):
        assert _loads_json_lenient('Here you go: {"a": 1} cheers') == {"a": 1}


class TestLLMFactory:
    def test_selects_gemini_by_default(self):
        client = get_llm_client(_settings(llm_provider="gemini"))
        assert isinstance(client, GeminiClient)

    def test_selects_groq_when_configured(self):
        client = get_llm_client(_settings(llm_provider="groq", groq_api_key="k"))
        assert isinstance(client, GroqClient)

    def test_groq_without_key_raises(self):
        with pytest.raises(ValueError):
            get_llm_client(_settings(llm_provider="groq", groq_api_key=None))


class TestHtmlToText:
    def test_strips_scripts_and_styles(self):
        html = (
            "<html><head><style>.x{color:red}</style></head>"
            "<body><script>var x = 1;</script>"
            "<h1>Hello</h1><p>A <b>grounded</b> claim.</p></body></html>"
        )
        text = _html_to_text(html)
        assert "var x" not in text
        assert "color:red" not in text
        assert "Hello" in text
        assert "A grounded claim." in text  # inline <b> doesn't merge words

    def test_empty_html_returns_empty(self):
        assert _html_to_text("") == ""


class TestFetchPage:
    def test_returns_empty_string_on_network_error(self, monkeypatch):
        import pitch_pilot.clients.fetch as fetch_module

        def _boom(*args, **kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr(fetch_module.httpx, "get", _boom)
        assert fetch_page("https://example.com") == ""

    def test_returns_empty_string_on_http_error_status(self, monkeypatch):
        import httpx

        import pitch_pilot.clients.fetch as fetch_module

        request = httpx.Request("GET", "https://example.com/missing")
        response = httpx.Response(404, request=request)

        class _Resp:
            text = "<html><body>not found</body></html>"

            def raise_for_status(self):
                # HTTPStatusError is NOT an httpx.RequestError — this locks in that
                # the broad except still covers non-2xx responses.
                raise httpx.HTTPStatusError("404", request=request, response=response)

        monkeypatch.setattr(fetch_module.httpx, "get", lambda *a, **k: _Resp())
        assert fetch_page("https://example.com/missing") == ""

    def test_returns_empty_string_on_parse_failure(self, monkeypatch):
        import pitch_pilot.clients.fetch as fetch_module

        class _Resp:
            text = "<html><body>ok</body></html>"

            def raise_for_status(self):
                return None

        def _raise(html):
            raise ValueError("parse boom")

        monkeypatch.setattr(fetch_module.httpx, "get", lambda *a, **k: _Resp())
        monkeypatch.setattr(fetch_module, "_html_to_text", _raise)
        assert fetch_page("https://example.com") == ""

    def test_happy_path_returns_extracted_text(self, monkeypatch):
        import pitch_pilot.clients.fetch as fetch_module

        class _Resp:
            text = "<html><body><h1>Hello</h1><p>world</p></body></html>"

            def raise_for_status(self):
                return None

        monkeypatch.setattr(fetch_module.httpx, "get", lambda *a, **k: _Resp())
        result = fetch_page("https://example.com")
        assert "Hello" in result
        assert "world" in result
