"""End-to-end tests for the deterministic pipeline graph. No network access.

A single fake LLM serves every phase (it routes by the prompt it receives), the
search client returns nothing (research stays seed-only), and the store is a fake
that records where each lead landed. The behaviors under test are the graph's
control flow and terminal routing:

* a disqualified company skips drafting and is logged as ``disqualified``;
* a qualified company whose draft passes verification is logged ``ready``;
* a qualified company whose draft fails verification is enqueued for ``review``;
* `build_pipeline` compiles and runs the whole graph on mocked dependencies.
"""

from __future__ import annotations

import pitch_pilot.nodes.research as research_module
from pitch_pilot.config import Settings
from pitch_pilot.graph.pipeline import build_pipeline
from pitch_pilot.graph.state import PipelineState
from pitch_pilot.models.icp import ICP
from pitch_pilot.models.lead import Company
from pitch_pilot.models.search import SearchResult

SEED_TEXT = "Acme builds developer tools. Acme is a fintech company. Acme is hiring engineers."

SEED_FACTS = [
    {"claim": "Acme builds developer tools", "evidence": "Acme builds developer tools",
     "category": "overview", "confidence": 0.9},
    {"claim": "Acme is hiring engineers", "evidence": "hiring engineers",
     "category": "hiring", "confidence": 0.8},
]

QUALIFIED_ASSESSMENT = {
    "industry": {"status": "match"},
    "region": {"status": "match"},
    "employee_count": {"value": 120},
    "positive_signals": [{"signal": "hiring engineers", "status": "match"}],
    "negative_signals": [],
}

DISQUALIFIED_ASSESSMENT = {
    "industry": {"status": "no_match"},
    "region": {"status": "no_match"},
    "employee_count": {"value": 5},  # outside the ICP band
    "positive_signals": [{"signal": "hiring engineers", "status": "no_match"}],
    "negative_signals": [],
}


def _settings(**overrides) -> Settings:
    values = {
        "gemini_api_key": "g", "tavily_api_key": "t",
        "research_max_queries": 2, "qualify_threshold": 0.5, "groundedness_threshold": 0.9,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _icp() -> ICP:
    return ICP(
        industries=["devtools", "fintech"], min_employees=50, max_employees=2000,
        regions=["US"], positive_signals=["hiring engineers"], negative_signals=["direct competitor"],
    )


class PipelineLLM:
    """One fake LLM for the whole graph; routes by the prompt's shape."""

    def __init__(self, *, assessment, draft_payload):
        self.assessment = assessment
        self.draft_payload = draft_payload
        self.draft_called = False

    def complete(self, system, user):  # pragma: no cover - unused
        return "OK"

    def complete_json(self, system, user):
        if user.startswith("SOURCE URL:"):
            url = user.split("SOURCE URL:", 1)[1].splitlines()[0].strip()
            return {"facts": SEED_FACTS if url == "https://acme.com" else []}
        if user.startswith("ICP:"):
            return self.assessment
        if user.startswith("CLAIM:"):
            return {"verdict": "faithful", "reason": "evidence supports the claim"}
        if "Write the outreach email" in user:
            self.draft_called = True
            return self.draft_payload
        # Otherwise it's the research planner: stop immediately (seed-only).
        return {"done": True, "reason": "seed is enough", "next_query": None}


class FakeSearch:
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        return []


class FakeStore:
    def __init__(self):
        self.saved = []
        self.review = []

    def save_lead(self, lead) -> None:
        self.saved.append(lead)

    def enqueue_for_review(self, lead) -> None:
        self.review.append(lead)


def _run(llm, store, monkeypatch):
    monkeypatch.setattr(research_module, "fetch_page", lambda *a, **k: SEED_TEXT)
    app = build_pipeline(llm=llm, search=FakeSearch(), store=store, settings=_settings())
    init = PipelineState(company=Company(domain="acme.com", name="Acme"), icp=_icp())
    return PipelineState.model_validate(app.invoke(init))


class TestPipelineRouting:
    def test_disqualified_lead_skips_draft_and_is_saved(self, monkeypatch):
        llm = PipelineLLM(assessment=DISQUALIFIED_ASSESSMENT, draft_payload={})
        store = FakeStore()
        final = _run(llm, store, monkeypatch)

        assert final.qualification.qualified is False
        assert final.draft is None              # drafting was skipped
        assert llm.draft_called is False         # ...and the draft LLM was never called
        assert final.verification is None
        assert final.status == "disqualified"
        assert len(store.saved) == 1 and store.saved[0].status == "disqualified"
        assert store.review == []

    def test_qualified_passing_draft_is_logged_ready(self, monkeypatch):
        draft_payload = {"subject": "Loved your tools", "body": "Hi Acme...",
                         "hooks": ["Acme builds developer tools"]}
        llm = PipelineLLM(assessment=QUALIFIED_ASSESSMENT, draft_payload=draft_payload)
        store = FakeStore()
        final = _run(llm, store, monkeypatch)

        assert final.qualification.qualified is True
        assert llm.draft_called is True
        assert final.draft.hooks_used == ["Acme builds developer tools"]
        assert final.verification.passed is True
        assert final.status == "ready"
        assert len(store.saved) == 1 and store.saved[0].status == "ready"
        assert store.review == []

    def test_qualified_failing_verify_routes_to_review(self, monkeypatch):
        # The draft node drops the fabricated hook, leaving nothing grounded to
        # stand on; verify then fails (no claims) and the lead routes to review.
        draft_payload = {"subject": "s", "body": "b", "hooks": ["Acme is a unicorn"]}
        llm = PipelineLLM(assessment=QUALIFIED_ASSESSMENT, draft_payload=draft_payload)
        store = FakeStore()
        final = _run(llm, store, monkeypatch)

        assert final.qualification.qualified is True
        assert final.draft.hooks_used == []          # the fabricated hook was dropped
        assert final.verification.passed is False
        assert final.status == "review"
        assert len(store.review) == 1 and store.review[0].status == "review"
        assert store.saved == []


class TestBuildPipelineCompiles:
    def test_compiles_and_runs_end_to_end(self, monkeypatch):
        llm = PipelineLLM(
            assessment=QUALIFIED_ASSESSMENT,
            draft_payload={"subject": "s", "body": "b", "hooks": ["Acme builds developer tools"]},
        )
        final = _run(llm, FakeStore(), monkeypatch)
        # Every stage produced its artifact.
        assert final.research is not None and final.research.facts
        assert final.qualification is not None
        assert final.draft is not None
        assert final.verification is not None
        assert final.status in {"ready", "review"}
