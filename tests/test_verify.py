"""Unit tests for the P5 verify node (the real groundedness gate). No network.

The LLM body-faithfulness judge is replaced with a fake that returns scripted
per-claim verdicts for the draft body. The behaviors under test:

* a faithful body claim grounded in a first-party hook verifies and the draft passes;
* an ``unsupported`` body claim fails the claim and the draft;
* an ``overreach`` claim fails under ``FAITHFULNESS_STRICT=True`` and passes when off;
* a hook that does not resolve to a first-party fact is a ``structural`` failure
  (and the judge is never called);
* an empty body, or no grounded hooks, never passes;
* a judge error / malformed response fails closed;
* ``groundedness_score``, ``faithfulness_score``, and ``tier_breakdown`` are computed
  per the P5 definitions (groundedness = faithful body claims / total body claims;
  tier_breakdown counts the grounding hooks).
"""

from __future__ import annotations

from pitch_pilot.clients.llm import LLMError
from pitch_pilot.config import Settings
from pitch_pilot.models.draft import Draft
from pitch_pilot.models.fact import Fact
from pitch_pilot.models.lead import Company
from pitch_pilot.models.research import ResearchResult
from pitch_pilot.nodes.verify import judge_body, run_verification, verify_node


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


class FakeJudge:
    """Scriptable body-faithfulness judge: returns a fixed list of claim verdicts."""

    def __init__(self, claims=None, *, raises=False, malformed=False):
        self.claims = claims if claims is not None else [
            {"claim": "body claim", "verdict": "faithful", "fact_id": 1}
        ]
        self.raises = raises
        self.malformed = malformed
        self.calls: list[str] = []

    def complete(self, system, user, temperature=None):  # pragma: no cover - unused
        return "OK"

    def complete_json(self, system, user, temperature=None):
        self.calls.append(user)
        self.temperature = temperature
        if self.raises:
            raise LLMError("judge down")
        if self.malformed:
            return {"not_claims": []}
        return {"claims": self.claims}


def _draft(*hooks, body="Hi there, here is the body.") -> Draft:
    return Draft(subject="s", body=body, hooks_used=list(hooks))


