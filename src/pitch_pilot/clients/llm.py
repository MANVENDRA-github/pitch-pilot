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

#: Smallest free-tier context window we target — Cerebras's free tier caps a single
#: request at 8,192 tokens (input + output). Prompt builders keep well under this by
#: budgeting their variable-length payloads (see `trim_to_token_budget` and ADR-0013).
CONTEXT_TOKEN_CAP = 8192


def trim_to_token_budget(lines: list[str], max_tokens: int) -> list[str]:
    """Keep leading ``lines`` whose cumulative size stays within ``max_tokens``.

    Uses a cheap ``~4 chars/token`` estimate. This bounds variable-length prompt
    payloads (e.g. a facts list) so no single request exceeds a provider's context
    cap, regardless of how many — or how long — the items are.

    Args:
        lines: Candidate prompt lines, in priority order.
        max_tokens: Token budget for the combined lines.

    Returns:
        The longest leading prefix of ``lines`` that fits the budget.
    """
    budget_chars = max_tokens * 4
    out: list[str] = []
    total = 0
    for line in lines:
        total += len(line) + 1  # +1 for the joining newline
        if total > budget_chars:
            break
        out.append(line)
    return out


class LLMError(RuntimeError):
    """Base error for LLM client failures."""


class LLMJSONError(LLMError):
    """Raised when an LLM response cannot be parsed as a JSON object."""


