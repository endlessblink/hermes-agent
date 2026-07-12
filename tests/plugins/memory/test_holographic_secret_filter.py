"""Memory must never store secrets — a leaked Supabase-secret fact showed why."""

import json

from agent.memory_capture import looks_like_secret, mechanical_facts
from plugins.memory.holographic import HolographicMemoryProvider


class TestSecretDetection:
    def test_flags_common_secret_shapes(self):
        assert looks_like_secret("api_key: sk-abcdef1234567890")
        assert looks_like_secret("export SUPABASE_SERVICE_KEY=eyJhbGciOiJ.abcdefghij")
        assert looks_like_secret("password = hunter2horse")
        assert looks_like_secret("token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")

    def test_normal_direction_notes_are_not_secrets(self):
        assert not looks_like_secret("don't change the lighting mid-shot")
        assert not looks_like_secret("we use GPT Image 2 and Magnific, not Higgsfield")


def test_mechanical_capture_skips_secret_statement():
    msgs = [{"role": "user", "content": "I always set SUPABASE_SERVICE_KEY=eyJabc.defghijklmno in the env"}]
    facts = mechanical_facts(msgs)
    assert not any("SUPABASE_SERVICE_KEY" in f.content for f in facts)


def test_fact_store_add_refuses_secret(tmp_path):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "mem.db")})
    p.initialize(session_id="s")
    try:
        res = json.loads(p._handle_fact_store(
            {"action": "add", "content": "the kong admin api_key: sk-livesecret1234567890"}))
        assert res["status"] == "skipped"
        assert p._store._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    finally:
        p.shutdown()
