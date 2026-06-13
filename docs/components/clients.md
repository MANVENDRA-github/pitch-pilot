# Clients

> **Last updated:** 2026-06-13 · **Source files:** `src/pitch_pilot/clients/`

The `clients` package is pitch-pilot's swappable external-service layer. Every call that leaves the process — an LLM completion, a web search, an HTTP page fetch — goes through a small interface defined here. The rest of the pipeline depends on those interfaces, never on a vendor SDK, so providers can be swapped by configuration and the network can be mocked at a single seam in tests.

There are three concerns, each with its own interface:

| Concern | Interface | Default provider | Factory |
| --- | --- | --- | --- |
| LLM completion | `LLMClient` (Protocol) | `GeminiClient` | `get_llm_client()` |
| Web search | `SearchClient` (Protocol) | `TavilyClient` | `get_search_client()` |
| Page fetch | `fetch_page()` (plain function) | httpx + selectolax | — |

All three are re-exported from the package root (`pitch_pilot.clients`), alongside `GroqClient`, `LLMError`, and `LLMJSONError`. See the API Reference (in the nav) for full signatures.

## Lazy SDK imports

Every concrete client imports its vendor SDK **lazily**, inside the method that first needs it — never at module import time:

- `GeminiClient` imports `from google import genai` (and `google.genai.types`) only when it builds its client or makes a call.
- `GroqClient` imports `from groq import Groq` only on first use.
- `TavilyClient` imports `from tavily import TavilyClient` only on first use.

Two consequences follow, and they are the reason for the pattern:

1. **Importing `pitch_pilot.clients` requires no provider package installed.** You can install only the provider you actually use; the others never need to be present to import the package or run unrelated code.
2. **Unit tests hit no network.** The pure parsing/selection logic (lenient JSON parsing, factory dispatch, payload normalization) is testable without any SDK or live credentials, because nothing connects until a method is called.

Each client also caches its underlying SDK client after first construction (an `_ensure_client()` helper), so the lazy import and connection happen exactly once.

## LLMClient

`LLMClient` is a `runtime_checkable` `Protocol` with two operations:

| Method | Returns | Notes |
| --- | --- | --- |
| `complete(system, user)` | `str` | Free-text completion, stripped of surrounding whitespace. |
| `complete_json(system, user)` | `dict` | A parsed JSON **object**. Raises `LLMJSONError` on bad JSON. |

Both take a `system` prompt (role/behavior) and a `user` prompt (the request). The error hierarchy is `LLMError(RuntimeError)` with `LLMJSONError(LLMError)` for JSON-parse failures specifically.

