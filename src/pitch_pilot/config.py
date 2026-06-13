"""Typed application configuration for pitch-pilot.

Configuration is read from environment variables (and an optional ``.env`` file)
into a single, validated `Settings` object. Required keys that are missing
cause a loud, explicit `ConfigError` that names exactly what to set — we
fail at startup, never halfway through a run.

Use `get_settings` (cached) everywhere instead of constructing ``Settings``
directly.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_VALID_PROVIDERS = {"gemini", "groq"}


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid, with a human-readable message."""


class Settings(BaseSettings):
    """All configuration for a pitch-pilot run, validated at load time.

    Values come from environment variables or a local ``.env`` file. Matching is
    case-insensitive (``GEMINI_API_KEY`` populates ``gemini_api_key``), and real
    environment variables take precedence over the ``.env`` file.

    Attributes:
        gemini_api_key: Google Gen AI (Gemini) API key. **Required.**
        tavily_api_key: Tavily search API key. **Required.**
        groq_api_key: Groq API key. Required only when ``llm_provider`` is ``"groq"``.
        llm_provider: Active LLM provider — ``"gemini"`` (default) or ``"groq"``.
        gemini_model: Gemini model id (default ``gemini-2.5-flash-lite``).
        groq_model: Groq model id (default ``llama-3.1-8b-instant``).
        research_max_queries: Max search queries per research run (``>= 1``, default 4).
        qualify_threshold: Minimum fit score, in ``[0, 1]``, for a company to
            qualify against the ICP (default 0.5). A matched negative signal vetoes
            qualification regardless of this score.
        groundedness_threshold: Minimum groundedness score, in ``[0, 1]``, for a
            draft to pass verification (default 0.9). With first-party-only
            enforcement (P3) a passing draft scores 1.0, so this is effectively a
            floor; it is kept for transparency and future tuning.
        faithfulness_strict: When ``True`` (default), the verification gate treats
            an ``"overreach"`` faithfulness verdict as a failure; when ``False``,
            only ``"unsupported"`` fails. ``"unsupported"`` always fails.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Required credentials (no default → a missing one fails loudly) ---
    gemini_api_key: str = Field(..., description="Google Gen AI (Gemini) API key. Required.")
    tavily_api_key: str = Field(..., description="Tavily search API key. Required.")

    # --- Optional credentials ---
    groq_api_key: str | None = Field(
        default=None, description="Groq API key. Required only when LLM_PROVIDER=groq."
    )

    # --- Provider + model selection ---
    llm_provider: str = Field(default="gemini", description='Active LLM provider: "gemini" or "groq".')
    gemini_model: str = Field(default="gemini-2.5-flash-lite", description="Gemini model id.")
    groq_model: str = Field(default="llama-3.1-8b-instant", description="Groq model id.")

    # --- Tunables ---
    research_max_queries: int = Field(
        default=4, ge=1, description="Max number of search queries per research run."
    )
    qualify_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Minimum fit score for a company to qualify against the ICP."
    )
    groundedness_threshold: float = Field(
        default=0.9, ge=0.0, le=1.0, description="Minimum groundedness score for a draft to pass verification."
    )
    faithfulness_strict: bool = Field(
        default=True,
        description="When true, an 'overreach' faithfulness verdict fails the gate; when false, only 'unsupported' fails.",
    )

    @field_validator("llm_provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _VALID_PROVIDERS:
            raise ValueError(
                f"LLM_PROVIDER must be one of {sorted(_VALID_PROVIDERS)}, got {value!r}."
            )
        return normalized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the validated, process-wide cached `Settings`.

    Raises:
        ConfigError: if a required key is missing or a value is invalid. The
            message names the offending environment variable(s). Call
            ``get_settings.cache_clear()`` to force a reload (used in tests).
    """
    try:
        return Settings()
    except ValidationError as exc:
        missing = sorted(
            {
                str(err["loc"][0]).upper()
                for err in exc.errors()
                if err.get("type") == "missing"
            }
        )
        if missing:
            raise ConfigError(
                "Missing required configuration: "
                + ", ".join(missing)
                + ". Set them as environment variables or in a .env file "
                "(copy .env.example to .env and fill in your keys)."
            ) from exc
        details = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']).upper()}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"Invalid configuration — {details}") from exc
