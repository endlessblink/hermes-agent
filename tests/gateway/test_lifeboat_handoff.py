"""Work requests leave Life-Boat instead of being answered inside it.

Telegram will not deliver a message from one bot to another, and this install
shares a single bot across every topic, so the request is passed to the
Orchestrator profile in-process and answered in its own topic.
"""

from __future__ import annotations

from gateway.lifeboat_handoff import (
    ORCHESTRATOR_PROFILE,
    HandoffRequest,
    build_handoff,
    pointer_line,
    should_hand_off,
)
from gateway.lifeboat_modes import CRISIS, SUPPORT, TIME, WORK


def test_work_mode_hands_off() -> None:
    assert should_hand_off(WORK, "תתקן את הבאג במסירה") is True


def test_support_mode_never_hands_off() -> None:
    assert should_hand_off(SUPPORT, "אני מרגיש כבד") is False


def test_time_mode_never_hands_off() -> None:
    assert should_hand_off(TIME, "בוא נתכנן את השבוע") is False


def test_crisis_never_hands_off_even_with_technical_words() -> None:
    """Safety outranks routing; a person in crisis is not dispatched anywhere."""
    assert should_hand_off(CRISIS, "יש באג ואני לא רוצה לחיות") is False


def test_the_user_can_keep_a_request_local() -> None:
    assert should_hand_off(WORK, "אל תעביר, תענה לי כאן על הבאג") is False


def test_a_handoff_targets_the_orchestrator_profile() -> None:
    request = build_handoff("תתקן את הבאג במסירה", session_key="s")

    assert isinstance(request, HandoffRequest)
    assert request.target_profile == ORCHESTRATOR_PROFILE


def test_a_handoff_carries_the_users_own_words() -> None:
    request = build_handoff("תתקן את הבאג במסירה", session_key="s")

    assert "תתקן את הבאג במסירה" in request.prompt


def test_a_handoff_records_where_it_came_from() -> None:
    request = build_handoff("תתקן את הבאג", session_key="session-a")

    assert request.origin_session_key == "session-a"


def test_the_pointer_line_names_the_destination() -> None:
    line = pointer_line()

    assert "Orchestrator" in line
    assert len(line) < 200


def test_the_pointer_line_is_not_a_coaching_question() -> None:
    assert "?" not in pointer_line().replace("Orchestrator", "")


def test_an_empty_request_is_not_handed_off() -> None:
    assert should_hand_off(WORK, "   ") is False