**Provider errors are normalized.** Both methods wrap the vendor SDK call and re-raise any provider exception (a network error, a rate-limit, or Groq's server-side `json_validate_failed`) as `LLMError`. This is the contract pipeline nodes rely on: each node catches `LLMError` and degrades gracefully (e.g. the qualify node falls back to an all-unknown assessment) rather than letting a vendor exception crash the whole run.

### Lenient JSON parsing

`complete_json` does not trust the model to emit clean JSON. The shared parser, `_loads_json_lenient`, tolerates the shapes LLMs actually produce:

- **Bare JSON** — parsed directly.
- **A fenced code block** — a leading ` ``` ` (with an optional language tag) is stripped, and a trailing fence is stripped independently, so an *unterminated* fence still parses. Stripping is anchored to the start of the response, so triple backticks appearing inside JSON *values* are left intact.
- **JSON preceded by prose** — if a direct parse fails, a best-effort recovery (`_extract_json_object`) tries the contents of a Markdown fenced block, then the widest `{...}` span, and returns the first that parses to a `dict`.

It raises `LLMJSONError` when the text is empty, is not valid JSON, or parses to something other than a JSON object (for example, a JSON array or a bare number).

### Providers

**`GeminiClient`** — backed by the official Google Gen AI SDK (`google-genai`). It uses the current client-centric API: a single `genai.Client`, with the model id passed per call and the system prompt supplied via `types.GenerateContentConfig(system_instruction=...)`. `complete` calls `client.models.generate_content(...)`. `complete_json` adds `response_mime_type="application/json"` to request Gemini's JSON output mode, then still parses leniently. Default model: `gemini-2.5-flash-lite`.

**`GroqClient`** — backed by the official Groq SDK, which is OpenAI-compatible. Both methods call `client.chat.completions.create(...)` with `system` and `user` messages. `complete_json` sets `response_format={"type": "json_object"}` and, because Groq's JSON mode *requires* an explicit instruction, injects a "respond with a single valid JSON object" line into the system message (the shared `_json_system` helper). Default model: `llama-3.1-8b-instant`.

### Factory: `get_llm_client()`

`get_llm_client(settings=None)` selects the provider from `Settings.llm_provider` (defaulting to the cached process settings when none is passed):

- `gemini` → `GeminiClient(api_key=gemini_api_key, model=gemini_model)`.
- `groq` → `GroqClient(api_key=groq_api_key, model=groq_model)`, but raises `ValueError` if `GROQ_API_KEY` is not set.
- Anything else → `ValueError` naming the unknown provider.

Provider name and model defaults are validated in [`configuration.md`](../configuration.md); `llm_provider` is normalized to lowercase and restricted to `gemini` or `groq` at config-load time.

## SearchClient

`SearchClient` is a `runtime_checkable` `Protocol` with one method:

```python
def search(self, query: str, max_results: int = 5) -> list[SearchResult]: ...
```

It returns a list of `SearchResult` — the provider-neutral shape (`title`, `url`, `content`) that every search provider normalizes to, so callers never touch a vendor's raw payload. The result's `url` becomes a `source_url` downstream, which is the anchor of groundedness. See [`data-models.md`](../data-models.md) for `SearchResult` and how facts attach to source URLs, and [`groundedness.md`](../groundedness.md) for the rule itself.

**`TavilyClient`** is the P0 implementation, backed by `tavily-python`. Tavily's `.search()` returns a plain dict whose `"results"` list carries `title` / `url` / `content` per item; the client maps each item to a `SearchResult`, coalescing any missing field to `""`. If the response is not a dict or has no results, it returns an empty list. The `max_results` argument is passed straight through (Tavily allows 0–20).

### Factory: `get_search_client()`

`get_search_client(settings=None)` returns a `TavilyClient(api_key=tavily_api_key)`. In P0 there is exactly one search provider, so there is no provider switch here yet.

## fetch_page

`fetch_page(url, timeout=10) -> str` is a plain function, not a class — there is only one fetch implementation. It GETs the page with `httpx` (following redirects) and extracts clean, whitespace-collapsed visible text with `selectolax`.

Key behaviors:

- **Real User-Agent.** It sends a Chrome desktop `User-Agent` header, because many sites reject httpx's default UA.
- **Text extraction.** It removes non-visible nodes (`script`, `style`, `noscript`, `template`, `iframe`, `svg`) — which selectolax does not drop automatically — then extracts body text with a space separator (so words from inline tags do not merge) and collapses all runs of whitespace.
- **Never raises.** Any failure — network error, timeout, non-2xx status (via `raise_for_status`), or a parse error — is caught, logged at WARNING level, and returns `""`. The agentic research sub-loop can therefore skip a bad source and move on instead of crashing the run.

The `timeout` is a per-request timeout in seconds.

## How to swap a provider

To move the LLM from Gemini to Groq, change only configuration — no code:

1. Set `LLM_PROVIDER=groq`.
2. Set `GROQ_API_KEY` (and optionally `GROQ_MODEL`, default `llama-3.1-8b-instant`).
3. Install the Groq SDK in your environment.

`get_llm_client()` then returns a `GroqClient`; the rest of the pipeline is unchanged because it only ever sees the `LLMClient` interface. If you set `LLM_PROVIDER=groq` without a `GROQ_API_KEY`, the factory raises a `ValueError` that tells you exactly what to add. Full setting reference is in [`configuration.md`](../configuration.md).

## How to add a new provider

The interfaces are `Protocol`s, so a new provider just needs to *structurally* match — no base class to inherit:

1. **Implement the Protocol.** For an LLM, write a class with `complete(self, system, user) -> str` and `complete_json(self, system, user) -> dict`. Import the vendor SDK lazily inside the method that uses it (and cache it via an `_ensure_client()` helper) so the lazy-import guarantees still hold. Reuse `_loads_json_lenient` so JSON handling stays consistent and `complete_json` raises `LLMJSONError` on bad output.
2. **Wire it into the factory.** Add a branch to `get_llm_client()` that returns your client for the new `llm_provider` value, and pull any new model id from `Settings`. For a search provider, do the same in `get_search_client()`.
3. **Extend config.** Add the provider name to the allowed set and any new keys/model defaults in `config.py`, then document them in [`configuration.md`](../configuration.md).

A search provider follows the same recipe: implement `search(self, query, max_results=5) -> list[SearchResult]`, normalizing the vendor payload to `SearchResult` so downstream code stays vendor-agnostic.

For definitions of `LLMClient`, `SearchClient`, `SearchResult`, `source_url`, and groundedness terms used here, see the [glossary](../glossary.md).