@runtime_checkable
class LLMClient(Protocol):
    """The provider-neutral LLM interface the pipeline depends on."""

    def complete(self, system: str, user: str, temperature: float | None = None) -> str:
        """Return a free-text completion for the given system + user prompts.

        ``temperature`` overrides the provider default when set; gate-critical calls
        (draft, verify judge) pass ``0.0`` for reproducibility.
        """
        ...

    def complete_json(self, system: str, user: str, temperature: float | None = None) -> dict:
        """Return a parsed JSON object. Raises `LLMJSONError` on bad JSON.

        ``temperature`` overrides the provider default when set (see `complete`).
        """
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

    def complete(self, system: str, user: str, temperature: float | None = None) -> str:
        """Return a free-text completion.

        Args:
            system: System instruction (role/behavior).
            user: User prompt (the request).
            temperature: Sampling temperature; the provider default when ``None``.

        Returns:
            The model's text response, stripped of surrounding whitespace.

        Raises:
            LLMError: if the provider request fails.
        """
        from google.genai import types

        client = self._ensure_client()
        config_kwargs: dict = {"system_instruction": system}
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        try:
            response = client.models.generate_content(
                model=self._model,
                contents=user,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:  # normalize vendor SDK errors to LLMError
            raise LLMError(f"Gemini request failed: {exc}") from exc
        return (response.text or "").strip()

    def complete_json(self, system: str, user: str, temperature: float | None = None) -> dict:
        """Return a parsed JSON object using Gemini's JSON output mode.

        Sets ``response_mime_type="application/json"`` and parses the result
        leniently (tolerating Markdown code fences).

        Args:
            system: System instruction.
            user: User prompt.
            temperature: Sampling temperature; the provider default when ``None``.

        Returns:
            The parsed JSON object.

        Raises:
            LLMError: if the provider request fails.
            LLMJSONError: if the response is not a valid JSON object.
        """
        from google.genai import types

        client = self._ensure_client()
        config_kwargs: dict = {
            "system_instruction": _json_system(system),
            "response_mime_type": "application/json",
        }
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        try:
            response = client.models.generate_content(
                model=self._model,
                contents=user,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:  # normalize vendor SDK errors to LLMError
            raise LLMError(f"Gemini request failed: {exc}") from exc
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

    def complete(self, system: str, user: str, temperature: float | None = None) -> str:
        """Return a free-text completion.

        Args:
            system: System message (role/behavior).
            user: User message (the request).
            temperature: Sampling temperature; the provider default when ``None``.

        Returns:
            The model's text response, stripped of surrounding whitespace.

        Raises:
            LLMError: if the provider request fails.
        """
        client = self._ensure_client()
        kwargs: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # normalize vendor SDK errors to LLMError
            raise LLMError(f"Groq request failed: {exc}") from exc
        return (response.choices[0].message.content or "").strip()

    def complete_json(self, system: str, user: str, temperature: float | None = None) -> dict:
        """Return a parsed JSON object using Groq's JSON mode.

        Uses ``response_format={"type": "json_object"}`` and injects an explicit
        "respond with JSON" instruction (which Groq's JSON mode requires).

        Args:
            system: System message.
            user: User message.
            temperature: Sampling temperature; the provider default when ``None``.

        Returns:
            The parsed JSON object.

        Raises:
            LLMError: if the provider request fails.
            LLMJSONError: if the response is not a valid JSON object.
        """
        client = self._ensure_client()
        kwargs: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _json_system(system)},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # normalize vendor SDK errors (e.g. json_validate_failed) to LLMError
            raise LLMError(f"Groq request failed: {exc}") from exc
        return _loads_json_lenient(response.choices[0].message.content or "")


class CerebrasClient:
    """`LLMClient` backed by the Cerebras Cloud SDK (OpenAI-compatible API).

    Cerebras exposes an OpenAI-compatible chat-completions API; the official
    ``cerebras-cloud-sdk`` defaults to ``https://api.cerebras.ai/v1``. We use it the
    same way as `GroqClient`, reusing the shared lenient JSON parsing. Its appeal for
    the eval is budget: the free tier allows ~1M tokens/day (≈10x Groq's), enough to
    run the whole eval set in one session (see ADR-0013). Available models vary by
    account — the default is ``gpt-oss-120b``; check the provider's ``models.list()``.
    """

    def __init__(self, api_key: str, model: str) -> None:
        """Store credentials and model id; the SDK client is built on first use.

        Args:
            api_key: Cerebras API key.
            model: Cerebras model id to call (e.g. ``gpt-oss-120b``).
        """
        self._api_key = api_key
        self._model = model
        self._client = None  # built lazily on first use

    def _ensure_client(self):
        """Lazily construct and cache the underlying ``cerebras.cloud.sdk.Cerebras`` client."""
        if self._client is None:
            from cerebras.cloud.sdk import Cerebras

            self._client = Cerebras(api_key=self._api_key)
        return self._client

    def complete(self, system: str, user: str, temperature: float | None = None) -> str:
        """Return a free-text completion.

        Args:
            system: System message (role/behavior).
            user: User message (the request).
            temperature: Sampling temperature; the provider default when ``None``.

        Returns:
            The model's text response, stripped of surrounding whitespace.

        Raises:
            LLMError: if the provider request fails.
        """
        client = self._ensure_client()
        kwargs: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # normalize vendor SDK errors to LLMError
            raise LLMError(f"Cerebras request failed: {exc}") from exc
        return (response.choices[0].message.content or "").strip()

    def complete_json(self, system: str, user: str, temperature: float | None = None) -> dict:
        """Return a parsed JSON object using Cerebras's JSON mode.

        Uses ``response_format={"type": "json_object"}`` and injects an explicit
        "respond with JSON" instruction, then parses leniently.

        Args:
            system: System message.
            user: User message.
            temperature: Sampling temperature; the provider default when ``None``.

        Returns:
            The parsed JSON object.

        Raises:
            LLMError: if the provider request fails.
            LLMJSONError: if the response is not a valid JSON object.
        """
        client = self._ensure_client()
        kwargs: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _json_system(system)},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # normalize vendor SDK errors to LLMError
            raise LLMError(f"Cerebras request failed: {exc}") from exc
        return _loads_json_lenient(response.choices[0].message.content or "")


def get_llm_client(settings: Settings | None = None) -> LLMClient:
    """Return the configured `LLMClient` based on ``LLM_PROVIDER``.

    Args:
        settings: Settings to use; defaults to `get_settings`.

    Raises:
        ValueError: if the selected provider's API key is missing, or if the
            provider name is unrecognized.
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

    if provider == "cerebras":
        if not settings.cerebras_api_key:
            raise ValueError(
                "LLM_PROVIDER=cerebras but CEREBRAS_API_KEY is not set. Add CEREBRAS_API_KEY to your .env."
            )
        return CerebrasClient(api_key=settings.cerebras_api_key, model=settings.cerebras_model)

    raise ValueError(f"Unknown LLM_PROVIDER {provider!r}; expected 'gemini', 'groq', or 'cerebras'.")
