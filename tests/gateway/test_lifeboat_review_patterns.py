"""Patterns from the review log, turned into checks.

The governance log records twenty-six named failure patterns observed in real
conversations. Several are already enforced — the question loop, premature
closure, contextless re-entry, unsupported safety escalation. These are four
that were written down and never enforced, each deterministic enough to decide
without guessing at meaning.

Named by their log id so the pattern and its check stay findable from each
other.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_rewrite import review_verdict


HURT = "היא אמרה לי שאני מעיק ואני לא מפסיק לחשוב על זה"
DECIDING = "אני לא יודע אם להמשיך איתה או לא"
SCREENSHOT = "הנה מה שהיא כתבה לי"


# --- P-023: affect mirroring that only restates the state -------------------

@pytest.mark.parametrize(
    "reply",
    [
        "זה נשמע כבד מאוד. באמת כבד.",
        "אני שומע כמה זה כואב. זה כואב.",
        "וואו, זה ממש קשה. נשמע קשה מאוד.",
    ],
)
def test_pure_affect_mirroring_is_rejected(reply: str) -> None:
    assert review_verdict(HURT, reply).accepted is False


def test_reflection_that_moves_somewhere_is_accepted() -> None:
    reply = "זה נשמע כבד. מה במשפט שלה נחת הכי חזק?"

    assert review_verdict(HURT, reply).accepted is True


# --- P-007: handing his decision to someone else ----------------------------

@pytest.mark.parametrize(
    "reply",
    [
        "אולי תשאל אותה מה היא חושבת ותחליטו יחד.",
        "כדאי שתתייעץ עם חבר קרוב ותראה מה הוא אומר.",
        "maybe ask her what she wants and decide from there.",
    ],
)
def test_offloading_his_decision_is_rejected(reply: str) -> None:
    assert review_verdict(DECIDING, reply).accepted is False


def test_keeping_the_decision_with_him_is_accepted() -> None:
    reply = "מה מושך אותך להישאר, ומה מושך אותך לעזוב?"

    assert review_verdict(DECIDING, reply).accepted is True


# --- P-005: asserting what another person thinks or feels -------------------

@pytest.mark.parametrize(
    "reply",
    [
        "ברור שהיא מרגישה מוצפת מזה.",
        "היא בטח חושבת שאתה לוחץ עליה.",
        "she clearly feels overwhelmed by this.",
    ],
)
def test_inferring_another_persons_inner_state_is_rejected(reply: str) -> None:
    assert review_verdict(SCREENSHOT, reply).accepted is False


def test_naming_what_is_unknowable_is_accepted() -> None:
    reply = "אין לי דרך לדעת מה היא מרגישה. מה בזה הכי מעסיק אותך?"

    assert review_verdict(SCREENSHOT, reply).accepted is True


# --- the patterns already enforced stay enforced ----------------------------

def test_previously_enforced_patterns_still_hold() -> None:
    """P-006 premature closure — still rejected after the new rules land."""
    assert review_verdict(HURT, "תודה על השיתוף, נעצור כאן.").accepted is False


def test_an_ordinary_grounded_reply_is_unaffected() -> None:
    assert review_verdict(HURT, "מתי היא אמרה את זה?").accepted is True


# --- clinical register creates distance -------------------------------------
#
# 2026-08-23, Noam on a live reply: "too much like a therapist and that causes
# distance between me and it". Not debrief-specific — it applies to everything
# it says.

@pytest.mark.parametrize(
    "reply",
    [
        "החוט הפעיל שנשאר הוא זה. מה איתו?",
        "אני אחזיק לך מקום לזה. מה עולה?",
        "מה זה מפעיל אצלך?",
        "בוא נעבד את זה יחד. מה מרגיש?",
    ],
)
def test_therapist_register_is_rejected(reply: str) -> None:
    assert review_verdict(HURT, reply).accepted is False


def test_plain_speech_about_the_same_thing_is_accepted() -> None:
    assert review_verdict(HURT, "מתי היא אמרה את זה?").accepted is True


def test_announcing_its_own_method_is_rejected() -> None:
    reply = "אני אראיין אותך שאלה אחת בכל פעם. איפה זה עומד?"

    assert review_verdict(HURT, reply).accepted is False


def test_making_him_pick_the_subject_is_rejected_everywhere() -> None:
    assert review_verdict(HURT, "במה תרצה להתחיל?").accepted is False
    assert review_verdict(HURT, "מה היה הדבר הראשון שהיה לך בראש?").accepted is False


def test_a_preamble_about_the_correction_is_rejected_everywhere() -> None:
    reply = "נכון. ביקשת שאוביל, ואני ביקשתי ממך לבחור. מתי היא אמרה את זה?"

    assert review_verdict(HURT, reply).accepted is False


# --- a menu of readings of his own experience -------------------------------
#
# 2026-08-23 20:04, after he said nothing had appeared: "וכשראית שאין כלום —
# לאן הראש הלך קודם: ל'באסה', 'שוב כלום', ל'אולי עוד מעט', או ישר ל'זה פשוט
# לא יקרה לי'?" Four readings of his own experience, offered for him to pick
# from. The old menu rule only caught "would you rather X or Y".

@pytest.mark.parametrize(
    "reply",
    [
        "לאן הראש הלך קודם: ל„באסה”, „שוב כלום”, ל„אולי עוד מעט”, "
        "או ישר ל„זה פשוט לא יקרה לי”?",
        "זה יותר עצב, או תסכול, או פשוט עייפות?",
        "was it sadness, or frustration, or just tiredness?",
    ],
)
def test_offering_readings_of_his_experience_is_rejected(reply: str) -> None:
    assert review_verdict(HURT, reply).accepted is False


def test_one_alternative_is_not_a_menu() -> None:
    """Two options is a real question; four is a form."""
    assert review_verdict(HURT, "זה נחת כעלבון או כדאגה?").accepted is True
