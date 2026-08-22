"""Life-Boat outbound chokepoint.

One place decides what the Life-Boat topic is ever allowed to show. It
suppresses gateway engine notices, drops exact repeats, and never invents
replacement prose of its own.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_surface import finalize_outbound, should_suppress_notice


QUEUED_NOTICE = "⏳ Queued for the next turn. I'll respond once the current task finishes."
INTERRUPT_NOTICE = "⚡ Interrupting current task. I'll respond to your message shortly."
COMPRESSION_NOTICE = "Context compression finished"
STEER_NOTICE = "⏩ Steered into current run. Your message arrives after the next tool call."

COACHING_REPLY = "אני שומע כמה זה יושב עליך כרגע. מה הכי נוכח בזה?"
STALE_REENTRY = "מה הכי חי אצלך עכשיו, אם בכלל?"
GENERIC_STEP_OR_FEELING = "רוצה שנחשוב על צעד אחד קטן, או שעדיף להישאר רגע עם מה שזה מעורר?"


@pytest.mark.parametrize(
    "notice",
    [QUEUED_NOTICE, INTERRUPT_NOTICE, COMPRESSION_NOTICE, STEER_NOTICE],
)
def test_engine_notices_are_suppressed_in_support_mode(notice: str) -> None:
    assert should_suppress_notice(notice, mode="support") is True


def test_engine_notice_is_suppressed_during_crisis() -> None:
    assert should_suppress_notice(QUEUED_NOTICE, mode="crisis") is True


def test_engine_notice_is_allowed_in_work_mode() -> None:
    """Work mode is hands-on, so knowing the engine is busy is useful there."""
    assert should_suppress_notice(QUEUED_NOTICE, mode="work") is False


def test_ordinary_coaching_reply_is_never_treated_as_a_notice() -> None:
    assert should_suppress_notice(COACHING_REPLY, mode="support") is False


def test_hebrew_batching_notice_is_suppressed() -> None:
    assert should_suppress_notice(
        "✅ מעבד את ההודעות שקיבלתי ביחד...",
        mode="support",
    ) is True


def test_first_reply_is_delivered(tmp_path) -> None:
    assert finalize_outbound(tmp_path, "session-a", COACHING_REPLY) == COACHING_REPLY


def test_exact_repeat_is_suppressed(tmp_path) -> None:
    """The 13:46 / 13:47 double-send: identical text must not go out twice."""
    finalize_outbound(tmp_path, "session-a", COACHING_REPLY)

    assert finalize_outbound(tmp_path, "session-a", COACHING_REPLY) is None


def test_repeat_in_a_different_session_is_delivered(tmp_path) -> None:
    finalize_outbound(tmp_path, "session-a", COACHING_REPLY)

    assert finalize_outbound(tmp_path, "session-b", COACHING_REPLY) == COACHING_REPLY


def test_distinct_replies_are_both_delivered(tmp_path) -> None:
    finalize_outbound(tmp_path, "session-a", COACHING_REPLY)

    second = "ומה קרה אחר כך?"
    assert finalize_outbound(tmp_path, "session-a", second) == second


def test_reply_without_a_question_keeps_the_model_wording(tmp_path) -> None:
    """The canned Hebrew opener must never be stapled onto a reply again."""
    flat = "זה נשמע כמו שבוע כבד מאוד."

    delivered = finalize_outbound(tmp_path, "session-a", flat)

    assert delivered == flat
    assert "מה הכי חי אצלך עכשיו" not in (delivered or "")


def test_checklist_reply_in_work_mode_is_left_alone(tmp_path) -> None:
    """Work answers are allowed to be structured, with no coaching tail added."""
    status = "הושלם: הקוד מוכן.\nחסום: נדרש אימות מחדש.\nהצעד הבא: נריץ בדיקות."

    delivered = finalize_outbound(tmp_path, "session-a", status, mode="work")

    assert delivered == status
    assert "רוצה שנחשוב על צעד אחד קטן" not in (delivered or "")


def test_empty_reply_is_suppressed(tmp_path) -> None:
    assert finalize_outbound(tmp_path, "session-a", "   ") is None


@pytest.mark.parametrize(
    "response",
    [
        STALE_REENTRY,
        "  מה  הכי חי אצלך עכשיו , אם בכלל ?  ",
        "מה-הכי-חי-אצלך-עכשיו,אם-בכלל؟",
    ],
)
def test_banned_contextless_reentry_is_silenced(tmp_path, response: str) -> None:
    assert finalize_outbound(tmp_path, "session-a", response) is None


def test_banned_generic_step_or_feeling_prompt_is_silenced_when_alone(tmp_path) -> None:
    assert finalize_outbound(tmp_path, "session-a", GENERIC_STEP_OR_FEELING) is None


def test_banned_generic_tail_is_removed_without_replacement(tmp_path) -> None:
    response = f"הפחד מהתוצאה עדיין מחזיק את השיחה. {STALE_REENTRY}"

    assert finalize_outbound(tmp_path, "session-a", response) == "הפחד מהתוצאה עדיין מחזיק את השיחה"


def test_both_banned_tails_are_removed_from_substantive_context(tmp_path) -> None:
    response = (
        "נשאר עם מה שקרה בראיון ולא עם שאלה כללית. "
        f"{STALE_REENTRY} {GENERIC_STEP_OR_FEELING}"
    )

    assert finalize_outbound(tmp_path, "session-a", response) == "נשאר עם מה שקרה בראיון ולא עם שאלה כללית"


def test_named_concrete_topic_question_is_preserved(tmp_path) -> None:
    response = "מה הכי חי אצלך עכשיו לגבי התוצאה של הראיון?"

    assert finalize_outbound(tmp_path, "session-a", response) == response


def test_suppression_receipt_is_redacted(tmp_path, caplog) -> None:
    with caplog.at_level("INFO"):
        assert finalize_outbound(tmp_path, "session-a", STALE_REENTRY) is None

    assert "reason=generic_reentry" in caplog.text
    assert "message_content=redacted" in caplog.text
    assert STALE_REENTRY not in caplog.text


PRE_API_NOTICE = (
    "📦 Pre-API compression: ~205,854 tokens near the context/output limit. "
    "Compacting before the next model call."
)
APPROVAL_NOTICE = "✅ Approved for session by The True Noam"


def test_pre_api_compression_notice_is_suppressed() -> None:
    """This exact notice reached the topic at 18:26 with the gate already live."""
    assert should_suppress_notice(PRE_API_NOTICE, mode="support") is True


def test_pre_api_compression_notice_is_allowed_in_work_mode() -> None:
    assert should_suppress_notice(PRE_API_NOTICE, mode="work") is False


def test_approval_confirmation_is_suppressed_in_support() -> None:
    assert should_suppress_notice(APPROVAL_NOTICE, mode="support") is True


def test_approval_confirmation_is_allowed_in_work_mode() -> None:
    assert should_suppress_notice(APPROVAL_NOTICE, mode="work") is False


def test_a_reply_mentioning_compression_in_prose_is_not_a_notice() -> None:
    """Talking about compression is not the same as the engine announcing it."""
    reply = "דיברנו על זה שהזיכרון מתמלא. איך זה מרגיש לך?"

    assert should_suppress_notice(reply, mode="support") is False


SKILLS_WARNING = "⚠️ הסקילים personal-coaching, obsidian ו־personal-context-governance לא נמצאו ולכן דולגו."
SKILLS_WARNING_EN = "⚠️ Skills personal-coaching, obsidian were not found and were skipped."


def test_a_skills_warning_is_suppressed_in_support() -> None:
    """Delivered live on 2026-08-22 above the nightly summary."""
    assert should_suppress_notice(SKILLS_WARNING, mode="support") is True


def test_an_english_skills_warning_is_suppressed_in_support() -> None:
    assert should_suppress_notice(SKILLS_WARNING_EN, mode="support") is True


def test_a_skills_warning_is_visible_in_work_mode() -> None:
    assert should_suppress_notice(SKILLS_WARNING, mode="work") is False


def test_prose_mentioning_a_skill_is_not_a_warning() -> None:
    reply = "דיברנו על זה שאתה מנסה לפתח מיומנות חדשה. איך זה הולך?"

    assert should_suppress_notice(reply, mode="support") is False
