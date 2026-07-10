"""Recall ranks by provenance and hides superseded facts (memory Slice 1c)."""

import pytest

from plugins.memory.holographic.retrieval import FactRetriever
from plugins.memory.holographic.store import MemoryStore


@pytest.fixture
def retriever(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "mem.db"))
    yield FactRetriever(store=store, hrr_dim=store.hrr_dim), store
    store.close()


def test_mechanical_outranks_inferred_on_equal_text(retriever):
    r, store = retriever
    # Same words, different provenance.
    store.add_fact("the deploy target is production always", origin="inferred")
    store.add_fact("the deploy target is production reliably", origin="mechanical")

    hits = r.search("deploy target production", min_trust=0.0, limit=2)
    assert hits, "expected recall hits"
    assert hits[0]["origin"] == "mechanical"


def test_obsidian_outranks_mechanical(retriever):
    r, store = retriever
    store.add_fact("project uses vite for bundling here", origin="mechanical")
    store.add_fact("project uses vite for bundling now", origin="obsidian")
    hits = r.search("project uses vite bundling", min_trust=0.0, limit=2)
    assert hits[0]["origin"] == "obsidian"


def test_superseded_fact_is_not_recalled(retriever):
    r, store = retriever
    old = store.add_fact("bundler is webpack currently", origin="mechanical")
    new = store.add_fact("bundler is vite currently", origin="mechanical")
    store.supersede_fact(old, superseded_by=new)

    hits = r.search("bundler", min_trust=0.0, limit=10)
    ids = {h["fact_id"] for h in hits}
    assert old not in ids
    assert new in ids


def test_inferred_still_recalls_when_alone(retriever):
    r, store = retriever
    store.add_fact("user was exploring a graph database option", origin="inferred")
    hits = r.search("graph database", min_trust=0.0, limit=5)
    assert any("graph database" in h["content"] for h in hits)
