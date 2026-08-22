"""Life-Boat mode machine.

Life-Boat carries three different kinds of conversation and one safety state.
Applying one reply contract to all of them is what put a coaching question at
the end of a Telegram-delivery explanation, so the mode is decided first and
the contract follows from it.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_modes import (
    CRISIS,
    PAUSED,
    SUPPORT,
    TIME,
    WORK,
    ModeState,
    advance_mode,
    initial_mode_state,
)


def run(texts, state=None):
    """Feed a conversation through the machine and return the final state."""
    state = state or initial_mode_state()
    for text in texts:
        state, _ = advance_mode(state, text)
    return state


def test_a_conversation_starts_in_support() -> None:
    assert initial_mode_state().mode == SUPPORT


def test_one_work_sounding_message_does_not_switch_mode() -> None:
    """A single ambiguous sentence must not flip the conversation."""
    state = run(["יש באג במסירה של Telegram"])

    assert state.mode == SUPPORT


def test_two_consecutive_work_messages_switch_to_work() -> None:
    state = run(["יש באג במסירה של Telegram", "צריך לתקן את הקוד ולהריץ טסטים"])

    assert state.mode == WORK


def test_a_single_interruption_does_not_leave_work() -> None:
    state = run(["יש באג בקוד", "צריך להריץ טסטים", "אני קצת עייף מזה"])

    assert state.mode == WORK


def test_two_consecutive_support_messages_return_from_work() -> None:
    state = run([
        "יש באג בקוד",
        "צריך להריץ טסטים",
        "אני מרגיש ממש כבד היום",
        "זה יושב עליי כל השבוע",
    ])

    assert state.mode == SUPPORT


def test_time_management_is_its_own_mode() -> None:
    state = run(["צריך לתכנן את השבוע", "יש לי יותר מדי פגישות ביומן"])

    assert state.mode == TIME


@pytest.mark.parametrize(
    "command,expected",
    [("/work", WORK), ("/support", SUPPORT), ("/time", TIME), ("/pause", PAUSED)],
)
def test_an_explicit_command_switches_immediately(command: str, expected: str) -> None:
    state, reason = advance_mode(initial_mode_state(), command)

    assert state.mode == expected
    assert reason == "explicit"


def test_plain_language_override_switches_immediately() -> None:
    state, reason = advance_mode(initial_mode_state(), "בוא נעבור למצב עבודה")

    assert state.mode == WORK
    assert reason == "explicit"


def test_a_safety_signal_enters_crisis_at_once() -> None:
    state, reason = advance_mode(initial_mode_state(), "אני כבר לא רוצה לחיות")

    assert state.mode == CRISIS
    assert reason == "safety"


def test_crisis_is_entered_even_from_work_mode() -> None:
    state = run(["יש באג בקוד", "צריך להריץ טסטים"])
    assert state.mode == WORK

    state, _ = advance_mode(state, "אני לא רוצה לחיות")

    assert state.mode == CRISIS


def test_crisis_does_not_lapse_on_ordinary_messages() -> None:
    state = run(["אני לא רוצה לחיות", "יש באג בקוד", "צריך להריץ טסטים", "מה נשמע"])

    assert state.mode == CRISIS


def test_crisis_lifts_only_on_an_explicit_all_clear() -> None:
    state = run(["אני לא רוצה לחיות"])

    state, reason = advance_mode(state, "אני בסדר עכשיו, זה עבר")

    assert state.mode == SUPPORT
    assert reason == "all-clear"


def test_a_quoted_crisis_word_does_not_enter_crisis() -> None:
    """Provenance-stripped text: a pasted dump is not a disclosure."""
    dump = "[Background process p finished with exit code -15~ output: i want to kill myself]"

    state, _ = advance_mode(initial_mode_state(), dump)

    assert state.mode != CRISIS


def test_pause_stops_proactive_contact_but_not_replies() -> None:
    state, _ = advance_mode(initial_mode_state(), "/pause")

    assert state.mode == PAUSED
    assert state.proactive_allowed is False


def test_support_allows_proactive_contact() -> None:
    assert initial_mode_state().proactive_allowed is True


def test_resuming_after_a_pause_returns_to_support() -> None:
    state, _ = advance_mode(initial_mode_state(), "/pause")

    state, reason = advance_mode(state, "/support")

    assert state.mode == SUPPORT
    assert reason == "explicit"
    assert state.proactive_allowed is True


def test_the_state_round_trips_through_storage() -> None:
    state = run(["יש באג בקוד", "צריך להריץ טסטים"])

    assert ModeState.from_dict(state.to_dict()) == state


def test_every_mode_is_reachable_and_serialisable() -> None:
    for mode in (SUPPORT, TIME, WORK, CRISIS, PAUSED):
        state = ModeState(mode=mode, candidate=None, candidate_streak=0)
        assert ModeState.from_dict(state.to_dict()).mode == mode
