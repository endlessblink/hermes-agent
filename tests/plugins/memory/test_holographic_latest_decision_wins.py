"""Latest-decision-wins: trust override on add + model-driven supersede.

Covers the "latest chat decision wins" work:
- ``MemoryStore.add_fact`` accepts an optional ``trust`` override (clamped),
  and omitting it preserves the store's default trust.
- Authoritative decisions captured at higher trust outrank a generic peer.
- The ``fact_store`` tool's new ``supersede`` action retires a stale fact so a
  newer contradicting decision is the only one recalled.
"""

import json

import pytest

from plugins.memory.holographic.retrieval import FactRetriever
from plugins.memory.holographic.store import MemoryStore
from plugins.memory.holographic import HolographicMemoryProvider


@pytest.fixture
def retriever(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "mem.db"))
    yield FactRetriever(store=store, hrr_dim=store.hrr_dim), store
    store.close()


def test_add_fact_trust_override_and_default(retriever):
    _, store = retriever
    default_id = store.add_fact("upscaler is unspecified for now")
    boosted_id = store.add_fact("we use Magnific for upscaling", trust=0.8)

    rows = {
        r["fact_id"]: r["trust_score"]
        for r in store.list_facts(min_trust=0.0, limit=10)
    }
    assert rows[default_id] == pytest.approx(store.default_trust)
    assert rows[boosted_id] == pytest.approx(0.8)


def test_add_fact_trust_is_clamped(retriever):
    _, store = retriever
    hi = store.add_fact("clamp high", trust=5.0)
    lo = store.add_fact("clamp low", trust=-2.0)
    rows = {r["fact_id"]: r["trust_score"] for r in store.list_facts(min_trust=0.0, limit=10)}
    assert rows[hi] == pytest.approx(1.0)
    assert rows[lo] == pytest.approx(0.0)


def test_higher_trust_decision_outranks_generic_peer(retriever):
    r, store = retriever
    store.add_fact("upscaling with higgsfield maybe someday", trust=0.5)
    store.add_fact("upscaling decision: use Magnific not Higgsfield", trust=0.8)
    hits = r.search("upscaling magnific higgsfield", min_trust=0.0, limit=2)
    assert hits, "expected recall hits"
    assert "Magnific" in hits[0]["content"]


def test_supersede_action_retires_stale_fact(tmp_path):
    # initialize() resolves db_path from config, so point it at a temp db.
    provider = HolographicMemoryProvider(config={"db_path": str(tmp_path / "mem.db")})
    provider.initialize(session_id="t")
    try:
        old = json.loads(provider._handle_fact_store(
            {"action": "add", "content": "we use Higgsfield for upscaling"}))["fact_id"]
        new = json.loads(provider._handle_fact_store(
            {"action": "add", "content": "we use Magnific for upscaling, not Higgsfield"}))["fact_id"]

        res = json.loads(provider._handle_fact_store(
            {"action": "supersede", "fact_id": old, "superseded_by": new}))
        assert res["status"] == "retired"
        assert res["superseded"] == old and res["by"] == new

        # Recall should now surface only the new fact.
        hits = provider._retriever.search("upscaling", min_trust=0.0, limit=10)
        ids = {h["fact_id"] for h in hits}
        assert old not in ids
        assert new in ids
    finally:
        provider.shutdown()
