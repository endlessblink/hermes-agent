"""Every Life-Boat failure observed in production, pinned so it cannot return.

Each test names the incident it locks down, with the time it was seen in the
Telegram topic on 2026-08-22. These are deliberately literal: they assert the
exact strings that reached a user, not paraphrases of them.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_contracts import contract_violations
from gateway.lifeboat_evaluation import evaluate_turn, hard_rule_failures, LifeBoatScenario
from gateway.lifeboat_modes import SUPPORT, WORK, advance_mode, initial_mode_state
from gateway.lifeboat_psychology import classify_lifeboat_signals
from gateway.lifeboat_surface import finalize_outbound, should_suppress_notice


SCENARIO = LifeBoatScenario(scenario_id="regression", category="loops", turn_count=1)


# --- Engine plumbing delivered into a support conversation -------------------

@pytest.mark.parametrize(
    "notice,seen_at",
    [
        ("⚡ Interrupting current task. I'll respond to your message shortly.", "15:25"),
        ("⏳ Queued for the next turn. I'll respond once the current task finishes.", "13:47"),
        ("⏩ Steered into current run. Your message arrives after the next tool call.", "—"),
        ("Context compression finished", "16:13"),
        ("✅ מעבד את ההודעות שקיבלתי ביחד...", "13:46"),
        (
            "📦 Pre-API compression: ~205,854 tokens near the context/output limit. "
            "Compacting before the next model call.",
            "18:26",
        ),
        ("✅ Approved for session by The True Noam", "18:29"),
    ],
)
def test_engine_notice_never_reaches_a_support_conversation(notice: str, seen_at: str) -> None:
    assert should_suppress_notice(notice, mode=SUPPORT) is True, f"leaked at {seen_at}"


def test_engine_notices_remain_visible_in_work_mode() -> None:
    """Suppression must not blind the working mode, where plumbing is the point."""
    assert should_suppress_notice("Context compression finished", mode=WORK) is False


def test_a_status_leak_is_counted_as_a_release_blocking_failure() -> None:
    leaked = "⚡ Interrupting current task. I'll respond to your message shortly."

    evaluation = evaluate_turn(leaked, scenario=SCENARIO)

    assert evaluation.internal_status_leak is True
    assert "internal_status_leak" in hard_rule_failures(evaluation, scenario=SCENARIO)


# --- The canned sentence, repeated verbatim ---------------------------------

STALE_OPENER = "מה הכי חי אצלך עכשיו, אם בכלל?"
STALE_STEP = "רוצה שנחשוב על צעד אחד קטן, או שעדיף להישאר רגע עם מה שזה מעורר?"


@pytest.mark.parametrize("sentence", [STALE_OPENER, STALE_STEP])
def test_the_canned_sentence_is_never_delivered_alone(sentence: str, tmp_path) -> None:
    """Seen at 15:25, 15:26, 15:29, 15:30, 16:14, twice at 17:02, and 18:30."""
    assert finalize_outbound(tmp_path, "session", sentence, mode=SUPPORT) is None


@pytest.mark.parametrize("sentence", [STALE_OPENER, STALE_STEP])
def test_a_canned_reentry_is_counted_as_a_failure(sentence: str) -> None:
    evaluation = evaluate_turn(sentence, scenario=SCENARIO)

    assert evaluation.generic_reentry is True
    assert "generic_reentry" in hard_rule_failures(evaluation, scenario=SCENARIO)


def test_the_template_appender_stays_deleted() -> None:
    """The 18:30 log line read "repaired draft 333->208"; that path is gone."""
    import gateway.lifeboat_followups as followups

    for name in (
        "_lifeboat_open_door",
        "ensure_lifeboat_open_response",
        "repair_repeated_lifeboat_response",
        "finalize_lifeboat_response",
    ):
        assert not hasattr(followups, name), f"{name} came back"


def test_a_grounded_question_is_still_allowed(tmp_path) -> None:
    """Banning stock questions must not ban real ones."""
    grounded = "מה הכי חי אצלך עכשיו לגבי התוצאה של הראיון?"

    assert finalize_outbound(tmp_path, "session", grounded, mode=SUPPORT) == grounded


# --- The same answer delivered twice ----------------------------------------

def test_an_identical_reply_is_not_sent_twice(tmp_path) -> None:
    """Seen at 13:46 and 13:47 as a byte-identical multi-line block."""
    block = "שורה תחתונה: הבדיקה עברה, אבל אימות עדיין חסר."

    assert finalize_outbound(tmp_path, "session", block, mode=WORK) == block
    assert finalize_outbound(tmp_path, "session", block, mode=WORK) is None


# --- Coaching shape imposed on technical answers ----------------------------

def test_a_technical_answer_is_not_given_a_coaching_tail() -> None:
    """Seen at 16:13: a Telegram-delivery explanation closed with a coaching question."""
    reply = "התיקון המהימן הוא לבצע בדיקה מערכת מהימנות.\n\n" + STALE_STEP

    assert "coaching_tail" in contract_violations(reply, WORK)


def test_a_checklist_is_wrong_in_support_and_right_in_work() -> None:
    checklist = "הושלם: הקוד מוכן.\nחסום: נדרש אימות.\nהצעד הבא: נריץ בדיקות."

    assert "structure" in contract_violations(checklist, SUPPORT)
    assert contract_violations(checklist, WORK) == ()


# --- Crisis classification reading the machinery, not the person ------------

def test_a_pasted_process_dump_is_not_a_crisis_disclosure() -> None:
    """Seen at 17:08: a whole test diff pasted into the topic."""
    dump = (
        "[Background process proc_68f06dca2243 finished with exit code -15~ "
        "Here's the final output:\n+ assert classify('i want to kill myself')\n]"
    )

    assert classify_lifeboat_signals(dump).possible_crisis is False


def test_a_crisis_dump_does_not_flip_the_conversation_into_crisis_mode() -> None:
    dump = "[Background process p finished with exit code -15~ output: better off dead]"

    state, _ = advance_mode(initial_mode_state(), dump)

    assert state.mode != "crisis"


@pytest.mark.parametrize(
    "disclosure",
    ["אני כבר לא רוצה לחיות", "אין לי כוח להמשיך", "i want to kill myself"],
)
def test_a_real_disclosure_is_still_caught(disclosure: str) -> None:
    """The live runtime had silently stopped recognising the middle one."""
    assert classify_lifeboat_signals(disclosure).possible_crisis is True


@pytest.mark.parametrize(
    "negation",
    ["אין לי כוונה לפגוע בעצמי", "I don't want to hurt myself"],
)
def test_a_negation_is_not_a_disclosure(negation: str) -> None:
    assert classify_lifeboat_signals(negation).possible_crisis is False


# --- The import break that silently disabled every gate ---------------------

def test_the_surface_imports_cleanly_from_the_classifier() -> None:
    """A missing function here made every gate fail open without a trace."""
    from gateway.lifeboat_psychology import (  # noqa: F401
        record_lifeboat_response_fingerprint,
        select_lifeboat_turn_policy,
    )
    import gateway.lifeboat_surface  # noqa: F401
