"""Session-local working state persistence tests."""

from __future__ import annotations

import time

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    database = SessionDB(db_path=home / "state.db")
    try:
        yield database
    finally:
        database.close()


def _make_session(database: SessionDB, session_id: str = "sess-working") -> str:
    def _do(conn):
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, source, started_at) "
            "VALUES (?, ?, ?)",
            (session_id, "cli", time.time()),
        )

    database._execute_write(_do)
    return session_id


def test_working_state_round_trips_and_renders_bounded_context(db):
    sid = _make_session(db)

    state = db.set_working_state(
        sid,
        {
            "active_task": "finish FlowState Hermes next-block handoff",
            "phase": "verification",
            "constraints": ["do not expose tokens", "preview before mutation"],
            "relevant_files": ["tools/flowstate_tool.py"],
            "ignore_me": None,
        },
        source="test",
    )

    assert state["active_task"] == "finish FlowState Hermes next-block handoff"
    assert "ignore_me" not in state
    assert db.get_working_state(sid)["constraints"] == [
        "do not expose tokens",
        "preview before mutation",
    ]

    rendered = db.render_working_state_context(sid)

    assert rendered.startswith("<working-state>")
    assert "Active task: finish FlowState Hermes next-block handoff" in rendered
    assert "Constraints: do not expose tokens; preview before mutation" in rendered
    assert "tools/flowstate_tool.py" in rendered
    assert rendered.endswith("</working-state>")


def test_working_state_patch_merges_nested_values_and_removes_nulls(db):
    sid = _make_session(db)
    db.set_working_state(
        sid,
        {
            "active_task": "old",
            "details": {"phase": "plan", "owner": "Hermes"},
            "blockers": ["one"],
        },
    )

    merged = db.patch_working_state(
        sid,
        {
            "active_task": "new",
            "details": {"phase": "build"},
            "blockers": None,
        },
    )

    assert merged == {
        "active_task": "new",
        "details": {"owner": "Hermes", "phase": "build"},
    }


def test_working_state_corrupt_row_degrades_to_empty_context(db):
    sid = _make_session(db)

    def _do(conn):
        conn.execute(
            "INSERT INTO session_working_state "
            "(session_id, state_json, revision, updated_at, source) "
            "VALUES (?, ?, 1, ?, ?)",
            (sid, "{not-json", time.time(), "test"),
        )

    db._execute_write(_do)

    assert db.get_working_state(sid) == {}
    assert db.render_working_state_context(sid) == ""


def test_clear_working_state_keeps_supersession_marker(db):
    sid = _make_session(db)
    db.set_working_state(sid, {"active_task": "stale task", "status": "active"})

    cleared = db.clear_working_state(sid, reason="user changed direction")

    assert cleared == {
        "active_task": "",
        "status": "superseded",
        "superseded_reason": "user changed direction",
    }
    rendered = db.render_working_state_context(sid)
    assert "Status: superseded" in rendered
    assert "Superseded reason: user changed direction" in rendered
    assert "Active task:" not in rendered


def test_working_state_context_truncates_large_payload(db):
    sid = _make_session(db)
    db.set_working_state(
        sid,
        {
            "active_task": "x" * 5000,
            "phase": "verification",
        },
    )

    rendered = db.render_working_state_context(sid, max_chars=600)

    assert len(rendered) <= 600
    assert rendered.endswith("...[working state truncated]")
