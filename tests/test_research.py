"""Unit tests for the agentic research node. No network access.

The LLM and search clients are replaced with scriptable fakes, and the seed
``fetch_page`` is monkeypatched, so every test runs offline. The behaviors under
test are the ones that make research *grounded* and *agentic*:

* every returned `Fact` carries an ``http(s)`` ``source_url`` **and** a non-empty
  ``evidence`` snippet;
* the extractor drops candidate facts whose evidence is not found in the source;
* the loop's query sequence is LLM-chosen and hard-capped by
  ``RESEARCH_MAX_QUERIES``;
* the loop stops early when the planner says it is done;
* empty searches and failed fetches are recorded as errors, never crash.
"""

from __future__ import annotations

import pitch_pilot.nodes.research as research_module
from pitch_pilot.config import Settings
from pitch_pilot.models.lead import Company
from pitch_pilot.models.research import ResearchResult
from pitch_pilot.models.search import SearchResult
from pitch_pilot.nodes.research import extract_facts, run_research


def _settings(**overrides) -> Settings:
    """Build a Settings object directly (no .env, no network)."""
    values = {
        "gemini_api_key": "g",
        "tavily_api_key": "t",
        "llm_provider": "gemini",
        "research_max_queries": 4,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeLLM:
    """A scriptable `LLMClient`.

    ``complete_json`` routes by the user prompt's prefix: extractor prompts start
    with ``"SOURCE URL:"`` and return the facts mapped to that URL; planner
    prompts start with ``"COMPANY:"`` and return the next scripted plan (or, in
    ``always_query`` mode, a fresh non-repeating query every time).
    """

    def __init__(self, *, plans=None, extracts=None, always_query=False):
        self.plans = list(plans or [])
        self.extracts = dict(extracts or {})  # url -> list[dict]
        self.always_query = always_query
        self.plan_call_count = 0
        self.extract_urls: list[str] = []

    def complete(self, system: str, user: str) -> str:  # pragma: no cover - unused
        return "OK"

    def complete_json(self, system: str, user: str) -> dict:
        if user.startswith("SOURCE URL:"):
            url = user.split("SOURCE URL:", 1)[1].splitlines()[0].strip()
            self.extract_urls.append(url)
            return {"facts": self.extracts.get(url, [])}
        # Otherwise it's a planner call.
        self.plan_call_count += 1
        if self.always_query:
            return {
                "done": False,
                "reason": "still thin",
                "next_query": f"query-{self.plan_call_count}",
            }
        if self.plans:
            return self.plans.pop(0)
        return {"done": True, "reason": "exhausted", "next_query": None}


class FakeSearch:
    """A scriptable `SearchClient` that records the queries it was asked to run."""

    def __init__(self, *, results_by_query=None, default=None, raises=False):
        self.results_by_query = results_by_query or {}
        self.default = list(default) if default is not None else []
        self.raises = raises
        self.queries: list[str] = []

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        self.queries.append(query)
        if self.raises:
            raise RuntimeError("search backend down")
        return list(self.results_by_query.get(query, self.default))


class TestExtractor:
    def test_drops_facts_whose_evidence_is_not_in_source(self):
        text = "Acme is a fintech company based in Berlin."
        llm = FakeLLM(
            extracts={
                "https://acme.com": [
                    {  # grounded — evidence is a real substring
                        "claim": "Acme is a fintech company",
                        "evidence": "fintech company",
                        "category": "overview",
                        "confidence": 0.9,
                    },
                    {  # hallucinated — evidence is nowhere in the text
                        "claim": "Acme raised a $50M Series C",
                        "evidence": "raised a $50M Series C",
                        "category": "news",
                        "confidence": 0.9,
                    },
                ]
            }
        )
        facts = extract_facts(text, "https://acme.com", "Acme", llm)
        assert len(facts) == 1
        assert facts[0].claim == "Acme is a fintech company"
        assert facts[0].evidence == "fintech company"

    def test_evidence_match_is_whitespace_and_case_insensitive(self):
        text = "Acme\n  is   HIRING   software engineers."
        llm = FakeLLM(
            extracts={
                "https://acme.com/jobs": [
                    {
                        "claim": "Acme is hiring engineers",
                        "evidence": "is hiring software engineers",
                        "category": "hiring",
                        "confidence": 0.8,
                    }
                ]
            }
        )
        facts = extract_facts(text, "https://acme.com/jobs", None, llm)
        assert len(facts) == 1

    def test_empty_text_yields_no_facts_and_no_llm_call(self):
        llm = FakeLLM()
        assert extract_facts("", "https://acme.com", None, llm) == []
        assert llm.extract_urls == []  # never called the model

    def test_invalid_category_is_dropped_to_none(self):
        text = "Acme uses Python and Postgres."
        llm = FakeLLM(
            extracts={
                "https://acme.com": [
                    {
                        "claim": "Acme uses Python",
                        "evidence": "uses Python",
                        "category": "made-up-category",
                        "confidence": 0.7,
                    }
                ]
            }
        )
        facts = extract_facts(text, "https://acme.com", None, llm)
        assert len(facts) == 1
        assert facts[0].category is None

    def test_per_source_cap_is_enforced(self):
        # Build more groundable candidates than the cap allows.
        sentences = [f"Fact number {i} is stated here." for i in range(20)]
        text = " ".join(sentences)
        llm = FakeLLM(
            extracts={
                "https://acme.com": [
                    {
                        "claim": f"claim {i}",
                        "evidence": f"Fact number {i} is stated here.",
                        "category": "overview",
                        "confidence": 0.6,
                    }
                    for i in range(20)
                ]
            }
        )
        facts = extract_facts(text, "https://acme.com", None, llm)
        assert len(facts) == research_module.MAX_FACTS_PER_SOURCE


class TestRunResearch:
    def test_every_fact_has_http_source_and_evidence(self, monkeypatch):
        seed_text = "Acme builds developer tools. Acme is a fintech company."
        monkeypatch.setattr(research_module, "fetch_page", lambda *a, **k: seed_text)

        news_result = SearchResult(
            title="Acme raises round",
            url="https://news.example.com/acme",
            content="Acme raised $10M in 2026 to expand its platform.",
        )
        llm = FakeLLM(
            plans=[
                {"done": False, "reason": "need news", "next_query": "Acme funding news"},
                {"done": True, "reason": "enough coverage", "next_query": None},
            ],
            extracts={
                "https://acme.com": [
                    {
                        "claim": "Acme builds developer tools",
                        "evidence": "Acme builds developer tools",
                        "category": "overview",
                        "confidence": 0.9,
                    }
                ],
                "https://news.example.com/acme": [
                    {
                        "claim": "Acme raised $10M in 2026",
                        "evidence": "Acme raised $10M in 2026",
                        "category": "news",
                        "confidence": 0.8,
                    }
                ],
            },
        )
        search = FakeSearch(results_by_query={"Acme funding news": [news_result]})

        result = run_research(Company(domain="acme.com"), llm, search, _settings())

        assert isinstance(result, ResearchResult)
        assert len(result.facts) == 2
        for fact in result.facts:
            assert fact.source_url.startswith(("http://", "https://"))
            assert fact.evidence  # non-empty evidence snippet
        assert result.source_count == 2
        assert result.queries_run == ["Acme funding news"]

    def test_query_sequence_is_llm_chosen(self, monkeypatch):
        # No seed text — isolate the planner-driven query sequence.
        monkeypatch.setattr(research_module, "fetch_page", lambda *a, **k: "")
        llm = FakeLLM(
            plans=[
                {"done": False, "reason": "a", "next_query": "what does acme do"},
                {"done": False, "reason": "b", "next_query": "acme hiring 2026"},
                {"done": True, "reason": "done", "next_query": None},
            ]
        )
        search = FakeSearch(default=[])  # results don't matter here

        result = run_research(Company(domain="acme.com"), llm, search, _settings())

        # The exact sequence the planner chose, in order, is what ran.
        assert result.queries_run == ["what does acme do", "acme hiring 2026"]
        assert search.queries == ["what does acme do", "acme hiring 2026"]

    def test_loop_respects_max_queries_budget(self, monkeypatch):
        monkeypatch.setattr(research_module, "fetch_page", lambda *a, **k: "")
        # Planner always wants another (unique) query; the budget must stop it.
        llm = FakeLLM(always_query=True)
        search = FakeSearch(default=[])
        settings = _settings(research_max_queries=3)

        result = run_research(Company(domain="acme.com"), llm, search, settings)

        assert len(result.queries_run) == 3
        assert result.queries_run == ["query-1", "query-2", "query-3"]
        assert len(search.queries) == 3

    def test_loop_stops_early_when_planner_done(self, monkeypatch):
        seed_text = "Acme builds developer tools."
        monkeypatch.setattr(research_module, "fetch_page", lambda *a, **k: seed_text)
        llm = FakeLLM(
            plans=[{"done": True, "reason": "seed is enough", "next_query": None}],
            extracts={
                "https://acme.com": [
                    {
                        "claim": "Acme builds developer tools",
                        "evidence": "Acme builds developer tools",
                        "category": "overview",
                        "confidence": 0.9,
                    }
                ]
            },
        )
        search = FakeSearch(default=[SearchResult(title="x", url="https://x.com", content="x")])

        result = run_research(Company(domain="acme.com"), llm, search, _settings())

        assert result.queries_run == []  # planner stopped before any search
        assert search.queries == []  # search was never called
        assert len(result.facts) == 1  # but the seed facts are present

    def test_empty_search_results_recorded_not_crashed(self, monkeypatch):
        monkeypatch.setattr(research_module, "fetch_page", lambda *a, **k: "")
        llm = FakeLLM(
            plans=[
                {"done": False, "reason": "go", "next_query": "acme news"},
                {"done": True, "reason": "stop", "next_query": None},
            ]
        )
        search = FakeSearch(results_by_query={"acme news": []})  # empty results

        result = run_research(Company(domain="acme.com"), llm, search, _settings())

        assert result.facts == []
        assert result.queries_run == ["acme news"]
        assert any("no search results" in err for err in result.errors)

    def test_search_backend_error_is_recorded_not_crashed(self, monkeypatch):
        monkeypatch.setattr(research_module, "fetch_page", lambda *a, **k: "")
        llm = FakeLLM(
            plans=[
                {"done": False, "reason": "go", "next_query": "acme news"},
                {"done": True, "reason": "stop", "next_query": None},
            ]
        )
        search = FakeSearch(raises=True)

        result = run_research(Company(domain="acme.com"), llm, search, _settings())

        assert result.queries_run == ["acme news"]
        assert any("search failed" in err for err in result.errors)

    def test_failed_seed_fetch_is_recorded_not_crashed(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr(research_module, "fetch_page", _boom)
        llm = FakeLLM(plans=[{"done": True, "reason": "nothing to do", "next_query": None}])
        search = FakeSearch(default=[])

        result = run_research(Company(domain="acme.com"), llm, search, _settings())

        assert result.facts == []
        assert any("seed fetch raised" in err for err in result.errors)

    def test_empty_seed_text_is_recorded(self, monkeypatch):
        monkeypatch.setattr(research_module, "fetch_page", lambda *a, **k: "")
        llm = FakeLLM(plans=[{"done": True, "reason": "done", "next_query": None}])
        search = FakeSearch(default=[])

        result = run_research(Company(domain="acme.com"), llm, search, _settings())

        assert any("seed page returned no usable text" in err for err in result.errors)

    def test_duplicate_claims_are_deduped(self, monkeypatch):
        seed_text = "Acme builds developer tools."
        monkeypatch.setattr(research_module, "fetch_page", lambda *a, **k: seed_text)
        same_fact = {
            "claim": "Acme builds developer tools",
            "evidence": "Acme builds developer tools",
            "category": "overview",
            "confidence": 0.9,
        }
        dupe_result = SearchResult(
            title="Acme", url="https://dir.example.com/acme", content=seed_text
        )
        llm = FakeLLM(
            plans=[
                {"done": False, "reason": "go", "next_query": "acme overview"},
                {"done": True, "reason": "stop", "next_query": None},
            ],
            extracts={
                "https://acme.com": [same_fact],
                "https://dir.example.com/acme": [same_fact],  # same claim, new source
            },
        )
        search = FakeSearch(results_by_query={"acme overview": [dupe_result]})

        result = run_research(Company(domain="acme.com"), llm, search, _settings())

        # The claim appears once despite being extracted from two sources.
        assert len(result.facts) == 1


class TestResearchNodeAdapter:
    def test_returns_research_field_for_state(self, monkeypatch):
        from pitch_pilot.graph.state import PipelineState
        from pitch_pilot.models.icp import ICP

        seed_text = "Acme builds developer tools."
        monkeypatch.setattr(research_module, "fetch_page", lambda *a, **k: seed_text)

        llm = FakeLLM(
            plans=[{"done": True, "reason": "seed is enough", "next_query": None}],
            extracts={
                "https://acme.com": [
                    {
                        "claim": "Acme builds developer tools",
                        "evidence": "Acme builds developer tools",
                        "category": "overview",
                        "confidence": 0.9,
                    }
                ]
            },
        )
        search = FakeSearch(default=[])

        monkeypatch.setattr(research_module, "get_settings", _settings)
        monkeypatch.setattr(research_module, "get_llm_client", lambda settings: llm)
        monkeypatch.setattr(research_module, "get_search_client", lambda settings: search)

        icp = ICP(
            industries=["devtools"],
            min_employees=10,
            max_employees=500,
            regions=["US"],
            positive_signals=["hiring"],
            negative_signals=["non-profit"],
        )
        state = PipelineState(company=Company(domain="acme.com"), icp=icp)

        update = research_module.research_node(state)

        assert set(update) == {"research"}
        assert isinstance(update["research"], ResearchResult)
        assert update["research"].company.domain == "acme.com"
        assert len(update["research"].facts) == 1
