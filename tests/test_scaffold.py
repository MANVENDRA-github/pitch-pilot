"""Unit tests for the graph state, the pipeline stub, and storage. No network."""

from __future__ import annotations

import json

import pytest

from pitch_pilot.graph.pipeline import build_pipeline
from pitch_pilot.graph.state import PipelineState
from pitch_pilot.models import ICP, Company, Lead
from pitch_pilot.storage import JsonStore, Store


def _icp() -> ICP:
    return ICP(
        industries=["fintech"],
        min_employees=10,
        max_employees=200,
        regions=["US"],
        positive_signals=["hiring"],
        negative_signals=["non-profit"],
    )


class TestPipelineState:
    def test_constructs_with_seed_inputs_only(self):
        state = PipelineState(company=Company(domain="acme.com"), icp=_icp())
        assert state.status == "pending"
        assert state.research is None
        assert state.qualification is None
        assert state.draft is None
        assert state.verification is None
        assert state.errors == []

    def test_errors_default_is_independent_per_instance(self):
        a = PipelineState(company=Company(domain="a.com"), icp=_icp())
        b = PipelineState(company=Company(domain="b.com"), icp=_icp())
        a.errors.append("boom")
        assert b.errors == []  # mutable default is not shared across instances


class TestPipelineStub:
    def test_build_pipeline_is_not_implemented_yet(self):
        with pytest.raises(NotImplementedError):
            build_pipeline()


class TestJsonStore:
    def test_satisfies_store_protocol(self):
        assert isinstance(JsonStore(), Store)  # runtime_checkable Protocol

    def test_save_and_enqueue_write_jsonl(self, tmp_path):
        path = tmp_path / "store.jsonl"
        store = JsonStore(path=path)
        lead = Lead(company=Company(domain="acme.com", name="Acme"))

        store.save_lead(lead)
        store.enqueue_for_review(lead)

        assert path.exists()
        assert store.review_path.exists()
        saved = json.loads(path.read_text(encoding="utf-8").strip())
        assert saved["company"]["domain"] == "acme.com"
        queued = json.loads(store.review_path.read_text(encoding="utf-8").strip())
        assert queued["company"]["name"] == "Acme"

    def test_save_lead_appends(self, tmp_path):
        path = tmp_path / "store.jsonl"
        store = JsonStore(path=path)
        store.save_lead(Lead(company=Company(domain="a.com")))
        store.save_lead(Lead(company=Company(domain="b.com")))
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
