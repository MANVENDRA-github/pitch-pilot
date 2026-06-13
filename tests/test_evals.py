"""Unit tests for the eval harness. No network access.

Covers the metric arithmetic, the runner's cache/resume/backoff behavior (with the
pipeline functions and LLM mocked), and that the shipped dataset loads and
validates against the `ICP` / `Company` models.
"""

from __future__ import annotations

import pytest

from evals import metrics, run_eval
from evals.run_eval import RetryingLLM, evaluate_one, load_companies, save_cached_research
from pitch_pilot.clients.llm import LLMError
from pitch_pilot.config import Settings
from pitch_pilot.models.draft import Draft
from pitch_pilot.models.fact import Fact
from pitch_pilot.models.icp import ICP, load_icp
from pitch_pilot.models.lead import Company
from pitch_pilot.models.qualification import QualificationResult
from pitch_pilot.models.research import ResearchResult
from pitch_pilot.models.verification import ClaimVerdict, VerificationResult


def _settings(**overrides) -> Settings:
    values = {"gemini_api_key": "g", "tavily_api_key": "t"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _icp() -> ICP:
    return ICP(industries=["fintech"], min_employees=50, max_employees=1000, regions=["US"],
               positive_signals=["payments"], negative_signals=["incumbent bank"])


# --- Canned result set for the metric tests: TP, FN, FP, TN, plus one error. ---
_CANNED = [
    {"status": "ok", "domain": "a", "label": "qualified", "predicted_qualified": True,
     "draft_passed": True, "groundedness_score": 1.0, "faithfulness_score": 1.0,
     "flagged_claims": [], "category": "good_fit", "fact_count": 20},
    {"status": "ok", "domain": "b", "label": "qualified", "predicted_qualified": False,
     "draft_passed": None, "groundedness_score": None, "faithfulness_score": None,
     "flagged_claims": [], "category": "good_fit", "fact_count": 18},
    {"status": "ok", "domain": "c", "label": "not_qualified", "predicted_qualified": True,
     "draft_passed": False, "groundedness_score": 0.5, "faithfulness_score": 0.5,
     "flagged_claims": ["unsupported: x", "overreach: y"], "category": "bad_fit", "fact_count": 10},
    {"status": "ok", "domain": "d", "label": "not_qualified", "predicted_qualified": False,
     "draft_passed": None, "groundedness_score": None, "faithfulness_score": None,
     "flagged_claims": [], "category": "bad_fit", "fact_count": 4},
    {"status": "error", "domain": "e", "label": "qualified", "error": "rate-limited"},
]


class TestMetrics:
    def test_qualification_precision_recall_f1(self):
        m = metrics.qualification_metrics(_CANNED)
        assert (m["tp"], m["fp"], m["tn"], m["fn"], m["n"]) == (1, 1, 1, 1, 4)  # error excluded
        assert m["precision"] == 0.5
        assert m["recall"] == 0.5
        assert m["f1"] == 0.5
        assert m["accuracy"] == 0.5

    def test_draft_pass_rate(self):
        d = metrics.draft_pass_rate(_CANNED)
        assert d == {"attempted": 2, "passed": 1, "pass_rate": 0.5}

    def test_mean_scores(self):
        s = metrics.mean_scores(_CANNED)
        assert s["n_drafted"] == 2
        assert s["mean_groundedness"] == 0.75
        assert s["mean_faithfulness"] == 0.75

    def test_failure_modes_counts_by_reason(self):
        fm = metrics.failure_modes(_CANNED)
        assert fm["unsupported"] == 1
        assert fm["overreach"] == 1
        assert fm["structural"] == 0

    def test_facts_by_category_degradation(self):
        fbc = metrics.facts_by_category(_CANNED)
        assert fbc == {"bad_fit": 7.0, "good_fit": 19.0}

    def test_aggregate_smoke(self):
        agg = metrics.aggregate(_CANNED)
        assert agg["n_ok"] == 4 and agg["n_error"] == 1
        assert agg["qualification"]["f1"] == 0.5


# --- Fakes for the runner: real models so attribute access matches production. ---
def _fake_qual(qualified=True):
    return QualificationResult(qualified=qualified, score=0.8 if qualified else 0.0, reason="fake")


def _fake_ver():
    return VerificationResult(
        groundedness_score=1.0, faithfulness_score=1.0, total_claims=1, grounded_claims=1,
        tier_breakdown={"own_site": 1},
        claim_verdicts=[ClaimVerdict(claim="h", fact_used="h", source_url="https://acme.com",
                                     tier="own_site", substring_ok=True, faithfulness="faithful")],
        flagged_claims=[], passed=True,
    )


def _patch_pipeline(monkeypatch, *, research_calls=None, qualified=True):
    """Patch the pipeline functions in run_eval with fakes; record research calls."""
    def fake_research(company, llm, search, settings):
        if research_calls is not None:
            research_calls.append(company.domain)
        return ResearchResult(company=company, facts=[])
    monkeypatch.setattr(run_eval, "run_research", fake_research)
    monkeypatch.setattr(run_eval, "run_qualification", lambda *a, **k: _fake_qual(qualified))
    monkeypatch.setattr(run_eval, "run_draft", lambda *a, **k: Draft(subject="s", body="b", hooks_used=["h"]))
    monkeypatch.setattr(run_eval, "run_verification", lambda *a, **k: _fake_ver())


class TestResearchCache:
    def test_uses_cache_and_does_not_re_research(self, monkeypatch, tmp_path):
        monkeypatch.setattr(run_eval, "CACHE_DIR", tmp_path / "cache")
        save_cached_research(ResearchResult(company=Company(domain="acme.com"), facts=[]))

        def boom(*a, **k):
            raise AssertionError("run_research must not be called when cache exists")
        monkeypatch.setattr(run_eval, "run_research", boom)
        monkeypatch.setattr(run_eval, "run_qualification", lambda *a, **k: _fake_qual(True))
        monkeypatch.setattr(run_eval, "run_draft", lambda *a, **k: Draft(subject="s", body="b", hooks_used=["h"]))
        monkeypatch.setattr(run_eval, "run_verification", lambda *a, **k: _fake_ver())

        record = evaluate_one({"domain": "acme.com", "category": "good_fit", "label": "qualified"},
                              _icp(), object(), object(), _settings())
        assert record["status"] == "ok"
        assert record["from_cache"] is True


class TestResume:
    def test_skips_domains_already_in_results(self, monkeypatch, tmp_path):
        monkeypatch.setattr(run_eval, "CACHE_DIR", tmp_path / "cache")
        results_file = tmp_path / "results.jsonl"
        results_file.write_text('{"domain": "a.com", "status": "ok"}\n', encoding="utf-8")
        calls: list[str] = []
        _patch_pipeline(monkeypatch, research_calls=calls)

        companies = [{"domain": "a.com", "category": "good_fit", "label": "qualified"},
                     {"domain": "b.com", "category": "good_fit", "label": "qualified"}]
        run_eval.run_eval(companies, _icp(), llm=object(), search=object(), settings=_settings(),
                          results_file=results_file, resume=True)
        assert calls == ["b.com"]  # a.com was skipped


class TestBackoff:
    def test_retries_rate_limit_then_succeeds(self):
        attempts = {"n": 0}

        class Inner:
            def complete_json(self, s, u):
                attempts["n"] += 1
                if attempts["n"] < 3:
                    raise LLMError("Groq request failed: Error code: 429 rate_limit_exceeded")
                return {"ok": 1}

        slept: list[float] = []
        proxy = RetryingLLM(Inner(), max_retries=5, base_delay=1.0, sleep=slept.append)
        assert proxy.complete_json("s", "u") == {"ok": 1}
        assert attempts["n"] == 3
        assert proxy.retries == 2 and proxy.gave_up is False
        assert len(slept) == 2

    def test_non_rate_limit_error_propagates_immediately(self):
        class Inner:
            def complete_json(self, s, u):
                raise LLMError("some other failure")
        proxy = RetryingLLM(Inner(), max_retries=3, sleep=lambda s: None)
        with pytest.raises(LLMError):
            proxy.complete_json("s", "u")
        assert proxy.retries == 0

    def test_persistent_rate_limit_sets_gave_up(self):
        class Inner:
            def complete_json(self, s, u):
                raise LLMError("429 too many requests")
        proxy = RetryingLLM(Inner(), max_retries=2, base_delay=0.01, sleep=lambda s: None)
        with pytest.raises(LLMError):
            proxy.complete_json("s", "u")
        assert proxy.gave_up is True
        assert proxy.retries == 2

    def test_retry_after_seconds_parsed(self):
        assert run_eval._retry_after_seconds("Please try again in 8.8s.") == 8.8
        assert run_eval._retry_after_seconds("Please retry in 45s") == 45.0
        assert run_eval._retry_after_seconds("no delay here") is None


class TestPersistentFailureContinues:
    def test_rate_limited_company_recorded_as_error_run_continues(self, monkeypatch, tmp_path):
        monkeypatch.setattr(run_eval, "CACHE_DIR", tmp_path / "cache")
        results_file = tmp_path / "results.jsonl"

        # research is fine (no LLM); qualify drives the proxy to give up.
        monkeypatch.setattr(run_eval, "run_research",
                            lambda company, llm, search, settings: ResearchResult(company=company, facts=[]))

        def qual_using_llm(research, icp, llm, settings):
            try:
                llm.complete_json("s", "u")
            except LLMError:
                pass
            return _fake_qual(False)
        monkeypatch.setattr(run_eval, "run_qualification", qual_using_llm)

        class AlwaysRateLimited:
            def complete_json(self, s, u):
                raise LLMError("429 resource_exhausted")
        proxy = RetryingLLM(AlwaysRateLimited(), max_retries=1, base_delay=0.01, sleep=lambda s: None)

        companies = [{"domain": "x.com", "category": "good_fit", "label": "qualified"},
                     {"domain": "y.com", "category": "good_fit", "label": "qualified"}]
        results = run_eval.run_eval(companies, _icp(), llm=proxy, search=object(), settings=_settings(),
                                    results_file=results_file)
        assert len(results) == 2  # run did not abort
        assert all(r["status"] == "error" for r in results)
        assert all("rate-limited" in r["error"] for r in results)


class TestRedraft:
    def test_qualification_is_frozen_and_draft_verify_recomputed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(run_eval, "CACHE_DIR", tmp_path / "cache")
        save_cached_research(ResearchResult(company=Company(domain="q.com"), facts=[]))
        monkeypatch.setattr(run_eval, "run_draft",
                            lambda *a, **k: Draft(subject="s", body="b", hooks_used=["h"]))
        monkeypatch.setattr(run_eval, "run_verification", lambda *a, **k: _fake_ver())

        records = [
            {"status": "ok", "domain": "q.com", "label": "qualified", "predicted_qualified": True,
             "score": 0.83, "qual_reason": "fit", "matched_signals": ["m"], "missed_signals": [],
             "draft_passed": False, "groundedness_score": 0.0, "fact_count": 10},
            {"status": "ok", "domain": "d.com", "label": "not_qualified", "predicted_qualified": False,
             "draft_passed": None, "groundedness_score": None},
            {"status": "error", "domain": "e.com", "error": "x"},
        ]
        out = run_eval.redraft(records, llm=object(), settings=_settings())

        q = next(r for r in out if r["domain"] == "q.com")
        # qualification fields are preserved verbatim
        assert (q["predicted_qualified"], q["score"], q["qual_reason"]) == (True, 0.83, "fit")
        assert q["matched_signals"] == ["m"]
        # draft + verify are recomputed
        assert q["draft_passed"] is True and q["groundedness_score"] == 1.0
        assert q["hooks_used"] == ["h"] and q["from_cache"] is True
        # disqualified and error records pass through untouched
        assert next(r for r in out if r["domain"] == "d.com")["draft_passed"] is None
        assert next(r for r in out if r["domain"] == "e.com")["status"] == "error"

    def test_missing_cache_leaves_record_unchanged(self, monkeypatch, tmp_path):
        monkeypatch.setattr(run_eval, "CACHE_DIR", tmp_path / "cache")

        def boom(*a, **k):
            raise AssertionError("must not draft without cached research")
        monkeypatch.setattr(run_eval, "run_draft", boom)

        records = [{"status": "ok", "domain": "nocache.com", "predicted_qualified": True,
                    "draft_passed": False, "score": 0.7, "qual_reason": "fit"}]
        out = run_eval.redraft(records, llm=object(), settings=_settings())
        assert out[0]["draft_passed"] is False  # unchanged (no cached research)


class TestRecheck:
    def _setup(self):
        research = ResearchResult(company=Company(domain="acme.com"), facts=[
            Fact(claim="A", source_url="https://acme.com", evidence="alpha", source_tier="own_site"),
        ])
        results = [{"status": "ok", "domain": "acme.com", "claim_verdicts": [
            {"fact_used": "A", "source_url": "https://acme.com", "tier": "own_site"},
        ]}]
        return research, results

    def test_recheck_confirms_evidence_on_live_page_by_tier(self):
        research, results = self._setup()
        out = run_eval.recheck(results, load_research=lambda d: research,
                               fetch=lambda u: "intro alpha outro", cache={})
        assert out["own_site"] == {"checked": 1, "present": 1, "dead": 0, "rate": 1.0}

    def test_recheck_flags_missing_evidence(self):
        research, results = self._setup()
        out = run_eval.recheck(results, load_research=lambda d: research,
                               fetch=lambda u: "the page changed", cache={})
        assert out["own_site"]["present"] == 0 and out["own_site"]["rate"] == 0.0

    def test_recheck_cache_only_does_not_fetch(self):
        research, results = self._setup()
        fetched: list[str] = []
        out = run_eval.recheck(results, load_research=lambda d: research,
                               fetch=lambda u: fetched.append(u) or "", cache={}, allow_fetch=False)
        assert fetched == []  # nothing fetched
        assert out == {}      # uncached source skipped


class TestDataset:
    def test_eval_icp_loads_and_validates(self):
        icp = load_icp("examples/eval_icp.json")
        assert isinstance(icp, ICP)
        assert "fintech" in icp.industries

    def test_eval_companies_load_and_construct(self):
        companies = load_companies("examples/eval_companies.json")
        assert len(companies) >= 15
        for c in companies:
            Company(domain=c["domain"])  # must construct
            assert c["label"] in {"qualified", "not_qualified"}
            assert c["category"] in {"good_fit", "bad_fit", "sparse"}
