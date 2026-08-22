"""What each mode is allowed to say, and what the conversation remembers."""

from __future__ import annotations

from gateway.lifeboat_contracts import contract_for, contract_violations
from gateway.lifeboat_modes import CRISIS, PAUSED, SUPPORT, TIME, WORK, advance_mode, initial_mode_state
from gateway.lifeboat_modes import load_mode_state, save_mode_state


CHECKLIST = "הושלם: הקוד מוכן.\nחסום: נדרש אימות.\nהצעד הבא: נריץ בדיקות."
OPEN_SUPPORT = "אני איתך בזה לרגע. מה קרה שם?"


def test_support_wants_an_open_question() -> None:
    assert contract_for(SUPPORT).wants_open_question is True


def test_work_does_not_want_an_open_question() -> None:
    assert contract_for(WORK).wants_open_question is False


def test_work_allows_structure() -> None:
    assert contract_for(WORK).allows_structure is True


def test_support_does_not_allow_structure() -> None:
    assert contract_for(SUPPORT).allows_structure is False


def test_support_is_shorter_than_work() -> None:
    assert contract_for(SUPPORT).max_chars < contract_for(WORK).max_chars


def test_a_checklist_violates_the_support_contract() -> None:
    assert "structure" in contract_violations(CHECKLIST, SUPPORT)


def test_a_checklist_is_fine_in_work_mode() -> None:
    assert contract_violations(CHECKLIST, WORK) == ()


def test_a_coaching_tail_violates_the_work_contract() -> None:
    """This is the Telegram-delivery answer that ended with a coaching question."""
    reply = "התיקון בוצע והבדיקות עברו.\n\nרוצה שנחשוב על צעד אחד קטן?"

    assert "coaching_tail" in contract_violations(reply, WORK)


def test_a_closed_support_reply_is_flagged() -> None:
    assert "closed" in contract_violations("לסיכום, זה פשוט קורה לפעמים.", SUPPORT)


def test_a_good_support_reply_passes() -> None:
    assert contract_violations(OPEN_SUPPORT, SUPPORT) == ()


def test_an_overlong_support_reply_is_flagged() -> None:
    assert "too_long" in contract_violations("א" * 4000, SUPPORT)


def test_crisis_replies_may_be_longer_than_support() -> None:
    assert contract_for(CRISIS).max_chars >= contract_for(SUPPORT).max_chars


def test_crisis_never_wants_structure() -> None:
    assert contract_for(CRISIS).allows_structure is False


def test_every_mode_has_a_contract() -> None:
    for mode in (SUPPORT, TIME, WORK, CRISIS, PAUSED):
        assert contract_for(mode) is not None


def test_mode_state_persists_per_session(tmp_path) -> None:
    state = initial_mode_state()
    state, _ = advance_mode(state, "/work")
    save_mode_state(tmp_path, "session-a", state)

    assert load_mode_state(tmp_path, "session-a").mode == WORK


def test_sessions_do_not_share_a_mode(tmp_path) -> None:
    state, _ = advance_mode(initial_mode_state(), "/work")
    save_mode_state(tmp_path, "session-a", state)

    assert load_mode_state(tmp_path, "session-b").mode == SUPPORT


def test_an_unknown_session_starts_in_support(tmp_path) -> None:
    assert load_mode_state(tmp_path, "never-seen").mode == SUPPORT


def test_time_mode_allows_structure_but_stays_conversational() -> None:
    contract = contract_for(TIME)

    assert contract.allows_structure is True
    assert contract.max_chars < contract_for(WORK).max_chars
