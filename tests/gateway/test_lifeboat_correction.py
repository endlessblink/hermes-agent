"""Recognising when Noam says the bot got it wrong.

Corrections were previously just conversation: he would say a subject had
passed, or that a reading was wrong, and nothing changed — the same subject
could come back the next day and the same reading could be repeated. What he
says is the most reliable signal available, and it was the one being thrown
away.

This reads plain language, so there is nothing to remember and no command to
learn. It only recognises what he actually wrote; it never infers a mood.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_correction import Correction, classify_correction


@pytest.mark.parametrize(
    "text",
    [
        "זה כבר לא רלוונטי",
        "זה עבר",
        "כבר לא מעסיק אותי",
        "that's passed",
        "not relevant anymore",
    ],
)
def test_saying_it_has_passed_is_recognised(text: str) -> None:
    assert classify_correction(text) == Correction.PASSED


@pytest.mark.parametrize(
    "text",
    [
        "תפסיק לשאול על זה",
        "אל תעלה את זה יותר",
        "drop it",
        "stop bringing this up",
    ],
)
def test_asking_to_drop_it_is_recognised(text: str) -> None:
    assert classify_correction(text) == Correction.DISMISSED


@pytest.mark.parametrize(
    "text",
    [
        "לא דייקת",
        "זה לא מה שאמרתי",
        "פירשת לא נכון",
        "that's not what I said",
        "you misread that",
    ],
)
def test_saying_the_reading_was_wrong_is_recognised(text: str) -> None:
    assert classify_correction(text) == Correction.MISREAD


@pytest.mark.parametrize(
    "text",
    [
        "כן, בדיוק",
        "נכון",
        "בוא נמשיך עם זה",
        "yes exactly",
    ],
)
def test_agreement_is_recognised_as_engagement(text: str) -> None:
    assert classify_correction(text) == Correction.ENGAGED


@pytest.mark.parametrize(
    "text",
    [
        "היה לי יום ארוך",
        "נפגשתי איתה אתמול",
        "",
        "   ",
    ],
)
def test_ordinary_conversation_is_not_a_correction(text: str) -> None:
    assert classify_correction(text) == Correction.NONE


def test_dismissal_outranks_passed_when_both_appear() -> None:
    """'It passed, stop asking' is a dismissal — the stronger instruction wins."""
    assert classify_correction("זה עבר, אל תעלה את זה יותר") == Correction.DISMISSED


def test_a_question_about_a_subject_is_not_a_dismissal() -> None:
    assert classify_correction("למה אתה שואל על זה?") == Correction.NONE


def test_classification_never_raises() -> None:
    assert classify_correction(None) == Correction.NONE
