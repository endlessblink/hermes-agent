"""Recency-boosted recall (Phase 4): 'what were we working on' surfaces recent
high-trust facts even when the query shares no keywords with them."""

import time

import pytest

from plugins.memory.holographic import HolographicMemoryProvider
from plugins.memory.holographic.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(db_path=str(tmp_path / "mem.db"))
    yield s
    s.close()


class TestRecentFacts:
    def test_returns_newest_first(self, store):
        store.add_fact("older fact about widgets", origin="mechanical")
        time.sleep(0.01)
        store.add_fact("newer fact about the memory system", origin="mechanical")
        recent = store.recent_facts(limit=5)
        assert recent[0]["content"] == "newer fact about the memory system"

    def test_excludes_superseded(self, store):
        a = store.add_fact("we use webpack", origin="mechanical")
        b = store.add_fact("we use vite now", origin="mechanical")
        store.supersede_fact(a, superseded_by=b)
        contents = [f["content"] for f in store.recent_facts(limit=5)]
        assert "we use webpack" not in contents
        assert "we use vite now" in contents

    def test_respects_min_trust(self, store):
        store.add_fact("low trust guess", origin="inferred")  # default trust 0.5
        # min_trust above 0.5 filters it out
        assert store.recent_facts(limit=5, min_trust=0.9) == []


class TestPrefetchMergesRecency:
    @pytest.fixture
    def provider(self, tmp_path):
        p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "m.db"), "hrr_dim": 64})
        p.initialize(session_id="s")
        yield p
        p.shutdown()

    def test_vague_query_surfaces_recent_subject(self, provider):
        # A subject fact that shares NO words with the question.
        provider._store.add_fact("We are building the reliable memory system.",
                                 category="subject", origin="inferred")
        # Keyword search for this question would miss it ("working on" != "building").
        out = provider.prefetch("what were we working on")
        assert "memory system" in out  # recency path surfaces it anyway

    def test_keyword_hit_still_works(self, provider):
        provider._store.add_fact("The deploy target is production.", origin="mechanical")
        out = provider.prefetch("deploy target")
        assert "production" in out
