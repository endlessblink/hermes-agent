"""End-to-end: a session's mechanical facts land in the store with provenance."""

import pytest

from plugins.memory.holographic import HolographicMemoryProvider


REPO = "/home/endlessblink/.hermes/hermes-agent"


@pytest.fixture
def provider(tmp_path):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "mem.db"), "hrr_dim": 64})
    p.initialize(session_id="sess-1")
    yield p
    p.shutdown()


def _session():
    return [
        {"role": "user", "content": "I prefer short answers", "id": 5},
        {
            "role": "assistant",
            "content": f"```bash\ncodex exec --cd {REPO} --sandbox workspace-write -\n```",
        },
    ]


def test_session_end_captures_mechanical_facts(provider):
    provider.on_session_end(_session())
    hits = provider._store.search_facts("repo OR codex OR answers", min_trust=0.0, limit=20)
    blob = " | ".join(h["content"] for h in hits)
    assert REPO in blob          # workspace fact
    assert "short answers" in blob  # explicit user preference


def test_captured_facts_are_tagged_mechanical_with_session(provider):
    provider.on_session_end(_session())
    rows = provider._store._conn.execute(
        "SELECT origin, source_session FROM facts"
    ).fetchall()
    assert rows
    assert all(r["origin"] == "mechanical" for r in rows)
    assert all(r["source_session"] == "sess-1" for r in rows)


def test_mechanical_capture_runs_without_auto_extract(provider):
    """Deterministic capture is not gated behind the model-extraction flag."""
    assert provider._config.get("auto_extract", False) is False
    provider.on_session_end(_session())
    count = provider._store._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    assert count > 0


def test_preference_provenance_links_to_message(provider):
    provider.on_session_end(_session())
    row = provider._store._conn.execute(
        "SELECT source_message_id FROM facts WHERE category = 'user_pref'"
    ).fetchone()
    assert row is not None and row["source_message_id"] == 5
