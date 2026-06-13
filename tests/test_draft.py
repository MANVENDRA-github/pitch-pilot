"""Unit tests for the draft node. No network access.

The LLM is a fake that returns a fixed draft payload. The behaviors under test
are the groundedness guarantees the node enforces on the model's output (P5,
selection-by-id — see ADR-0014):

* ``hooks_used`` resolves the model's selected fact **ids** to real research facts —
  an out-of-range or invented id is dropped;
* under Policy B, NO ``third_party_snippet`` fact is even claimable, so it can never
  become a hook (third-party facts may be passed as context but never selected),
  even if the model echoes the claim text verbatim;
* a verbatim echo of a *claimable* fact's claim resolves as a defensive fallback;
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
    def test_selected_ids_resolve_to_facts(self):
        # claimable = [_OWN_SOFT] -> id 1; id 5 is out of range and is dropped.
        llm = FakeLLM({"subject": "s", "body": "b", "facts_used": [1, 5]})
        draft = run_draft(_research([_OWN_SOFT]), _qual(), llm, _settings())
        assert draft.hooks_used == ["Acme builds developer tools"]

    def test_invalid_selection_yields_no_hooks(self):
        llm = FakeLLM({"subject": "s", "body": "b", "facts_used": [99]})
        draft = run_draft(_research([_OWN_SOFT]), _qual(), llm, _settings())
        assert draft.hooks_used == []

    def test_only_own_site_numeric_is_claimable_not_third_party(self):
        # The third-party numeric is not claimable, so id 1 is the own_site fact.
        llm = FakeLLM({"subject": "s", "body": "b", "facts_used": [1]})
        draft = run_draft(_research([_OWN_NUMERIC, _TP_NUMERIC]), _qual(), llm, _settings())
        assert "Acme has 200 employees" in draft.hooks_used        # own_site numeric: ok
        assert "Acme raised $50M Series B" not in draft.hooks_used  # third-party: not claimable

    def test_third_party_claim_echo_is_never_a_hook(self):
        # Policy B: even if the model echoes a third_party_snippet fact's claim text,
        # it is not claimable and can never become a hook.
        llm = FakeLLM({"subject": "s", "body": "b",
                       "facts_used": ["Acme is popular with developers"]})
        draft = run_draft(_research([_OWN_SOFT, _TP_SOFT]), _qual(), llm, _settings())
        assert draft.hooks_used == []

    def test_verbatim_claim_echo_resolves_as_fallback(self):
        # A model that echoes a claimable fact's exact claim (not its id) still resolves.
        llm = FakeLLM({"subject": "s", "body": "b",
                       "facts_used": ["Acme builds developer tools"]})
        draft = run_draft(_research([_OWN_SOFT, _TP_SOFT]), _qual(), llm, _settings())
        assert draft.hooks_used == ["Acme builds developer tools"]

    def test_hooks_are_deduped_preserving_order(self):
        llm = FakeLLM({"subject": "s", "body": "b", "facts_used": [2, 1, 1, 2]})
        draft = run_draft(_research([_OWN_SOFT, _OWN_NUMERIC]), _qual(), llm, _settings())
        assert draft.hooks_used == ["Acme has 200 employees", "Acme builds developer tools"]

    def test_subject_and_body_passed_through(self):
        llm = FakeLLM({"subject": "  Hello Acme  ", "body": "  Body here  ", "facts_used": []})
        draft = run_draft(_research([_OWN_SOFT]), _qual(), llm, _settings())
        assert draft.subject == "Hello Acme"
        assert draft.body == "Body here"
        assert draft.hooks_used == []

    def test_no_claimable_facts_yields_empty_draft(self):
        # Only third-party facts -> nothing is claimable under Policy B.
        draft = run_draft(_research([_TP_NUMERIC, _TP_SOFT]), _qual(), FakeLLM(), _settings())
        assert draft.hooks_used == []
        assert draft.subject == ""

    def test_llm_failure_yields_empty_draft(self):
        draft = run_draft(_research([_OWN_SOFT]), _qual(), FakeLLM(raises=True), _settings())
        assert draft.hooks_used == []


class TestContextCapGuard:
    def test_large_facts_list_stays_under_context_cap(self):
        from pitch_pilot.clients.llm import CONTEXT_TOKEN_CAP
        from pitch_pilot.nodes.draft import (
            _DRAFT_SYSTEM,
            _claimable_facts,
            _context_facts,
            _draft_user_prompt,
        )

        own = [Fact(claim=f"Acme ships API product feature {i} at scale",
                    source_url=f"https://acme.com/long/path/page-{i}", evidence="e",
                    source_tier="own_site") for i in range(300)]
        tp = [Fact(claim=f"Third party says thing {i} about Acme",
                   source_url=f"https://blog.example.com/post-{i}", evidence="e",
                   source_tier="third_party_snippet") for i in range(300)]
        facts = own + tp
        prompt = _draft_user_prompt("Acme", _claimable_facts(facts), _context_facts(facts), _qual())
        est_tokens = (len(_DRAFT_SYSTEM) + len(prompt)) / 4
        assert est_tokens < CONTEXT_TOKEN_CAP


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
        llm = FakeLLM({"subject": "s", "body": "b", "facts_used": [1]})
        update = draft_node(state, llm=llm, settings=_settings())
        assert set(update) == {"draft", "status"}
        assert update["status"] == "drafted"
        assert update["draft"].hooks_used == ["Acme builds developer tools"]
