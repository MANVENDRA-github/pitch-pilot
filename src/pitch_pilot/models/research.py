"""The `ResearchResult` model — grounded facts gathered about a company."""

from __future__ import annotations

from pydantic import BaseModel, Field

from pitch_pilot.models.fact import Fact
from pitch_pilot.models.lead import Company


class ResearchResult(BaseModel):
    """Everything the research sub-loop learned about a company.

    Attributes:
        company: The company the research is about.
        facts: The grounded facts discovered. Each `Fact` carries its own
            ``source_url``, so the whole result is grounded by construction.
        queries_run: The search queries actually executed, kept for transparency
            and for debugging the agentic research loop.
        errors: Non-fatal problems encountered during research (a failed fetch,
            an empty search, an extraction error). The research node never
            crashes on a bad page; it records the problem here and moves on.
    """

    company: Company
    facts: list[Fact] = Field(default_factory=list)
    queries_run: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def source_count(self) -> int:
        """Number of *distinct* source URLs across all facts.

        Counts unique sources (not facts), since several facts can cite the same
        page. A higher source count means the research draws on more independent
        evidence.
        """
        return len({fact.source_url for fact in self.facts})
