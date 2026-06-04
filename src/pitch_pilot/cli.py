"""Command-line entry point for pitch-pilot.

P0 exposes one command — ``smoke`` — the acceptance gate that proves all three
external dependencies (search, LLM, fetch) work with the configured keys::

    python -m pitch_pilot.cli smoke

It runs one Tavily search, one LLM completion, and one page fetch, printing a
clear ✅ / ❌ for each and exiting non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable

from pitch_pilot.config import ConfigError, get_settings

# Status markers. Downgraded to ASCII by _reconfigure_utf8() if stdout cannot
# encode the emoji (e.g. a non-UTF-8 Windows console or a redirected stream).
OK = "✅"
FAIL = "❌"


def _reconfigure_utf8() -> None:
    """Make the status markers safe to print on any console (notably Windows).

    Best-effort: switch stdout/stderr to UTF-8 (degrading any unencodable
    character instead of raising), then fall back to ASCII markers if the stream
    still cannot encode the emoji.
    """
    global OK, FAIL
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except Exception:  # noqa: BLE001 — best-effort only
                pass
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        (OK + FAIL).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        OK, FAIL = "[OK]", "[FAIL]"


def _run_check(label: str, check: Callable[[], str]) -> bool:
    """Run one smoke check, printing a pass/fail marker with a detail line."""
    try:
        detail = check()
    except Exception as exc:  # noqa: BLE001 — report any failure, don't abort the suite
        print(f"{FAIL} {label}: {exc}")
        return False
    print(f"{OK} {label}: {detail}")
    return True


def run_smoke() -> int:
    """Run the P0 smoke test. Returns a process exit code (0 = all passed)."""
    _reconfigure_utf8()  # idempotent; ensures markers are safe via any entry point
    print("pitch-pilot smoke test - verifying external dependencies\n")

    try:
        settings = get_settings()
    except ConfigError as exc:
        print(f"{FAIL} Config: {exc}")
        return 1
    print(f"{OK} Config: loaded (LLM provider = {settings.llm_provider})\n")

    def check_search() -> str:
        from pitch_pilot.clients.search import get_search_client

        client = get_search_client(settings)
        results = client.search("Anthropic", max_results=1)
        if not results:
            raise RuntimeError("Tavily returned no results")
        top = results[0]
        return f"top result → {top.title} | {top.url}"

    def check_llm() -> str:
        from pitch_pilot.clients.llm import get_llm_client

        client = get_llm_client(settings)
        model = settings.gemini_model if settings.llm_provider == "gemini" else settings.groq_model
        reply = client.complete(
            system="You are a terse assistant. Reply with exactly what is asked and nothing else.",
            user="Reply with the single word: OK",
        )
        return f"{settings.llm_provider}/{model} replied → {reply!r}"

    def check_fetch() -> str:
        from pitch_pilot.clients.fetch import fetch_page

        url = "https://example.com"
        text = fetch_page(url)
        if not text:
            raise RuntimeError(f"fetched 0 characters from {url}")
        return f"{url} → {len(text)} chars of clean text"

    checks = [
        ("Search (Tavily)", check_search),
        ("LLM completion", check_llm),
        ("Fetch (httpx + selectolax)", check_fetch),
    ]
    passed = sum(_run_check(label, check) for label, check in checks)

    print(f"\n{passed}/{len(checks)} checks passed.")
    return 0 if passed == len(checks) else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    _reconfigure_utf8()

    parser = argparse.ArgumentParser(
        prog="pitch-pilot",
        description="pitch-pilot — autonomous SDR agent (P0 scaffold).",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "smoke", help="Run the P0 acceptance gate: verify search, LLM and fetch."
    )

    args = parser.parse_args(argv)

    if args.command == "smoke":
        return run_smoke()

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
