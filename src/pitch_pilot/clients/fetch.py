"""Fetch a URL and return clean, readable text.

Uses ``httpx`` to GET the page and ``selectolax`` to strip non-visible nodes and
extract whitespace-collapsed text. It is designed to **never crash the
pipeline**: any failure (network error, timeout, non-2xx status, parse error) is
logged and returns ``""`` so research can simply move on to the next source.
"""

from __future__ import annotations

import logging
import re

import httpx
from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)

# A real browser User-Agent — many sites reject the default httpx UA.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# selectolax does NOT drop these automatically — remove them before extracting text.
_STRIP_TAGS = ("script", "style", "noscript", "template", "iframe", "svg")
_WHITESPACE = re.compile(r"\s+")


def _html_to_text(html: str) -> str:
    """Extract clean, whitespace-collapsed visible text from an HTML string."""
    tree = HTMLParser(html)
    for tag in _STRIP_TAGS:
        for node in tree.css(tag):
            node.decompose()
    root = tree.body if tree.body is not None else tree.root
    if root is None:
        return ""
    # separator=" " keeps words from inline tags (e.g. <b>) from merging together.
    text = root.text(separator=" ", strip=True)
    return _WHITESPACE.sub(" ", text).strip()


def fetch_page(url: str, timeout: float = 10) -> str:
    """GET ``url`` and return clean extracted text, or ``""`` on any failure.

    Args:
        url: The page to fetch.
        timeout: Per-request timeout in seconds.

    Returns:
        Clean, whitespace-collapsed text, or ``""`` if the fetch or parse failed.
    """
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — never crash the pipeline on a bad page
        logger.warning("fetch_page failed for %s: %s", url, exc)
        return ""

    try:
        return _html_to_text(response.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to parse HTML from %s: %s", url, exc)
        return ""
