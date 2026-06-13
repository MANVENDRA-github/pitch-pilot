"""Unit tests for the draft node. No network access.

The LLM is a fake that returns a fixed draft payload. The behaviors under test
are the groundedness guarantees the node enforces on the model's output:

* ``hooks_used`` is always a subset of the real research facts — a hook the model
  invents is discarded;
* under Policy B, NO ``third_party_snippet`` fact can become a hook — only
  ``own_site`` / ``authoritative`` facts are claimable (third-party facts may be
  passed as context but never as a stated claim);
* no claimable facts (or an LLM failure) yields an empty draft, not a crash.
"""

from __future__ import annotations

from pitch_pilot.clients.llm import LLMError
from pitch_pilot.config import Settings
from pitch_pilot.models.fact import Fact
from pitch_pilot.models.lead import Company
from pitch_pilot.models.qualification import QualificationResult
from pitch_pilot.models.research import ResearchResult
from pitch_pilot.nodes.draft import draft_node, run_draft


def _settings(**overrides) -> Settings:
    values = {"gemini_api_key": "g", "tavily_api_key": "t"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _qual() -> QualificationResult:
    return QualificationResult(qualified=True, score=0.8, reason="good fit")


# A spread of facts across tiers, with and without hard numerics.
_OWN_SOFT = Fact(claim="Acme builds developer tools", source_url="https://acme.com",
                 evidence="e", source_tier="own_site")
_OWN_NUMERIC = Fact(claim="Acme has 200 employees", source_url="https://acme.com/about",
                    evidence="e", source_tier="own_site")
_TP_NUMERIC = Fact(claim="Acme raised $50M Series B", source_url="https://news.example.com/acme",
                   evidence="e", source_tier="third_party_snippet")
_TP_SOFT = Fact(claim="Acme is popular with developers", source_url="https://blog.example.com/acme",
                evidence="e", source_tier="third_party_snippet")


def _research(facts) -> ResearchResult:
    return ResearchResult(company=Company(domain="acme.com", name="Acme"), facts=facts)


class FakeLLM:
    def __init__(self, payload=None, *, raises=False):
        self.payload = payload or {}
        self.raises = raises

    def complete(self, system, user):  # pragma: no cover - unused
        return "OK"

    def complete_json(self, system, user):
        if self.raises:
            raise LLMError("draft model down")
        return self.payload


class TestRunDraft:
    def test_hooks_are_a_subset_of_facts(self):
        llm = FakeLLM({
            "subject": "Loved your dev tools",
            "body": "Hi Acme, ...",
            "hooks": [
                "Acme builds developer tools",       # real fact
                "Acme is the #1 fintech in the world",  # fabricated
            ],
        })
        draft = run_draft(_research([_OWN_SOFT]), _qual(), llm, _settings())
        assert draft.hooks_used == ["Acme builds developer tools"]
        fact_claims = {"Acme builds developer tools"}
        assert all(h in fact_claims for h in draft.hooks_used)

    def test_refuses_third_party_numeric_but_allows_own_site_numeric(self):
        llm = FakeLLM({
            "subject": "s",
            "body": "b",
            "hooks": ["Acme raised $50M Series B", "Acme has 200 employees"],
        })
        draft = run_draft(_research([_OWN_NUMERIC, _TP_NUMERIC]), _qual(), llm, _settings())
        assert "Acme has 200 employees" in draft.hooks_used        # own_site numeric: ok
        assert "Acme raised $50M Series B" not in draft.hooks_used  # third-party: refused

    def test_third_party_fact_cannot_be_a_hook_even_when_soft(self):
        # Policy B: a third_party_snippet fact may inform context but never a claim.
        llm = FakeLLM({"subject": "s", "body": "b", "hooks": ["Acme is popular with developers"]})
        draft = run_draft(_research([_OWN_SOFT, _TP_SOFT]), _qual(), llm, _settings())
        assert "Acme is popular with developers" not in draft.hooks_used

    def test_claim_pool_excludes_all_third_party_facts(self):
        # Only first-party facts are claimable; the model offered both, hooks both.
        llm = FakeLLM({
            "subject": "s", "body": "b",
            "hooks": ["Acme builds developer tools", "Acme is popular with developers"],
        })
        draft = run_draft(_research([_OWN_SOFT, _TP_SOFT]), _qual(), llm, _settings())
        assert draft.hooks_used == ["Acme builds developer tools"]

    def test_subject_and_body_passed_through(self):
        llm = FakeLLM({"subject": "  Hello Acme  ", "body": "  Body here  ", "hooks": []})
        draft = run_draft(_research([_OWN_SOFT]), _qual(), llm, _settings())
        assert draft.subject == "Hello Acme"
        assert draft.body == "Body here"

    def test_no_claimable_facts_yields_empty_draft(self):
        # Only third-party facts -> nothing is claimable under Policy B.
        draft = run_draft(_research([_TP_NUMERIC, _TP_SOFT]), _qual(), FakeLLM(), _settings())
        assert draft.hooks_used == []
        assert draft.subject == ""

    def test_llm_failure_yields_empty_draft(self):
        draft = run_draft(_research([_OWN_SOFT]), _qual(), FakeLLM(raises=True), _settings())
        assert draft.hooks_used == []


class TestDraftNodeAdapter:
    def test_returns_draft_and_status(self):
        from pitch_pilot.graph.state import PipelineState
        from pitch_pilot.models.icp import ICP

        icp = ICP(industries=["devtools"], min_employees=10, max_employees=500,
                  regions=["US"], positive_signals=["hiring"], negative_signals=["non-profit"])
        state = PipelineState(
            company=Company(domain="acme.com", name="Acme"), icp=icp,
            research=_research([_OWN_SOFT]), qualification=_qual(),
        )
        llm = FakeLLM({"subject": "s", "body": "b", "hooks": ["Acme builds developer tools"]})
        update = draft_node(state, llm=llm, settings=_settings())
        assert set(update) == {"draft", "status"}
        assert update["status"] == "drafted"
        assert update["draft"].hooks_used == ["Acme builds developer tools"]
