"""Unit tests for the qualify node. No network access.

The LLM is replaced with a fake that returns a fixed *assessment* (the semantic
match of ICP signals against facts). The behaviors under test are the
deterministic guarantees the node layers on top of that assessment:

* a matched negative signal is a hard veto, regardless of fit score;
* the fit score is computed deterministically from the assessment;
* unknowns are never guessed — neither matched nor missed, and unknown structural
  attributes are dropped from the score, not penalized;
* with no facts, the company is not qualified and the LLM is never called.
"""

from __future__ import annotations

from pitch_pilot.clients.llm import LLMError
from pitch_pilot.config import Settings
from pitch_pilot.models.fact import Fact
from pitch_pilot.models.icp import ICP
from pitch_pilot.models.lead import Company
from pitch_pilot.models.research import ResearchResult
from pitch_pilot.nodes.qualify import qualify_node, run_qualification


def _settings(**overrides) -> Settings:
    values = {"gemini_api_key": "g", "tavily_api_key": "t", "qualify_threshold": 0.5}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _icp(**overrides) -> ICP:
    values = {
        "industries": ["fintech", "devtools"],
        "min_employees": 50,
        "max_employees": 2000,
        "regions": ["US"],
        "positive_signals": ["recent funding", "hiring engineers"],
        "negative_signals": ["direct competitor", "non-profit"],
    }
    values.update(overrides)
    return ICP(**values)


def _research(n: int = 2) -> ResearchResult:
    facts = [
        Fact(claim=f"fact {i}", source_url=f"https://acme.com/{i}", evidence="e", source_tier="own_site")
        for i in range(n)
    ]
    return ResearchResult(company=Company(domain="acme.com"), facts=facts)


class FakeLLM:
    """Returns a fixed assessment dict, or raises if ``raises=True``."""

    def __init__(self, assessment=None, *, raises=False):
        self.assessment = assessment or {}
        self.raises = raises
        self.calls = 0

    def complete(self, system, user):  # pragma: no cover - unused
        return "OK"

    def complete_json(self, system, user):
        self.calls += 1
        if self.raises:
            raise LLMError("assessor down")
        return self.assessment


class TestRunQualification:
    def test_deterministic_score(self):
        assessment = {
            "industry": {"status": "match"},
            "region": {"status": "match"},
            "employee_count": {"value": 100},
            "positive_signals": [
                {"signal": "recent funding", "status": "match"},
                {"signal": "hiring engineers", "status": "no_match"},
            ],
            "negative_signals": [],
        }
        result = run_qualification(_research(), _icp(), FakeLLM(assessment), _settings())
        # industry(.35) + size(.25) + region(.15) + positives 1/2 (.25*0.5) = 0.875
        assert result.score == 0.875
        assert result.qualified is True
        assert result.matched_signals == ["recent funding"]
        assert result.missed_signals == ["hiring engineers"]

    def test_matched_negative_signal_is_a_hard_veto(self):
        assessment = {
            "industry": {"status": "match"},
            "region": {"status": "match"},
            "employee_count": {"value": 100},
            "positive_signals": [
                {"signal": "recent funding", "status": "match"},
                {"signal": "hiring engineers", "status": "match"},
            ],
            "negative_signals": [{"signal": "direct competitor", "status": "match"}],
        }
        result = run_qualification(_research(), _icp(), FakeLLM(assessment), _settings())
        assert result.score == 1.0  # perfect fit on paper...
        assert result.qualified is False  # ...but the veto wins
        assert "(negative) direct competitor" in result.missed_signals
        assert "direct competitor" in result.reason

    def test_unknowns_are_never_guessed(self):
        assessment = {
            "industry": {"status": "unknown"},
            "region": {"status": "unknown"},
            "employee_count": {"value": None},
            "positive_signals": [
                {"signal": "recent funding", "status": "match"},
                {"signal": "hiring engineers", "status": "unknown"},
            ],
            "negative_signals": [
                {"signal": "direct competitor", "status": "unknown"},
                {"signal": "non-profit", "status": "unknown"},
            ],
        }
        result = run_qualification(_research(), _icp(), FakeLLM(assessment), _settings())
        # Only the positives component is known: 1 of 2 matched -> 0.5
        assert result.score == 0.5
        assert result.matched_signals == ["recent funding"]
        # The unknown positive is neither matched nor missed (not guessed).
        assert "hiring engineers" not in result.matched_signals
        assert "hiring engineers" not in result.missed_signals
        # Unknown negatives never veto.
        assert result.qualified is True

    def test_below_threshold_is_not_qualified(self):
        assessment = {
            "industry": {"status": "no_match"},
            "region": {"status": "no_match"},
            "employee_count": {"value": 5},  # outside 50-2000 band -> no_match
            "positive_signals": [
                {"signal": "recent funding", "status": "no_match"},
                {"signal": "hiring engineers", "status": "no_match"},
            ],
            "negative_signals": [],
        }
        result = run_qualification(_research(), _icp(), FakeLLM(assessment), _settings())
        assert result.score == 0.0
        assert result.qualified is False

    def test_no_facts_skips_llm_and_disqualifies(self):
        llm = FakeLLM()
        empty = ResearchResult(company=Company(domain="acme.com"))
        result = run_qualification(empty, _icp(), llm, _settings())
        assert result.qualified is False
        assert result.score == 0.0
        assert llm.calls == 0
        assert "No grounded facts" in result.reason

    def test_llm_failure_degrades_to_all_unknown(self):
        result = run_qualification(_research(), _icp(), FakeLLM(raises=True), _settings())
        assert result.score == 0.0
        assert result.qualified is False


class TestContextCapGuard:
    def test_large_facts_list_stays_under_context_cap(self):
        from pitch_pilot.clients.llm import CONTEXT_TOKEN_CAP
        from pitch_pilot.nodes.qualify import _QUALIFY_SYSTEM, _qualify_user_prompt

        facts = [
            Fact(claim=f"Acme fact number {i} about payments, fraud and growth at scale",
                 source_url=f"https://acme.com/some/fairly/long/path/page-{i}",
                 evidence="e", source_tier="own_site")
            for i in range(500)
        ]
        prompt = _qualify_user_prompt(_icp(), facts)
        est_tokens = (len(_QUALIFY_SYSTEM) + len(prompt)) / 4  # ~4 chars/token
        assert est_tokens < CONTEXT_TOKEN_CAP


class TestQualifyNodeAdapter:
    def test_returns_qualification_and_status(self):
        assessment = {
            "industry": {"status": "match"},
            "region": {"status": "match"},
            "employee_count": {"value": 100},
            "positive_signals": [{"signal": "recent funding", "status": "match"}],
            "negative_signals": [],
        }
        from pitch_pilot.graph.state import PipelineState

        state = PipelineState(company=Company(domain="acme.com"), icp=_icp(), research=_research())
        update = qualify_node(state, llm=FakeLLM(assessment), settings=_settings())
        assert set(update) == {"qualification", "status"}
        assert update["status"] == "qualified"
        assert update["qualification"].qualified is True
