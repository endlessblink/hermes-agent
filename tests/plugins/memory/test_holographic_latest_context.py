"""Latest decisions are always surfaced — never crowded out, survive compression.

The tool-choice decision must reach the model even when the turn's query has no
keyword overlap and many other facts match.
"""

import pytest

from plugins.memory.holographic import HolographicMemoryProvider
from plugins.memory.holographic.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(db_path=str(tmp_path / "mem.db"))
    yield s
    s.close()


def test_recent_facts_category_filter(store):
    store.add_fact("we use GPT Image 2, not Higgsfield", category="correction", trust=0.8)
    store.add_fact("the pasta looks good", category="subject")
    rows = store.recent_facts(min_trust=0.0, limit=10, categories=["correction", "decision"])
    cats = {r["category"] for r in rows}
    assert cats == {"correction"}
    assert any("GPT Image 2" in r["content"] for r in rows)


def test_latest_decision_not_crowded_out(tmp_path):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "mem.db")})
    p.initialize(session_id="s")
    try:
        # A pile of keyword-matching NON-decision facts.
        for i in range(8):
            p._store.add_fact(f"dinner scene note number {i} about lighting", category="subject")
        # One authoritative decision with NO keyword overlap with the query.
        p._store.add_fact(
            "we generate stills with GPT Image 2 and upscale with Magnific, not Higgsfield",
            category="correction", trust=0.8,
        )
        block = p.prefetch("dinner scene lighting note")  # matches the 8 subjects, not the decision
        assert "latest decisions" in block.lower()
        assert "GPT Image 2" in block and "Higgsfield" in block  # decision survived

    finally:
        p.shutdown()


def test_decision_surfaces_on_unrelated_query(tmp_path):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "mem.db")})
    p.initialize(session_id="s")
    try:
        p._store.add_fact(
            "we use GPT Image 2 + Magnific, never Higgsfield",
            category="correction", trust=0.8,
        )
        # Query shares no words with the decision (post-compression 'continue').
        block = p.prefetch("let's continue")
        assert "GPT Image 2" in block
    finally:
        p.shutdown()


def test_newer_decision_supersedes_in_block(tmp_path):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "mem.db")})
    p.initialize(session_id="s")
    try:
        old = p._store.add_fact("we use Higgsfield for generation", category="decision", trust=0.8)
        new = p._store.add_fact("we use GPT Image 2 for generation", category="decision", trust=0.8)
        p._store.supersede_fact(old, superseded_by=new)
        block = p.prefetch("what now")
        assert "GPT Image 2" in block
        assert "Higgsfield" not in block  # retired decision no longer surfaces
    finally:
        p.shutdown()
