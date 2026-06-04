"""LLM client abstraction.

One small interface — `LLMClient` — with two operations:

    * ``complete(system, user) -> str``       — free-text completion
    * ``complete_json(system, user) -> dict``  — a parsed JSON object

It is implemented by `GeminiClient` (default) and `GroqClient`,
selected at runtime by `get_llm_client` based on ``LLM_PROVIDER``.

The concrete vendor SDKs are imported *lazily* inside each client, so importing
this module never requires a provider package to be installed and the pure
parsing/selection logic can be unit-tested without touching the network.
"""

from __future__ import annotations

import json
import re
from typing import Protocol, runtime_checkable

from pitch_pilot.config import Settings, get_settings

_JSON_INSTRUCTION = (
    "Respond with a single valid JSON object and nothing else — no prose and no "
    "markdown code fences."
)


class LLMError(RuntimeError):
    """Base error for LLM client failures."""


class LLMJSONError(LLMError):
    """Raised when an LLM response cannot be parsed as a JSON object."""


@runtime_checkable
class LLMClient(Protocol):
    """The provider-neutral LLM interface the pipeline depends on."""

    def complete(self, system: str, user: str) -> str:
        """Return a free-text completion for the given system + user prompts."""
        ...

    def complete_json(self, system: str, user: str) -> dict:
        """Return a parsed JSON object. Raises `LLMJSONError` on bad JSON."""
        ...


def _json_system(system: str) -> str:
    """Append an explicit 'output JSON' instruction (required by Groq's JSON mode)."""
    return f"{(system or '').strip()}\n\n{_JSON_INSTRUCTION}".strip()


