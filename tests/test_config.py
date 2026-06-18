"""Unit tests for configuration loading. No network access.

These tests isolate configuration by ``chdir``-ing into a temp directory (so no
real ``.env`` is read) and by clearing the relevant environment variables, then
exercising :func:`get_settings` directly.
"""

from __future__ import annotations

import os

import pytest

from pitch_pilot.config import ConfigError, get_settings

_CONFIG_ENV_VARS = (
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "TAVILY_API_KEY",
    "LLM_PROVIDER",
    "GEMINI_MODEL",
    "GROQ_MODEL",
    "RESEARCH_MAX_QUERIES",
    "GROUNDEDNESS_THRESHOLD",
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Ensure each test sees fresh settings (get_settings is lru_cached)."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _isolate(monkeypatch, tmp_path):
    """Run in a temp dir with no config env vars, so loading is deterministic.

    Clears matching env vars by case-folded name because Settings matches
    case-insensitively; deleting only the UPPERCASE names would let a lower/mixed
    -case export leak in on case-sensitive platforms (Linux/macOS CI).
    """
    monkeypatch.chdir(tmp_path)  # no .env in this directory
    targets = {var.upper() for var in _CONFIG_ENV_VARS}
    for key in list(os.environ):
        if key.upper() in targets:
            monkeypatch.delenv(key, raising=False)


def test_missing_all_required_keys_raises_clear_error(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    with pytest.raises(ConfigError) as excinfo:
        get_settings()
    message = str(excinfo.value)
    assert "GEMINI_API_KEY" in message
    assert "TAVILY_API_KEY" in message


def test_missing_one_required_key_is_named(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "present")
    # TAVILY_API_KEY still missing
    with pytest.raises(ConfigError) as excinfo:
        get_settings()
    message = str(excinfo.value)
    assert "TAVILY_API_KEY" in message
    assert "GEMINI_API_KEY" not in message  # the one that IS set isn't reported


def test_valid_settings_load_with_defaults(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("TAVILY_API_KEY", "t-key")

    settings = get_settings()

    assert settings.gemini_api_key == "g-key"
    assert settings.tavily_api_key == "t-key"
    assert settings.groq_api_key is None
    assert settings.llm_provider == "gemini"  # default
    assert settings.research_max_queries == 4  # default (eval/demo depth on Cerebras — ADR-0012)
    assert settings.research_max_page_chars == 3500  # default (token lever)
    assert settings.research_max_facts_per_source == 5  # default
    assert settings.groundedness_threshold == 0.9  # default
    assert settings.gemini_model  # has a sensible default
    assert settings.groq_model


def test_env_overrides_and_type_coercion(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("TAVILY_API_KEY", "t")
    monkeypatch.setenv("RESEARCH_MAX_QUERIES", "7")
    monkeypatch.setenv("GROUNDEDNESS_THRESHOLD", "0.75")
    monkeypatch.setenv("LLM_PROVIDER", "GROQ")  # also exercises case-normalization

    settings = get_settings()

    assert settings.research_max_queries == 7
    assert isinstance(settings.research_max_queries, int)
    assert settings.groundedness_threshold == 0.75
    assert settings.llm_provider == "groq"


def test_get_settings_is_cached(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("TAVILY_API_KEY", "t")
    assert get_settings() is get_settings()


def test_invalid_provider_raises_config_error(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("TAVILY_API_KEY", "t")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ConfigError):
        get_settings()


def test_out_of_range_threshold_raises_config_error(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("TAVILY_API_KEY", "t")
    monkeypatch.setenv("GROUNDEDNESS_THRESHOLD", "1.5")
    with pytest.raises(ConfigError):
        get_settings()
