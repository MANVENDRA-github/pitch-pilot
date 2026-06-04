"""Typed data contracts for pitch-pilot.

Every artifact that flows through the pipeline is a pydantic model defined in
this package and re-exported here, so callers can simply::

    from pitch_pilot.models import Fact, ICP, ResearchResult

The keystone is `Fact`, which enforces groundedness at construction.
"""

from __future__ import annotations

from pitch_pilot.models.draft import Draft
from pitch_pilot.models.fact import Fact
from pitch_pilot.models.icp import ICP
from pitch_pilot.models.lead import Company, Lead
from pitch_pilot.models.qualification import QualificationResult
from pitch_pilot.models.research import ResearchResult
from pitch_pilot.models.search import SearchResult
from pitch_pilot.models.verification import VerificationResult

__all__ = [
    "Fact",
    "SearchResult",
    "ICP",
    "Company",
    "Lead",
    "ResearchResult",
    "QualificationResult",
    "Draft",
    "VerificationResult",
]
