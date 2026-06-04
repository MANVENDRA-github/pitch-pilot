"""Unit tests for the typed data contracts.

These tests are pure — they construct pydantic models and never touch the
network. The most important behavior under test is that a :class:`Fact` cannot be
constructed without a valid ``http(s)`` source, which is what makes groundedness
a structural guarantee rather than a runtime hope.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pitch_pilot.models import (
    ICP,
    Company,
    Draft,
    Fact,
    Lead,
    QualificationResult,
    ResearchResult,
    SearchResult,
    VerificationResult,
)


class TestFactGroundedness:
    """A Fact must be born grounded — a real http(s) source or it does not exist."""

    def test_valid_fact_constructs(self):
        fact = Fact(claim="Acme raised a $20M Series B", source_url="https://acme.com/news")
        assert fact.source_url == "https://acme.com/news"
        assert fact.confidence == 0.5  # default
        assert fact.source_title is None
        assert fact.category is None

    def test_empty_source_url_rejected(self):
        with pytest.raises(ValidationError):
            Fact(claim="ungrounded claim", source_url="")

    def test_whitespace_only_source_url_rejected(self):
        with pytest.raises(ValidationError):
            Fact(claim="ungrounded claim", source_url="   ")

    def test_non_http_scheme_rejected(self):
        with pytest.raises(ValidationError):
            Fact(claim="bad scheme", source_url="ftp://acme.com/file")

    def test_bare_domain_without_scheme_rejected(self):
        with pytest.raises(ValidationError):
            Fact(claim="no scheme", source_url="acme.com/news")

    @pytest.mark.parametrize("scheme", ["http://", "https://"])
    def test_http_and_https_accepted(self, scheme):
        fact = Fact(claim="ok", source_url=f"{scheme}acme.com/about")
        assert fact.source_url.startswith(scheme)

    def test_source_url_is_stripped(self):
        fact = Fact(claim="ok", source_url="  https://acme.com/about  ")
        assert fact.source_url == "https://acme.com/about"

    def test_confidence_upper_bound_enforced(self):
        with pytest.raises(ValidationError):
            Fact(claim="x", source_url="https://a.com", confidence=1.5)

    def test_confidence_lower_bound_enforced(self):
        with pytest.raises(ValidationError):
            Fact(claim="x", source_url="https://a.com", confidence=-0.1)


class TestModelsConstruct:
    """Every contract constructs cleanly with valid data."""

    def test_company_and_lead(self):
        lead = Lead(company=Company(domain="acme.com", name="Acme"))
        assert lead.company.domain == "acme.com"
        assert lead.company.name == "Acme"

    def test_company_name_optional(self):
        assert Company(domain="acme.com").name is None

    def test_icp(self):
        icp = ICP(
            industries=["fintech"],
            min_employees=50,
            max_employees=500,
            regions=["US", "EU"],
            positive_signals=["hiring SDRs"],
            negative_signals=["non-profit"],
        )
        assert icp.min_employees == 50
        assert "fintech" in icp.industries

    def test_search_result(self):
        sr = SearchResult(title="Acme — About", url="https://acme.com/about", content="...")
        assert sr.url == "https://acme.com/about"

    def test_qualification_result(self):
        qual = QualificationResult(
            qualified=True,
            score=0.82,
            reason="Headcount and industry both match.",
            matched_signals=["hiring SDRs"],
            missed_signals=[],
        )
        assert qual.qualified is True
        assert qual.score == 0.82

    def test_qualification_score_bounds_enforced(self):
        with pytest.raises(ValidationError):
            QualificationResult(qualified=False, score=1.4, reason="bad")

    def test_draft(self):
        draft = Draft(subject="Quick idea for Acme", body="Hi there…", hooks_used=["funding"])
        assert draft.subject.startswith("Quick")
        assert draft.hooks_used == ["funding"]

    def test_draft_hooks_default_empty(self):
        assert Draft(subject="s", body="b").hooks_used == []

    def test_verification_result(self):
        ver = VerificationResult(
            groundedness_score=0.95,
            total_claims=4,
            grounded_claims=4,
            flagged_claims=[],
            passed=True,
        )
        assert ver.passed is True
        assert ver.groundedness_score == 0.95

    def test_verification_score_bounds_enforced(self):
        with pytest.raises(ValidationError):
            VerificationResult(
                groundedness_score=2.0, total_claims=1, grounded_claims=1, passed=False
            )


class TestResearchResult:
    def test_source_count_counts_distinct_sources(self):
        company = Company(domain="acme.com")
        facts = [
            Fact(claim="a", source_url="https://acme.com/1"),
            Fact(claim="b", source_url="https://acme.com/1"),  # duplicate source
            Fact(claim="c", source_url="https://acme.com/2"),
        ]
        research = ResearchResult(company=company, facts=facts, queries_run=["q1", "q2"])
        assert research.source_count == 2  # two distinct URLs
        assert len(research.facts) == 3

    def test_defaults_are_empty(self):
        research = ResearchResult(company=Company(domain="acme.com"))
        assert research.facts == []
        assert research.queries_run == []
        assert research.source_count == 0