class TestGate:
    def test_faithful_claim_verifies_and_passes(self):
        judge = FakeJudge([{"claim": "Acme builds dev tools", "verdict": "faithful", "fact_id": 1}])
        result = run_verification(_draft("Acme builds developer tools"), _research([_OWN]), judge, _settings())
        assert result.passed is True
        assert result.groundedness_score == 1.0
        assert result.faithfulness_score == 1.0
        assert result.grounded_claims == 1
        assert result.flagged_claims == []
        assert result.tier_breakdown == {"own_site": 1}
        cv = result.claim_verdicts[0]
        assert cv.tier == "own_site" and cv.fact_used == "Acme builds developer tools"
        assert cv.faithfulness == "faithful" and cv.source_url == "https://acme.com"

    def test_authoritative_tier_is_first_party(self):
        judge = FakeJudge([{"claim": "They filed an S-1", "verdict": "faithful", "fact_id": 1}])
        result = run_verification(_draft("Acme filed an S-1"), _research([_AUTH]), judge, _settings())
        assert result.passed is True
        assert result.tier_breakdown == {"authoritative": 1}

    def test_unsupported_claim_fails_draft(self):
        judge = FakeJudge([{"claim": "Acme cures cancer", "verdict": "unsupported", "fact_id": None}])
        result = run_verification(_draft("Acme builds developer tools"), _research([_OWN]), judge, _settings())
        assert result.passed is False
        assert result.groundedness_score == 0.0
        assert result.faithfulness_score == 0.0
        assert any(f.startswith("unsupported:") for f in result.flagged_claims)
        cv = result.claim_verdicts[0]
        assert cv.fact_used is None and cv.tier is None  # unsupported -> no backing fact

    def test_overreach_fails_when_strict(self):
        judge = FakeJudge([{"claim": "Acme is the #1 dev tool", "verdict": "overreach", "fact_id": 1}])
        result = run_verification(_draft("Acme builds developer tools"), _research([_OWN]),
                                  judge, _settings(faithfulness_strict=True))
        assert result.passed is False
        assert any(f.startswith("overreach:") for f in result.flagged_claims)
        assert result.faithfulness_score == 0.0  # overreach is not "faithful"
        assert result.groundedness_score == 0.0  # ...and not verified under strict

    def test_overreach_passes_when_lenient(self):
        judge = FakeJudge([{"claim": "Acme is the #1 dev tool", "verdict": "overreach", "fact_id": 1}])
        result = run_verification(_draft("Acme builds developer tools"), _research([_OWN]),
                                  judge, _settings(faithfulness_strict=False))
        assert result.passed is True
        assert result.groundedness_score == 1.0   # overreach counts as verified when lenient
        assert result.faithfulness_score == 0.0   # ...but still not "faithful"
        assert result.claim_verdicts[0].faithfulness == "overreach"

    def test_unresolvable_hook_is_structural_failure(self):
        # The only fact is third-party, so the hook cannot resolve to a first-party
        # fact -> structural failure, and the body judge is never called.
        judge = FakeJudge()
        result = run_verification(_draft("Acme is popular with developers"), _research([_TP]), judge, _settings())
        assert result.passed is False
        assert any(f.startswith("structural:") for f in result.flagged_claims)
        assert result.tier_breakdown == {}
        assert judge.calls == []

    def test_own_site_chosen_over_third_party_for_same_claim(self):
        tp_same = Fact(claim="Acme builds developer tools", source_url="https://x.com/acme",
                       evidence="builds developer tools", source_tier="third_party_snippet")
        judge = FakeJudge([{"claim": "Acme builds dev tools", "verdict": "faithful", "fact_id": 1}])
        result = run_verification(_draft("Acme builds developer tools"),
                                  _research([tp_same, _OWN]), judge, _settings())
        assert result.passed is True
        assert result.tier_breakdown == {"own_site": 1}
        assert result.claim_verdicts[0].source_url == "https://acme.com"

    def test_empty_body_does_not_pass(self):
        judge = FakeJudge()
        result = run_verification(_draft("Acme builds developer tools", body="   "),
                                  _research([_OWN]), judge, _settings())
        assert result.passed is False
        assert result.groundedness_score == 0.0
        assert judge.calls == []  # nothing to judge

    def test_no_hooks_does_not_pass(self):
        judge = FakeJudge()
        result = run_verification(_draft(), _research([_OWN]), judge, _settings())
        assert result.passed is False
        assert result.total_claims == 0
        assert result.groundedness_score == 0.0
        assert judge.calls == []

    def test_judge_failure_fails_closed(self):
        judge = FakeJudge(raises=True)
        result = run_verification(_draft("Acme builds developer tools"), _research([_OWN]), judge, _settings())
        assert result.passed is False
        assert any(f.startswith("judge-error:") for f in result.flagged_claims)
        assert result.groundedness_score == 0.0

    def test_malformed_judge_fails_closed(self):
        judge = FakeJudge(malformed=True)
        result = run_verification(_draft("Acme builds developer tools"), _research([_OWN]), judge, _settings())
        assert result.passed is False
        assert any(f.startswith("judge-error:") for f in result.flagged_claims)

    def test_scores_and_tier_breakdown_across_a_mix(self):
        # 2 grounding hooks (own_site x2). 3 body claims: faithful, overreach, unsupported.
        # verified=1/3, faithful=1/3; tier_breakdown counts the hooks, not the claims.
        judge = FakeJudge([
            {"claim": "Acme builds dev tools", "verdict": "faithful", "fact_id": 1},
            {"claim": "Acme is the best employer", "verdict": "overreach", "fact_id": 2},
            {"claim": "Acme is profitable", "verdict": "unsupported", "fact_id": None},
        ])
        draft = _draft("Acme builds developer tools", "Acme is hiring engineers")
        result = run_verification(draft, _research([_OWN, _OWN2]), judge, _settings())
        assert result.total_claims == 3
        assert result.grounded_claims == 1
        assert result.groundedness_score == 0.3333
        assert result.faithfulness_score == 0.3333
        assert result.tier_breakdown == {"own_site": 2}
        assert result.passed is False

    def test_judge_uses_zero_temperature(self):
        judge = FakeJudge([{"claim": "c", "verdict": "faithful", "fact_id": 1}])
        run_verification(_draft("Acme builds developer tools"), _research([_OWN]), judge, _settings())
        assert judge.temperature == 0.0


class TestJudgeBody:
    def test_unknown_verdict_defaults_to_unsupported(self):
        class Weird:
            def complete_json(self, s, u, temperature=None):
                return {"claims": [{"claim": "c", "verdict": "banana", "fact_id": 1}]}
        ok, claims = judge_body("body", [_OWN], Weird())
        assert ok is True
        assert claims[0]["verdict"] == "unsupported"
        assert claims[0]["fact"] is None  # unsupported -> no backing fact resolved

    def test_judge_failure_returns_not_ok(self):
        class Boom:
            def complete_json(self, s, u, temperature=None):
                raise LLMError("down")
        ok, claims = judge_body("body", [_OWN], Boom())
        assert ok is False and claims == []

    def test_resolves_fact_id_to_fact(self):
        class Good:
            def complete_json(self, s, u, temperature=None):
                return {"claims": [{"claim": "c", "verdict": "faithful", "fact_id": 2}]}
        ok, claims = judge_body("body", [_OWN, _OWN2], Good())
        assert ok is True
        assert claims[0]["fact"] is _OWN2


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
