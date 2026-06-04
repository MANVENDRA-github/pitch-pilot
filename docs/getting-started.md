# Getting Started

> **Last updated:** 2026-06-05 · **Source files:** `README.md`, `.env.example`, `src/pitch_pilot/cli.py`

This page takes you from a fresh clone to a passing smoke test. The current scaffold (P0) ships typed data contracts, swappable provider clients, typed config, a smoke test, and unit tests — so "running" pitch-pilot today means verifying that its three external dependencies (search, LLM, fetch) work with your keys. The LangGraph pipeline lands in P1.

For the full settings reference, see [configuration.md](configuration.md). For the directory layout and end-to-end data flow, see [architecture.md](architecture.md).

## Prerequisites

- **Python 3.11+** (the package declares `requires-python = ">=3.11"`)
- **git**
- A **Gemini API key** and a **Tavily API key** (both required). A Groq key is optional and only needed if you switch `LLM_PROVIDER` to `groq`.

## 1. Clone the repository

```bash
git clone https://github.com/Manvendra/pitch-pilot.git
cd pitch-pilot
```

## 2. Create and activate a virtual environment

=== "Windows / PowerShell"

    ```powershell
    py -3.11 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    ```

=== "macOS / Linux"

    ```bash
    python3.11 -m venv .venv && source .venv/bin/activate
    ```

## 3. Install the package

Install pitch-pilot in editable mode with the `dev` extras (pytest plus the MkDocs docs toolchain):

```bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

This installs the runtime dependencies (pydantic, langgraph, google-genai, groq, tavily-python, httpx, selectolax) and exposes the `pitch-pilot` console script.

## 4. Configure your keys

Copy the template to `.env`:

=== "Windows / PowerShell"

    ```powershell
    Copy-Item .env.example .env
    ```

=== "macOS / Linux"

    ```bash
    cp .env.example .env
    ```

!!! warning "Windows `.env` gotcha"
    On Windows, do **not** create `.env` with `Set-Content -Encoding utf8`. That writes a UTF-8 BOM that corrupts the *first* key, so it silently fails to load. `Copy-Item .env.example .env` is safe — or use `-Encoding utf8NoBOM`, or a normal text editor.

Now open `.env` and fill in at least the two required keys:

| Variable | Required | Description |
| --- | --- | --- |
| `GEMINI_API_KEY` | yes | Google Gen AI (Gemini) API key |
| `TAVILY_API_KEY` | yes | Tavily search API key |

The remaining variables (`GROQ_API_KEY`, `LLM_PROVIDER`, `GEMINI_MODEL`, `GROQ_MODEL`, `RESEARCH_MAX_QUERIES`, `GROUNDEDNESS_THRESHOLD`) have sensible defaults and are optional. See [configuration.md](configuration.md) for the complete list, defaults, and validation rules.

The two required keys are validated at startup: if one is missing, pitch-pilot fails immediately with a `ConfigError` that names the offending variable.

## 5. Run the smoke test

The smoke test is the P0 acceptance gate. It proves all three external dependencies work with your configured keys:

```bash
python -m pitch_pilot.cli smoke
```

It runs **one Tavily search**, **one LLM completion**, and **one page fetch** (against `https://example.com`), printing a per-check pass/fail marker with a detail line for each. It exits **non-zero if any check fails**, so it works as a CI gate. The same command is available via the installed console script:

```bash
pitch-pilot smoke
```

A passing run looks roughly like this:

```text
pitch-pilot smoke test - verifying external dependencies

✅ Config: loaded (LLM provider = gemini)

✅ Search (Tavily): top result → ...
✅ LLM completion: gemini/gemini-2.5-flash-lite replied → 'OK'
✅ Fetch (httpx + selectolax): https://example.com → ... chars of clean text

3/3 checks passed.
```

If your console can't encode the emoji markers, pitch-pilot automatically degrades them to `[OK]` / `[FAIL]` — the exit code is unchanged.

## 6. Run the unit tests

The unit tests are fully mocked — **no network access and no API keys required**:

```bash
pytest
```

## 7. Preview the docs (optional)

The `dev` extras include MkDocs Material, so you can serve this documentation site locally with live reload:

```bash
mkdocs serve
```

Then open the printed local URL (default `http://127.0.0.1:8000`) in your browser.

## Next steps

- [configuration.md](configuration.md) — every setting, its default, and how config is validated.
- [architecture.md](architecture.md) — the hybrid design (deterministic outer graph + agentic research sub-loop), the annotated directory tree, and the end-to-end data flow.