def _extract_json_object(text: str) -> dict | None:
    """Best-effort recovery of a JSON object embedded in noisy text.

    Tries, in order: the contents of a Markdown fenced code block, then the
    widest ``{...}`` span. Returns the first that parses to a ``dict``, else
    ``None``.
    """
    candidates: list[str] = []
    fenced = re.search(r"```[a-zA-Z0-9_+-]*\s*(.*?)```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _loads_json_lenient(text: str) -> dict:
    """Parse a JSON object from an LLM response, tolerating Markdown code fences.

    Robust to the shapes LLMs actually emit: bare JSON, a fenced JSON block
    (even one whose closing fence was truncated), or a JSON object preceded by
    prose. Bare JSON whose *values* contain triple backticks is left intact,
    because a leading fence is only stripped when the response starts with one.

    Args:
        text: The raw LLM output.

    Returns:
        The parsed JSON object.

    Raises:
        LLMJSONError: if the text is empty, not valid JSON, or not a JSON object.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise LLMJSONError("LLM returned empty output; expected a JSON object.")

    candidate = cleaned
    if candidate.startswith("```"):
        # Strip the opening fence (and optional language tag) and a closing fence
        # if present — handled independently so an unterminated fence still parses,
        # and anchored to the start so backticks inside values are never touched.
        candidate = re.sub(r"^```[a-zA-Z0-9_+-]*", "", candidate)
        candidate = re.sub(r"```\s*$", "", candidate).strip()

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        recovered = _extract_json_object(cleaned)
        if recovered is not None:
            return recovered
        raise LLMJSONError(
            f"LLM did not return valid JSON ({exc}). Raw output: {text!r}"
        ) from exc

    if not isinstance(data, dict):
        raise LLMJSONError(
            f"Expected a JSON object, got {type(data).__name__}: {data!r}"
        )
    return data


class GeminiClient:
    """`LLMClient` backed by the official Google Gen AI SDK (``google-genai``).

    Uses the current client-centric API: a single ``genai.Client`` with the model
    id passed per call and the system prompt supplied via
    ``types.GenerateContentConfig(system_instruction=...)``.
    """

    def __init__(self, api_key: str, model: str) -> None:
        """Store credentials and model id; the SDK client is built on first use.

        Args:
            api_key: Google Gen AI API key.
            model: Gemini model id to call (e.g. ``gemini-2.5-flash-lite``).
        """
        self._api_key = api_key
        self._model = model
        self._client = None  # built lazily on first use

    def _ensure_client(self):
        """Lazily construct and cache the underlying ``genai.Client``."""
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def complete(self, system: str, user: str) -> str:
        """Return a free-text completion.

        Args:
            system: System instruction (role/behavior).
            user: User prompt (the request).

        Returns:
            The model's text response, stripped of surrounding whitespace.
        """
        from google.genai import types

        client = self._ensure_client()
        response = client.models.generate_content(
            model=self._model,
            contents=user,
            config=types.GenerateContentConfig(system_instruction=system),
        )
        return (response.text or "").strip()

    def complete_json(self, system: str, user: str) -> dict:
        """Return a parsed JSON object using Gemini's JSON output mode.

        Sets ``response_mime_type="application/json"`` and parses the result
        leniently (tolerating Markdown code fences).

        Args:
            system: System instruction.
            user: User prompt.

        Returns:
            The parsed JSON object.

        Raises:
            LLMJSONError: if the response is not a valid JSON object.
        """
        from google.genai import types

        client = self._ensure_client()
        response = client.models.generate_content(
            model=self._model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=_json_system(system),
                response_mime_type="application/json",
            ),
        )
        return _loads_json_lenient(response.text or "")


class GroqClient:
    """`LLMClient` backed by the official Groq SDK (OpenAI-compatible API)."""

    def __init__(self, api_key: str, model: str) -> None:
        """Store credentials and model id; the SDK client is built on first use.

        Args:
            api_key: Groq API key.
            model: Groq model id to call (e.g. ``llama-3.1-8b-instant``).
        """
        self._api_key = api_key
        self._model = model
        self._client = None  # built lazily on first use

    def _ensure_client(self):
        """Lazily construct and cache the underlying ``groq.Groq`` client."""
        if self._client is None:
            from groq import Groq

            self._client = Groq(api_key=self._api_key)
        return self._client

    def complete(self, system: str, user: str) -> str:
        """Return a free-text completion.

        Args:
            system: System message (role/behavior).
            user: User message (the request).

        Returns:
            The model's text response, stripped of surrounding whitespace.
        """
        client = self._ensure_client()
        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return (response.choices[0].message.content or "").strip()

    def complete_json(self, system: str, user: str) -> dict:
        """Return a parsed JSON object using Groq's JSON mode.

        Uses ``response_format={"type": "json_object"}`` and injects an explicit
        "respond with JSON" instruction (which Groq's JSON mode requires).

        Args:
            system: System message.
            user: User message.

        Returns:
            The parsed JSON object.

        Raises:
            LLMJSONError: if the response is not a valid JSON object.
        """
        client = self._ensure_client()
        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _json_system(system)},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        return _loads_json_lenient(response.choices[0].message.content or "")


def get_llm_client(settings: Settings | None = None) -> LLMClient:
    """Return the configured `LLMClient` based on ``LLM_PROVIDER``.

    Args:
        settings: Settings to use; defaults to `get_settings`.

    Raises:
        ValueError: if the provider is ``groq`` but no ``GROQ_API_KEY`` is set,
            or if the provider name is unrecognized.
    """
    settings = settings or get_settings()
    provider = settings.llm_provider

    if provider == "gemini":
        return GeminiClient(api_key=settings.gemini_api_key, model=settings.gemini_model)

    if provider == "groq":
        if not settings.groq_api_key:
            raise ValueError(
                "LLM_PROVIDER=groq but GROQ_API_KEY is not set. Add GROQ_API_KEY to your .env."
            )
        return GroqClient(api_key=settings.groq_api_key, model=settings.groq_model)

    raise ValueError(f"Unknown LLM_PROVIDER {provider!r}; expected 'gemini' or 'groq'.")
