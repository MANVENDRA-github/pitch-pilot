# Configuration

> **Last updated:** 2026-06-14 · **Source files:** `src/pitch_pilot/config.py`, `.env.example`

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
| `CEREBRAS_API_KEY` | Only when `LLM_PROVIDER=cerebras` | `None` | Cerebras API key. Its ~1M tokens/day free tier (~10x Groq) is used to run the full eval in one session (ADR-0013). |
| `LLM_PROVIDER` | No | `gemini` | Active LLM provider — `"gemini"`, `"groq"`, or `"cerebras"`. |
| `GEMINI_MODEL` | No | `gemini-2.5-flash-lite` | Gemini model id. |
| `GROQ_MODEL` | No | `llama-3.1-8b-instant` | Groq model id. |
| `CEREBRAS_MODEL` | No | `gpt-oss-120b` | Cerebras model id. Available models vary by account/tier — check the provider's `models.list()`. |
| `RESEARCH_MAX_QUERIES` | No | `4` | Max search queries per research run. The depth the eval tables and demos use on Cerebras; the earlier lean default of 3 was a Groq-quota value never shipped (ADR-0012). |
| `RESEARCH_MAX_PAGE_CHARS` | No | `3500` | Max characters of each source's text fed to the extractor. The biggest token lever; truncation preserves the evidence-substring check (ADR-0012). |
| `RESEARCH_MAX_FACTS_PER_SOURCE` | No | `5` | Max facts extracted from a single source, so one page can't dominate. |
| `QUALIFY_THRESHOLD` | No | `0.5` | Minimum fit score for a company to qualify against the ICP. A matched negative signal vetoes qualification regardless. |
| `GROUNDEDNESS_THRESHOLD` | No | `0.9` | Floor kept for transparency/future tuning. The verify gate is **all-or-nothing on faithfulness** (pass iff no body claim is `unsupported`, and none `overreach` under strict), not a score cutoff; under strict mode a passing draft scores 1.0. |
| `FAITHFULNESS_STRICT` | No | `true` | When `true`, an `overreach` faithfulness verdict fails the verify gate; when `false`, only `unsupported` fails. `unsupported` always fails. |

The LLM providers and their keys/models are consumed when building model clients — see [components/clients.md](components/clients.md).

## Validation rules

These constraints are enforced when `Settings` loads; violating any of them produces a `ConfigError`:

| Rule | Field |
| --- | --- |
| Must be `"gemini"`, `"groq"`, or `"cerebras"` (trimmed and lower-cased before checking). | `LLM_PROVIDER` |
| Must be an integer `>= 1`. | `RESEARCH_MAX_QUERIES` |
| Must be an integer `>= 500`. | `RESEARCH_MAX_PAGE_CHARS` |
| Must be an integer `>= 1`. | `RESEARCH_MAX_FACTS_PER_SOURCE` |
| Must be a float in the closed range `[0, 1]`. | `QUALIFY_THRESHOLD` |
| Must be a float in the closed range `[0, 1]`. | `GROUNDEDNESS_THRESHOLD` |
| Boolean (`true`/`false`, case-insensitive; also `1`/`0`). | `FAITHFULNESS_STRICT` |
| Required only when `LLM_PROVIDER=groq`; otherwise optional and defaults to `None`. | `GROQ_API_KEY` |
| Required only when `LLM_PROVIDER=cerebras`; otherwise optional and defaults to `None`. | `CEREBRAS_API_KEY` |

The `LLM_PROVIDER` validator normalizes its input, so `GEMINI`, ` gemini `, and `Gemini` all resolve to the canonical `"gemini"` (likewise `groq` / `cerebras`).

## Sample `.env`

Copy `.env.example` to `.env` and fill in your keys. The defaults below match the values baked into `config.py`, so a minimal `.env` only needs the required keys.

```bash
# --- Required ---
GEMINI_API_KEY=your-gemini-api-key
TAVILY_API_KEY=tvly-your-tavily-api-key

# --- Optional (only needed when the matching LLM_PROVIDER is selected) ---
GROQ_API_KEY=your-groq-api-key
CEREBRAS_API_KEY=your-cerebras-api-key

# --- Provider selection: "gemini" (default), "groq", or "cerebras" ---
LLM_PROVIDER=gemini

# --- Model ids (current free-tier defaults shown) ---
GEMINI_MODEL=gemini-2.5-flash-lite
GROQ_MODEL=llama-3.1-8b-instant
CEREBRAS_MODEL=gpt-oss-120b

# --- Research / qualification / grounding tunables ---
RESEARCH_MAX_QUERIES=4
RESEARCH_MAX_PAGE_CHARS=3500
RESEARCH_MAX_FACTS_PER_SOURCE=5
QUALIFY_THRESHOLD=0.5
GROUNDEDNESS_THRESHOLD=0.9
FAITHFULNESS_STRICT=true
```

On Windows/PowerShell, create the file with `Copy-Item .env.example .env`. Avoid `Set-Content -Encoding utf8`, which writes a BOM that corrupts the first key.
