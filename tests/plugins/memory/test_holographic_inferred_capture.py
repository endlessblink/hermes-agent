"""Inferred capture wiring: session end → model → facts stored at inferred tier."""

import pytest

from plugins.memory.holographic import HolographicMemoryProvider


@pytest.fixture
def provider(tmp_path):
    p = HolographicMemoryProvider(
        config={"db_path": str(tmp_path / "mem.db"), "hrr_dim": 64, "infer_facts": True}
    )
    p.initialize(session_id="sess-inf")
    yield p
    p.shutdown()


def _fake_extract(messages):
    from agent.memory_extraction import InferredFact
    return [
        InferredFact("We are building the memory system for Hermes.", "subject"),
        InferredFact("Remember to always run tests before committing.", "lesson"),
        InferredFact("Add a provenance-ranked recall to the fact store.", "change"),
    ]


def test_inferred_facts_stored_from_session(provider, monkeypatch):
    monkeypatch.setattr(
        "agent.memory_extraction.extract_inferred_facts", _fake_extract
    )
    provider.on_session_end([{"role": "user", "content": "let's keep building the memory system"}])

    rows = provider._store._conn.execute(
        "SELECT origin, category, content FROM facts WHERE origin = 'inferred'"
    ).fetchall()
    cats = {r["category"] for r in rows}
    assert {"subject", "lesson", "change"} <= cats
    assert all(r["origin"] == "inferred" for r in rows)
    assert all(r["source_session"] == "sess-inf" for r in provider._store._conn.execute(
        "SELECT source_session FROM facts WHERE origin='inferred'"
    ).fetchall())


def test_inferred_recalls_what_we_are_working_on(provider, monkeypatch):
    monkeypatch.setattr(
        "agent.memory_extraction.extract_inferred_facts", _fake_extract
    )
    provider.on_session_end([{"role": "user", "content": "continue the memory work"}])

    # Recall is keyword-based (FTS); querying by terms that appear in the fact
    # surfaces it. (Bridging "working on" -> "building" is the semantic gap a
    # reranker/embeddings would close -- a deferred slice, not this wiring.)
    hits = provider.prefetch("memory system tests")
    assert "memory system" in hits
    assert "run tests" in hits


def test_inferred_skipped_when_disabled(tmp_path, monkeypatch):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "m.db"), "hrr_dim": 64})
    p.initialize(session_id="s")
    called = {"n": 0}

    def _spy(messages):
        called["n"] += 1
        return []

    monkeypatch.setattr("agent.memory_extraction.extract_inferred_facts", _spy)
    p.on_session_end([{"role": "user", "content": "hello"}])
    assert called["n"] == 0  # infer_facts defaults off
    p.shutdown()
