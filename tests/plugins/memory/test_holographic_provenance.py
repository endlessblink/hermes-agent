"""Provenance and supersede for the holographic fact store (memory Slice 1).

Every fact must carry where it came from (which session/message, and whether it
was captured mechanically, inferred by the model, or mirrored from Obsidian), so
any recalled claim is checkable. A newer fact that contradicts an older one
marks the old one superseded rather than deleting it -- the staleness fix.
"""

import pytest

from plugins.memory.holographic.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(db_path=str(tmp_path / "mem.db"))
    yield s
    s.close()


def _cols(store):
    return {row[1] for row in store._conn.execute("PRAGMA table_info(facts)").fetchall()}


class TestProvenanceColumns:
    def test_new_columns_exist(self, store):
        cols = _cols(store)
        assert {"source_session", "source_message_id", "origin", "superseded_by"} <= cols

    def test_add_fact_records_provenance(self, store):
        fid = store.add_fact(
            "the FlowState work lives in hermes-agent",
            origin="mechanical",
            source_session="20260710_x",
            source_message_id=6119,
        )
        row = store._conn.execute(
            "SELECT origin, source_session, source_message_id FROM facts WHERE fact_id = ?",
            (fid,),
        ).fetchone()
        assert row["origin"] == "mechanical"
        assert row["source_session"] == "20260710_x"
        assert row["source_message_id"] == 6119

    def test_origin_defaults_to_inferred(self, store):
        """A bare add (legacy callers) is the least-trusted origin, not mechanical."""
        fid = store.add_fact("some remembered thing")
        row = store._conn.execute("SELECT origin FROM facts WHERE fact_id = ?", (fid,)).fetchone()
        assert row["origin"] == "inferred"

    def test_provenance_survives_a_reopen(self, tmp_path):
        path = str(tmp_path / "persist.db")
        s1 = MemoryStore(db_path=path)
        fid = s1.add_fact("durable fact", origin="obsidian", source_session="s1")
        s1.close()
        s2 = MemoryStore(db_path=path)
        row = s2._conn.execute("SELECT origin FROM facts WHERE fact_id = ?", (fid,)).fetchone()
        assert row["origin"] == "obsidian"
        s2.close()


class TestSupersede:
    def test_supersede_marks_old_not_deletes(self, store):
        old = store.add_fact("deploy target is staging", origin="mechanical")
        new = store.add_fact("deploy target is production", origin="mechanical")
        store.supersede_fact(old, superseded_by=new)

        row = store._conn.execute(
            "SELECT superseded_by FROM facts WHERE fact_id = ?", (old,)
        ).fetchone()
        assert row["superseded_by"] == new
        # The old fact is retained for history, not gone.
        assert store._conn.execute(
            "SELECT COUNT(*) FROM facts WHERE fact_id = ?", (old,)
        ).fetchone()[0] == 1

    def test_search_excludes_superseded_by_default(self, store):
        old = store.add_fact("uses webpack for bundling", origin="mechanical")
        new = store.add_fact("uses vite for bundling now", origin="mechanical")
        store.supersede_fact(old, superseded_by=new)

        hits = store.search_facts("bundling")
        ids = {h["fact_id"] for h in hits}
        assert old not in ids
        assert new in ids

    def test_search_can_include_superseded_for_history(self, store):
        old = store.add_fact("old approach kept", origin="mechanical")
        new = store.add_fact("new approach replaced it", origin="mechanical")
        store.supersede_fact(old, superseded_by=new)

        hits = store.search_facts("approach", include_superseded=True)
        assert old in {h["fact_id"] for h in hits}

    def test_superseding_a_missing_fact_is_a_noop(self, store):
        new = store.add_fact("only real fact", origin="mechanical")
        # Should not raise.
        store.supersede_fact(999999, superseded_by=new)
