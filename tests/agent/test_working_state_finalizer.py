from types import SimpleNamespace

from agent.turn_finalizer import _patch_completed_turn_working_state
from hermes_state import SessionDB


def _agent(db):
    return SimpleNamespace(_session_db=db, session_id="s1")


def test_completed_turn_patches_working_state(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s1", source="cli")

    _patch_completed_turn_working_state(
        _agent(db),
        original_user_message="fix the auth state in ./src/auth.ts",
        final_response="Updated auth state handling and tests passed.",
        messages=[
            {"role": "user", "content": "fix ./src/auth.ts"},
            {"role": "assistant", "content": "done"},
        ],
        interrupted=False,
        failed=False,
    )

    state = db.get_working_state("s1")
    assert state["active_task"] == "fix the auth state in ./src/auth.ts"
    assert state["status"] == "answered"
    assert state["completed_actions"] == ["Updated auth state handling and tests passed."]
    assert state["relevant_files"] == ["./src/auth.ts"]


def test_interrupted_turn_does_not_mark_completed(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s1", source="cli")
    db.set_working_state(
        "s1",
        {"pending_turn": {"prompt_hash": "abc", "started_at": 1, "state": "running"}},
        source="test",
    )

    _patch_completed_turn_working_state(
        _agent(db),
        original_user_message="fix thing",
        final_response="partial",
        messages=[],
        interrupted=True,
        failed=False,
    )

    assert "pending_turn" not in db.get_working_state("s1")


def test_failed_turn_clears_pending_recovery_marker(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s1", source="cli")
    db.set_working_state(
        "s1",
        {"pending_turn": {"prompt_hash": "abc", "started_at": 1, "state": "running"}},
        source="test",
    )

    _patch_completed_turn_working_state(
        _agent(db),
        original_user_message="fix thing",
        final_response="",
        messages=[],
        interrupted=False,
        failed=True,
    )

    assert "pending_turn" not in db.get_working_state("s1")


def test_supersession_clears_active_task(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s1", source="cli")
    db.set_working_state("s1", {"active_task": "old task"}, source="test")

    _patch_completed_turn_working_state(
        _agent(db),
        original_user_message="stop that and just verify",
        final_response="ok",
        messages=[],
        interrupted=False,
        failed=False,
    )

    state = db.get_working_state("s1")
    assert state["active_task"] == ""
    assert state["status"] == "superseded"
    assert state["superseded_reason"] == "stop that and just verify"
