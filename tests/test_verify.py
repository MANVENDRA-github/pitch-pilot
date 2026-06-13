"""Unit tests for the P3 verify node (the real groundedness gate). No network.

The LLM faithfulness judge is replaced with a fake that returns a scripted verdict
per claim. The behaviors under test:

* a faithful, first-party, substring-anchored claim verifies and the draft passes;
* an ``unsupported`` verdict fails the claim and the draft;
* an ``overreach`` verdict fails under ``FAITHFULNESS_STRICT=True`` and passes when
  it is off;
* a claim backed only by a ``third_party_snippet`` fact is a hard policy failure
  (and the judge is never even called);
* a claim whose backing fact has no evidence fails the substring check;
* an unbacked claim fails;
* ``groundedness_score``, ``faithfulness_score``, and ``tier_breakdown`` are
  computed correctly across a mix.
"""

from __future__ import annotations

from pitch_pilot.config import Settings
from pitch_pilot.models.draft import Draft
from pitch_pilot.models.fact import Fact
from pitch_pilot.models.lead import Company
from pitch_pilot.models.research import ResearchResult
from pitch_pilot.nodes.verify import judge_faithfulness, run_verification, verify_node


def _settings(**overrides) -> Settings:
    values = {
        "gemini_api_key": "g", "tavily_api_key": "t",
        "groundedness_threshold": 0.9, "faithfulness_strict": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _research(facts) -> ResearchResult:
    return ResearchResult(company=Company(domain="acme.com"), facts=facts)


_OWN = Fact(claim="Acme builds developer tools", source_url="https://acme.com",
            evidence="Acme builds developer tools", source_tier="own_site")
_OWN2 = Fact(claim="Acme is hiring engineers", source_url="https://acme.com/jobs",
             evidence="hiring engineers", source_tier="own_site")
_AUTH = Fact(claim="Acme filed an S-1", source_url="https://sec.gov/acme",
             evidence="S-1", source_tier="authoritative")
_TP = Fact(claim="Acme is popular with developers", source_url="https://blog.example.com/acme",
           evidence="popular with developers", source_tier="third_party_snippet")
_OWN_NO_EVIDENCE = Fact(claim="Acme is the best", source_url="https://acme.com/x",
                        evidence="", source_tier="own_site")


class FakeJudge:
    """Scriptable faithfulness judge: maps claim text -> verdict (default faithful)."""

    def __init__(self, verdicts=None, default="faithful"):
        self.verdicts = verdicts or {}
        self.default = default
        self.calls: list[str] = []

    def complete(self, system, user):  # pragma: no cover - unused
        return "OK"

    def complete_json(self, system, user):
        claim = user.split("CLAIM:", 1)[1].split("\n", 1)[0].strip()
        self.calls.append(claim)
        return {"verdict": self.verdicts.get(claim, self.default), "reason": "test"}


def _draft(*hooks) -> Draft:
    return Draft(subject="s", body="b", hooks_used=list(hooks))


class TestGate:
    def test_faithful_first_party_claim_verifies_and_passes(self):
        judge = FakeJudge(default="faithful")
        result = run_verification(_draft("Acme builds developer tools"), _research([_OWN]), judge, _settings())
        assert result.passed is True
        assert result.groundedness_score == 1.0
        assert result.faithfulness_score == 1.0
        assert result.grounded_claims == 1
        assert result.flagged_claims == []
        assert result.tier_breakdown == {"own_site": 1}
        cv = result.claim_verdicts[0]
        assert cv.tier == "own_site" and cv.substring_ok is True and cv.faithfulness == "faithful"

    def test_authoritative_tier_is_first_party(self):
        result = run_verification(_draft("Acme filed an S-1"), _research([_AUTH]), FakeJudge(), _settings())
        assert result.passed is True
        assert result.tier_breakdown == {"authoritative": 1}

    def test_unsupported_verdict_fails_claim_and_draft(self):
        judge = FakeJudge({"Acme builds developer tools": "unsupported"})
        result = run_verification(_draft("Acme builds developer tools"), _research([_OWN]), judge, _settings())
        assert result.passed is False
        assert result.groundedness_score == 0.0
        assert result.faithfulness_score == 0.0
        assert any(f.startswith("unsupported:") for f in result.flagged_claims)

    def test_overreach_fails_when_strict(self):
        judge = FakeJudge({"Acme builds developer tools": "overreach"})
        result = run_verification(_draft("Acme builds developer tools"), _research([_OWN]),
                                  judge, _settings(faithfulness_strict=True))
        assert result.passed is False
        assert any(f.startswith("overreach:") for f in result.flagged_claims)
        assert result.faithfulness_score == 0.0  # overreach is not "faithful"

    def test_overreach_passes_when_lenient(self):
        judge = FakeJudge({"Acme builds developer tools": "overreach"})
        result = run_verification(_draft("Acme builds developer tools"), _research([_OWN]),
                                  judge, _settings(faithfulness_strict=False))
        assert result.passed is True
        assert result.groundedness_score == 1.0       # overreach counts as verified when lenient
        assert result.faithfulness_score == 0.0       # ...but still not "faithful"
        assert result.claim_verdicts[0].faithfulness == "overreach"

    def test_third_party_only_claim_is_hard_policy_failure(self):
        judge = FakeJudge()
        result = run_verification(_draft("Acme is popular with developers"), _research([_TP]), judge, _settings())
        assert result.passed is False
        assert any(f.startswith("volatile-source:") for f in result.flagged_claims)
        assert result.tier_breakdown == {"third_party_snippet": 1}
        assert judge.calls == []  # policy fails before we bother judging faithfulness

    def test_own_site_chosen_over_third_party_for_same_claim(self):
        # Same claim from both tiers -> the own_site fact is chosen, so it verifies.
        tp_same = Fact(claim="Acme builds developer tools", source_url="https://x.com/acme",
                       evidence="builds developer tools", source_tier="third_party_snippet")
        result = run_verification(_draft("Acme builds developer tools"),
                                  _research([tp_same, _OWN]), FakeJudge(), _settings())
        assert result.passed is True
        assert result.claim_verdicts[0].tier == "own_site"

    def test_substring_mismatch_fails(self):
        judge = FakeJudge()
        result = run_verification(_draft("Acme is the best"), _research([_OWN_NO_EVIDENCE]), judge, _settings())
        assert result.passed is False
        assert any(f.startswith("not-substring:") for f in result.flagged_claims)
        assert result.claim_verdicts[0].substring_ok is False
        assert judge.calls == []  # no evidence -> nothing to judge

    def test_unbacked_claim_fails(self):
        judge = FakeJudge()
        result = run_verification(_draft("Acme invented time travel"), _research([_OWN]), judge, _settings())
        assert result.passed is False
        assert any(f.startswith("unbacked:") for f in result.flagged_claims)
        assert result.tier_breakdown == {"unbacked": 1}
        assert judge.calls == []

    def test_empty_draft_does_not_pass(self):
        result = run_verification(_draft(), _research([_OWN]), FakeJudge(), _settings())
        assert result.passed is False
        assert result.total_claims == 0
        assert result.groundedness_score == 0.0
        assert result.faithfulness_score == 0.0

    def test_scores_and_tier_breakdown_across_a_mix(self):
        # 3 claims: own_site faithful (verified), own_site overreach (fail, strict),
        # third_party (volatile fail). verified=1/3, faithful=1/3.
        judge = FakeJudge({
            "Acme builds developer tools": "faithful",
            "Acme is hiring engineers": "overreach",
        })
        draft = _draft("Acme builds developer tools", "Acme is hiring engineers",
                       "Acme is popular with developers")
        result = run_verification(draft, _research([_OWN, _OWN2, _TP]), judge, _settings())
        assert result.total_claims == 3
        assert result.grounded_claims == 1
        assert result.groundedness_score == 0.3333
        assert result.faithfulness_score == 0.3333
        assert result.tier_breakdown == {"own_site": 2, "third_party_snippet": 1}
        assert result.passed is False


class TestJudgeFaithfulness:
    def test_unknown_verdict_defaults_to_unsupported(self):
        class Weird:
            def complete_json(self, s, u):
                return {"verdict": "banana", "reason": "?"}
        assert judge_faithfulness("c", "e", Weird())["verdict"] == "unsupported"

    def test_judge_failure_fails_closed(self):
        from pitch_pilot.clients.llm import LLMError

        class Boom:
            def complete_json(self, s, u):
                raise LLMError("down")
        assert judge_faithfulness("c", "e", Boom())["verdict"] == "unsupported"


class TestVerifyNodeAdapter:
    def test_returns_verification(self):
        from pitch_pilot.graph.state import PipelineState
        from pitch_pilot.models.icp import ICP

        icp = ICP(industries=["devtools"], min_employees=10, max_employees=500,
                  regions=["US"], positive_signals=["hiring"], negative_signals=["non-profit"])
        state = PipelineState(
            company=Company(domain="acme.com"), icp=icp, research=_research([_OWN]),
            draft=_draft("Acme builds developer tools"),
        )
        update = verify_node(state, llm=FakeJudge(), settings=_settings())
        assert set(update) == {"verification"}
        assert update["verification"].passed is True
