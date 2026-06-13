"""The `ICP` model — the Ideal Customer Profile used to qualify a company."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel


class ICP(BaseModel):
    """A declarative description of who is (and isn't) a good-fit customer.

    The ICP is the rubric the ``qualify`` step scores a company against. It is a
    configuration object — every field is required so a run is always evaluated
    against a fully-specified profile.

    Attributes:
        industries: Target industries, e.g. ``["fintech", "devtools"]``.
        min_employees: Lower bound of the target headcount band (inclusive).
        max_employees: Upper bound of the target headcount band (inclusive).
        regions: Target geographies, e.g. ``["US", "EU"]``.
        positive_signals: Signals that indicate a good fit, e.g.
            ``["hiring SDRs", "recent funding"]``.
        negative_signals: Signals that indicate a poor fit, e.g.
            ``["non-profit", "direct competitor"]``.
    """

    industries: list[str]
    min_employees: int
    max_employees: int
    regions: list[str]
    positive_signals: list[str]
    negative_signals: list[str]


def load_icp(path: str | Path) -> ICP:
    """Load and validate an `ICP` from a JSON file.

    Args:
        path: Path to a JSON file with the ICP fields (see
            ``examples/icp.sample.json`` for the shape).

    Returns:
        The parsed, validated `ICP`.

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        ValueError: if the file is not valid JSON or is missing required ICP
            fields (the message names the problem).
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(
            f"ICP file not found: {file_path}. Copy examples/icp.sample.json and edit it."
        )
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"ICP file {file_path} is not valid JSON: {exc}") from exc
    try:
        return ICP.model_validate(data)
    except Exception as exc:  # noqa: BLE001 — re-raise as a clear, actionable error
        raise ValueError(f"ICP file {file_path} is missing or has invalid fields: {exc}") from exc
