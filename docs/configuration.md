# Configuration

> **Last updated:** 2026-06-05 · **Source files:** `src/pitch_pilot/config.py`, `.env.example`

All runtime configuration lives in a single, validated `Settings` object built on Pydantic Settings. pitch-pilot reads its configuration once at startup and fails loudly if anything required is missing or invalid — it never starts a run half-configured. For a step-by-step setup walkthrough, see [Getting Started](getting-started.md).

## How settings load

`Settings` is populated from two sources, in order of precedence:

1. **Real environment variables** — these always win.
2. **A local `.env` file** — read from the working directory if present (`env_file=".env"`, UTF-8).

Matching is **case-insensitive**: the environment variable `GEMINI_API_KEY` populates the `gemini_api_key` field. Unknown keys are ignored (`extra="ignore"`), so an over-full `.env` will not crash the app.

Always read configuration through `get_settings()` rather than constructing `Settings()` directly:

```python
from pitch_pilot.config import get_settings

settings = get_settings()
print(settings.llm_provider)  # "gemini"
```

`get_settings()` is cached with `lru_cache(maxsize=1)`, so the file/environment is parsed once per process. In tests (or after mutating the environment) call `get_settings.cache_clear()` to force a fresh reload.

## Required keys are named at startup

If a required value is missing, `get_settings()` raises a `ConfigError` (a `RuntimeError` subclass) whose message **names every missing variable**, for example:

```
Missing required configuration: GEMINI_API_KEY, TAVILY_API_KEY. Set them as
environment variables or in a .env file (copy .env.example to .env and fill in
your keys).
```

Invalid (but present) values also raise `ConfigError`, with the offending field name and the validation message inline. Either way the failure happens at startup, before any pipeline node runs.

## Settings reference

One row per field defined in `config.py`:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | Yes | — | Google Gen AI (Gemini) API key. |
| `TAVILY_API_KEY` | Yes | — | Tavily search API key (used by the research sub-loop). |
| `GROQ_API_KEY` | Only when `LLM_PROVIDER=groq` | `None` | Groq API key. |
| `LLM_PROVIDER` | No | `gemini` | Active LLM provider — `"gemini"` or `"groq"`. |
| `GEMINI_MODEL` | No | `gemini-2.5-flash-lite` | Gemini model id. |
| `GROQ_MODEL` | No | `llama-3.1-8b-instant` | Groq model id. |
| `RESEARCH_MAX_QUERIES` | No | `4` | Max search queries per research run. |
| `GROUNDEDNESS_THRESHOLD` | No | `0.9` | Minimum groundedness score for a draft to pass verification. |

The two LLM providers and their keys/models are consumed when building model clients — see [components/clients.md](components/clients.md).

## Validation rules

These constraints are enforced when `Settings` loads; violating any of them produces a `ConfigError`:

| Rule | Field |
| --- | --- |
| Must be `"gemini"` or `"groq"` (trimmed and lower-cased before checking). | `LLM_PROVIDER` |
| Must be an integer `>= 1`. | `RESEARCH_MAX_QUERIES` |
| Must be a float in the closed range `[0, 1]`. | `GROUNDEDNESS_THRESHOLD` |
| Required only when `LLM_PROVIDER=groq`; otherwise optional and defaults to `None`. | `GROQ_API_KEY` |

The `LLM_PROVIDER` validator normalizes its input, so `GEMINI`, ` gemini `, and `Gemini` all resolve to the canonical `"gemini"`.

## Sample `.env`

Copy `.env.example` to `.env` and fill in your keys. The defaults below match the values baked into `config.py`, so a minimal `.env` only needs the required keys.

```bash
# --- Required ---
GEMINI_API_KEY=your-gemini-api-key
TAVILY_API_KEY=tvly-your-tavily-api-key

# --- Optional (only needed when LLM_PROVIDER=groq) ---
GROQ_API_KEY=your-groq-api-key

# --- Provider selection: "gemini" (default) or "groq" ---
LLM_PROVIDER=gemini

# --- Model ids (current free-tier defaults shown) ---
GEMINI_MODEL=gemini-2.5-flash-lite
GROQ_MODEL=llama-3.1-8b-instant

# --- Research / grounding tunables ---
RESEARCH_MAX_QUERIES=4
GROUNDEDNESS_THRESHOLD=0.9
```

On Windows/PowerShell, create the file with `Copy-Item .env.example .env`. Avoid `Set-Content -Encoding utf8`, which writes a BOM that corrupts the first key.
