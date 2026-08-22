"""BUG-30: a superseded turn must not answer after a newer one is handled.

The gateway already stamps every turn with a monotonic generation and checks it
at several points while the agent runs. It did not check it at the last step --
delivery. A run superseded after its final checkpoint therefore still sent its
reply, which is how a stale answer arrived in the Life-Boat topic after the
user had already moved on.

The guard tested here is the one at that final boundary.
"""

from __future__ import annotations

import logging

from unittest.mock import MagicMock

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner


SESSION = "agent:life-advisor:telegram:group:-1004230590253:2"


def _make_runner() -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    runner.adapters = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._session_run_generation = {}
    runner._pending_messages = {}
    runner._draining = False
    runner._update_runtime_status = MagicMock()
    return runner


def test_a_current_run_is_allowed_to_deliver() -> None:
    runner = _make_runner()
    generation = runner._begin_session_run_generation(SESSION)

    assert runner._delivery_superseded(SESSION, generation) is False


def test_a_superseded_run_is_not_allowed_to_deliver() -> None:
    """A newer user turn arrived while this one was finishing."""
    runner = _make_runner()
    generation = runner._begin_session_run_generation(SESSION)
    runner._invalidate_session_run_generation(SESSION, reason="new_user_turn")

    assert runner._delivery_superseded(SESSION, generation) is True


def test_only_the_newest_of_several_rapid_turns_may_deliver() -> None:
    """Rapid multi-message input: three turns land, only the last answers."""
    runner = _make_runner()
    first = runner._begin_session_run_generation(SESSION)
    second = runner._begin_session_run_generation(SESSION)
    third = runner._begin_session_run_generation(SESSION)

    assert runner._delivery_superseded(SESSION, first) is True
    assert runner._delivery_superseded(SESSION, second) is True
    assert runner._delivery_superseded(SESSION, third) is False


def test_a_restart_does_not_let_a_stale_queued_turn_deliver() -> None:
    """A resume superseded by a real inbound message must stay silent."""
    runner = _make_runner()
    queued = runner._begin_session_run_generation(SESSION)
    runner._invalidate_session_run_generation(SESSION, reason="startup_resume_superseded")

    assert runner._delivery_superseded(SESSION, queued) is True


def test_multi_part_batching_within_one_turn_still_delivers() -> None:
    """Intentional multi-part output shares one generation and must survive."""
    runner = _make_runner()
    generation = runner._begin_session_run_generation(SESSION)

    assert runner._delivery_superseded(SESSION, generation) is False
    assert runner._delivery_superseded(SESSION, generation) is False


def test_a_run_without_a_generation_is_not_treated_as_superseded() -> None:
    """Paths that never claimed a token must keep working unchanged."""
    runner = _make_runner()

    assert runner._delivery_superseded(SESSION, None) is False


def test_an_unknown_session_is_not_treated_as_superseded() -> None:
    runner = _make_runner()

    assert runner._delivery_superseded("", 3) is False


def test_the_supersede_receipt_is_recorded_without_user_content(caplog) -> None:
    runner = _make_runner()
    generation = runner._begin_session_run_generation(SESSION)
    runner._invalidate_session_run_generation(SESSION, reason="new_user_turn")

    with caplog.at_level(logging.INFO, logger="gateway.run"):
        assert runner._delivery_superseded(SESSION, generation) is True

    text = caplog.text
    assert "superseded" in text
    assert "message_content=redacted" in text


def test_the_receipt_names_the_session_and_generation(caplog) -> None:
    runner = _make_runner()
    generation = runner._begin_session_run_generation(SESSION)
    runner._invalidate_session_run_generation(SESSION, reason="new_user_turn")

    with caplog.at_level(logging.INFO, logger="gateway.run"):
        runner._delivery_superseded(SESSION, generation)

    assert SESSION in caplog.text
    assert str(generation) in caplog.text


def test_a_session_never_registered_is_not_treated_as_superseded() -> None:
    """Fail open: a run the generation map has never seen is not evidence of
    supersession. Treating an unknown session as stale silently blanked
    ordinary replies in paths that never claim a generation token."""
    runner = _make_runner()

    assert runner._delivery_superseded(SESSION, 1) is False
    assert runner._delivery_superseded(SESSION, 7) is False


def test_a_known_session_still_supersedes_correctly() -> None:
    """Failing open for unknown sessions must not weaken the real guard."""
    runner = _make_runner()
    stale = runner._begin_session_run_generation(SESSION)
    runner._begin_session_run_generation(SESSION)

    assert runner._delivery_superseded(SESSION, stale) is True
