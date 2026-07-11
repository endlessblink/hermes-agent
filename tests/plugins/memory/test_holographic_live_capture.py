"""Mid-conversation (per-turn) capture: corrections/decisions saved as they happen.

sync_turn runs deterministic capture each turn so a long session accumulates
memory without waiting for session end.
"""

import pytest

from agent.memory_capture import mechanical_facts
from plugins.memory.holographic import HolographicMemoryProvider


class TestCorrectionPatterns:
    def _cats(self, text):
        return {f.category for f in mechanical_facts([{"role": "user", "content": text}])}

    def test_captures_english_corrections(self):
        assert "correction" in self._cats("don't change the lighting mid-shot")
        assert "correction" in self._cats("he should not smile to himself")
        assert "correction" in self._cats("these errors should not repeat")
        assert "correction" in self._cats("avoid showing him eating the pasta")

    def test_captures_hebrew_corrections(self):
        assert "correction" in self._cats("אל תשנה את התאורה באמצע השוט")
        assert "correction" in self._cats("שוב נתקעת על אותה בעיה")

    def test_plain_statement_not_a_correction(self):
        assert "correction" not in self._cats("the dinner scene looks good so far")


class TestLiveSyncTurnCapture:
    def test_sync_turn_captures_correction_scoped_to_project(self, tmp_path):
        p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "mem.db")})
        p.initialize(session_id="s")
        try:
            p.set_active_project("too-much")
            p.sync_turn(
                "don't change the lighting between frames and he should not smile to himself",
                "Understood, I'll keep the lighting consistent.",
            )
            facts = p._store.list_facts(min_trust=0.0, limit=10)
            assert any(f["category"] == "correction" for f in facts), "correction not captured live"
            corr = next(f for f in facts if f["category"] == "correction")
            assert p._store.fact_projects(corr["fact_id"]) == ["too-much"]
            assert corr["trust_score"] == pytest.approx(0.8)  # authoritative
            # And it's recalled afterwards.
            hits = p._retriever.search("lighting smile", min_trust=0.0, limit=5, project_id="too-much")
            assert any("lighting" in h["content"] for h in hits)
        finally:
            p.shutdown()

    def test_sync_turn_ignores_empty_and_plain_chatter(self, tmp_path):
        p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "mem.db")})
        p.initialize(session_id="s")
        try:
            p.sync_turn("", "")
            p.sync_turn("looks nice, thanks", "glad you like it")
            assert p._store._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
        finally:
            p.shutdown()
