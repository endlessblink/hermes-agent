"""A fresh session should recall the lane the last session was working in.

Observed 2026-07-10: Hermes opened a new chat, had no idea the previous
session had been building the FlowState planning surface, and asked the user
to choose between four guesses. The lane block added in ``a36923674`` only
renders during compression recovery; an ordinary new session gets nothing.

Nothing was missing from disk. The previous session's lane was sitting in
``session_working_state``; no code read it across a session boundary.
"""

import time

import pytest

from hermes_state import SessionDB


REPO = "/home/endlessblink/.hermes/hermes-agent"


@pytest.fixture
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


def _session(db, sid, *, started_at=None, source="desktop"):
    db.create_session(sid, source=source)
    if started_at is not None:
        db._execute_write(  # noqa: SLF001 - test fixture
            lambda conn: conn.execute(
                "UPDATE sessions SET started_at = ? WHERE id = ?", (started_at, sid)
            )
        )
    return sid


def _lane(repo=REPO, **kw):
    lane = {"repo_path": repo, "branch": "main", "source": "transcript"}
    lane.update(kw)
    return lane


class TestGetRecentLane:
    def test_returns_lane_from_the_most_recent_other_session(self, db):
        _session(db, "old", started_at=time.time() - 3600)
        _session(db, "new")
        db.patch_working_state("old", {"lane": _lane()}, source="test")

        lane = db.get_recent_lane(exclude_session_id="new")
        assert lane is not None
        assert lane["repo_path"] == REPO

    def test_never_returns_the_current_session_own_lane(self, db):
        _session(db, "cur")
        db.patch_working_state("cur", {"lane": _lane()}, source="test")
        assert db.get_recent_lane(exclude_session_id="cur") is None

    def test_prefers_the_newest_lane(self, db):
        _session(db, "older", started_at=time.time() - 7200)
        _session(db, "newer", started_at=time.time() - 60)
        _session(db, "cur")
        db.patch_working_state("older", {"lane": _lane(repo="/srv/old")}, source="test")
        time.sleep(0.01)
        db.patch_working_state("newer", {"lane": _lane(repo="/srv/new")}, source="test")

        assert db.get_recent_lane(exclude_session_id="cur")["repo_path"] == "/srv/new"

    def test_ignores_sessions_with_no_lane(self, db):
        _session(db, "chatty")
        _session(db, "cur")
        db.patch_working_state("chatty", {"active_task": "said hello"}, source="test")
        assert db.get_recent_lane(exclude_session_id="cur") is None

    def test_ignores_lanes_older_than_the_cutoff(self, db):
        _session(db, "stale")
        _session(db, "cur")
        db.patch_working_state("stale", {"lane": _lane()}, source="test")
        # Age the working-state row itself, not the session.
        db._execute_write(  # noqa: SLF001
            lambda conn: conn.execute(
                "UPDATE session_working_state SET updated_at = ? WHERE session_id = 'stale'",
                (time.time() - 60 * 60 * 24 * 8,),
            )
        )
        assert db.get_recent_lane(exclude_session_id="cur", max_age_hours=24) is None
        assert db.get_recent_lane(exclude_session_id="cur", max_age_hours=24 * 30) is not None

    def test_ignores_subagent_and_cron_sessions(self, db):
        _session(db, "sub", source="subagent")
        _session(db, "cron", source="cron")
        _session(db, "cur")
        db.patch_working_state("sub", {"lane": _lane(repo="/srv/sub")}, source="test")
        db.patch_working_state("cron", {"lane": _lane(repo="/srv/cron")}, source="test")
        assert db.get_recent_lane(exclude_session_id="cur") is None

    def test_empty_db_returns_none(self, db):
        assert db.get_recent_lane(exclude_session_id="anything") is None


class TestRenderedBlock:
    def test_block_names_repo_and_marks_it_unconfirmed(self, db):
        from agent.lane_recall import render_recent_lane_block

        block = render_recent_lane_block(_lane(prompt_file="/tmp/p.md"))
        assert "<recent-lane>" in block and "</recent-lane>" in block
        assert REPO in block
        # A lane from a *previous* session is a hint, never an instruction.
        assert "previous session" in block.lower()
        assert "confirm" in block.lower() or "ask" in block.lower()

    def test_no_lane_renders_nothing(self):
        from agent.lane_recall import render_recent_lane_block

        assert render_recent_lane_block(None) == ""


class TestEndToEnd:
    """Session A records a lane; session B, brand new, is told about it."""

    def test_fresh_session_is_handed_the_previous_lane(self, db, monkeypatch):
        from agent.lane_recall import render_recent_lane_block

        _session(db, "yesterday")
        _session(db, "today")

        # Session A finishes a turn in which Codex was launched against a repo.
        from agent.turn_finalizer import _lane_for_turn

        history = [
            {
                "role": "assistant",
                "content": f"```bash\ncodex exec --cd {REPO} --sandbox workspace-write - < /tmp/p.md\n```",
            }
        ]
        monkeypatch.setattr("agent.runtime_cwd.resolve_context_cwd", lambda: "/home/endlessblink")
        lane = _lane_for_turn(agent=None, messages=history)
        assert lane is not None and lane["repo_path"] == REPO
        db.patch_working_state("yesterday", {"lane": lane}, source="turn_finalizer")

        # Session B starts blind. It must be handed the lane, marked unconfirmed.
        recalled = db.get_recent_lane(exclude_session_id="today")
        block = render_recent_lane_block(recalled)
        assert REPO in block
        assert "/tmp/p.md" in block
        assert "hint" in block.lower()

    def test_a_chatty_turn_does_not_erase_an_established_lane(self, db, monkeypatch):
        from agent.turn_finalizer import _lane_for_turn

        monkeypatch.setattr("agent.runtime_cwd.resolve_context_cwd", lambda: "/home/endlessblink")
        # No repo, no agent launch, home-dir cwd -> nothing identifiable.
        assert _lane_for_turn(agent=None, messages=[{"role": "user", "content": "thanks!"}]) is None
