# pitch-pilot

> An autonomous **SDR** agent: give it a company **domain**, and it researches the
> company, qualifies it against your Ideal Customer Profile, drafts grounded
> outreach, and verifies every claim against a real source — then logs it for a
> human to review. **Nothing is auto-sent.**

## Why it's different — groundedness

The hero feature is **groundedness**: no fact exists without a `source_url`. The
core [`Fact`](src/pitch_pilot/models/fact.py) type refuses to be constructed
without an `http(s)` source, so every claim the agent makes is traceable to a page
*by design* — not by a hopeful post-hoc check. Outreach is drafted only from
grounded facts, every claim is re-verified against its source, and only drafts
above a groundedness threshold reach the human-review queue.

- ✅ Every fact carries a `source_url` (enforced at construction)
- ✅ Nothing auto-sends — qualified leads land in a review queue
- 🚫 No LinkedIn scraping (out of scope by design)

## Architecture (P0)

Hybrid: a **deterministic outer graph** (`research → qualify → draft → verify →
log`) wrapping an **agentic research sub-loop**. See
[`docs/architecture.md`](docs/architecture.md) and
[`docs/decisions.md`](docs/decisions.md).

This phase (P0) ships the foundation — typed data contracts, swappable provider
clients, typed config, a smoke test, and unit tests. The LangGraph pipeline and
node logic land in P1.

## Setup (Windows / PowerShell)

```powershell
# 1. Create and activate a virtual environment (Python 3.11+)
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install the package (editable) + dev tools
python -m pip install --upgrade pip
pip install -e ".[dev]"

# 3. Configure your keys
Copy-Item .env.example .env
# then edit .env and fill in at least GEMINI_API_KEY and TAVILY_API_KEY
```

> **Windows `.env` gotcha:** don't create `.env` with `Set-Content -Encoding utf8`
> — it writes a UTF-8 BOM that corrupts the *first* key so it silently fails to
> load. `Copy-Item .env.example .env` is safe (or use `-Encoding utf8NoBOM` /
> a normal editor).

<details>
<summary>macOS / Linux</summary>

```bash
python3.11 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
cp .env.example .env
```
</details>

## Configuration

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | ✅ | — | Google Gen AI (Gemini) API key |
| `TAVILY_API_KEY` | ✅ | — | Tavily search API key |
| `GROQ_API_KEY` | optional | — | Groq API key (only if `LLM_PROVIDER=groq`) |
| `LLM_PROVIDER` | optional | `gemini` | Active LLM provider: `gemini` or `groq` |
| `GEMINI_MODEL` | optional | `gemini-2.5-flash-lite` | Gemini model id |
| `GROQ_MODEL` | optional | `llama-3.1-8b-instant` | Groq model id |
| `RESEARCH_MAX_QUERIES` | optional | `4` | Max search queries per research run |
| `GROUNDEDNESS_THRESHOLD` | optional | `0.9` | Min groundedness score for a draft to pass |

Required keys are validated at startup: if one is missing, pitch-pilot fails
immediately with a `ConfigError` that names it.

## Smoke test (P0 acceptance gate)

Proves all three external dependencies work with your keys:

```powershell
python -m pitch_pilot.cli smoke
```

It runs one Tavily search, one LLM completion, and one page fetch, printing a
clear ✅ / ❌ for each. (Also available as the `pitch-pilot smoke` console script.)

## Tests

Unit tests are fully mocked — **no network and no API keys required**:

```powershell
pytest
```

## Project layout

See [`docs/architecture.md`](docs/architecture.md) for the annotated directory
tree and the end-to-end data flow.

## License

MIT
