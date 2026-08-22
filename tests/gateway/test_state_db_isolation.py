"""No test may read or write the developer's real Hermes database.

hermes_state resolves its default database path once, at import time. Test
modules import it during collection -- before the per-test HERMES_HOME is
installed -- so the cached path kept pointing at the live ~/.hermes/state.db.

The visible cost was a suite that failed in bulk runs and passed in isolation,
because one test read whichever real sessions happened to exist. The quieter
cost is that unit tests were touching personal conversation data at all.
"""

from __future__ import annotations

import os
from pathlib import Path

import hermes_state


def _real_home() -> Path:
    return Path.home() / ".hermes"


def test_the_default_database_is_not_the_developers_real_one() -> None:
    assert Path(hermes_state.DEFAULT_DB_PATH).resolve() != (_real_home() / "state.db").resolve()


def test_the_default_database_lives_under_the_per_test_home() -> None:
    assert str(os.environ["HERMES_HOME"]) in str(hermes_state.DEFAULT_DB_PATH)


def test_a_default_constructed_session_db_stays_inside_the_test_home(tmp_path) -> None:
    """This is the constructor the polluted test used: SessionDB() with no path."""
    db = hermes_state.SessionDB()
    try:
        used = Path(getattr(db, "db_path", hermes_state.DEFAULT_DB_PATH)).resolve()
    finally:
        db.close()

    assert str(_real_home().resolve()) not in str(used)


def test_channel_discovery_sees_no_real_sessions() -> None:
    """The exact failure: twelve of the user's real chats leaked into a test."""
    from gateway.channel_directory import _build_from_sessions

    assert _build_from_sessions("telegram") == []
