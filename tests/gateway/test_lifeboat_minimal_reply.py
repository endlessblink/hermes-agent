"""What may be said to someone who answers "ככה ככה".

A minimal, ambiguous reply is the hardest moment to get right: there is nothing
concrete to reflect, and the temptations are exactly the moves that make a
support conversation feel like an intake form. Probing the live system with
"ככה ככה" showed three of them passing every gate — a capacity survey, a
forced-choice menu, and a checklist that was flagged and delivered anyway.

The Life-Boat quality goal already forbids all three in prose. These make it
enforceable.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_contracts import contract_violations
from gateway.lifeboat_modes import SUPPORT, WORK
from gateway.lifeboat_rewrite import review_verdict


SOSO = "ככה ככה"


@pytest.mark.parametrize(
    "reply",
    [
        "על סקאלה של 1 עד 10, איך היית מדרג את הבוקר?",
        "מ-1 עד 10 איפה אתה?",
        "on a scale of 1 to 10, how is the morning?",
        "איך היית מדרג את האנרגיה שלך היום?",
    ],
)
def test_a_capacity_survey_is_rejected(reply: str) -> None:
    assert review_verdict(SOSO, reply).accepted is False


@pytest.mark.parametrize(
    "reply",
    [
        "רוצה לדבר על זה, או שנעבור למשהו מעשי?",
        "מעדיף לפרק את זה או להישאר עם התחושה?",
        "would you rather talk about it or do something practical?",
    ],
)
def test_a_forced_choice_menu_is_rejected(reply: str) -> None:
    assert review_verdict(SOSO, reply).accepted is False


def test_a_checklist_is_refused_in_support_and_not_merely_noted() -> None:
    checklist = "בוא נפרק: 1. שינה 2. אנרגיה 3. מצב רוח"

    assert "structure" in contract_violations(checklist, SUPPORT)
    assert review_verdict(SOSO, checklist).accepted is False


@pytest.mark.parametrize(
    "reply",
    [
        "ככה ככה זה גם משהו. מה הכי מורגש מזה עכשיו?",
        "אתמול כתבת שהקשר עם רי הרגיש כמו עוד חזית. זה חלק מהככה ככה של היום?",
        "מה עשה את הבוקר ככה ככה ולא משהו אחר?",
    ],
)
def test_an_open_grounded_reply_is_accepted(reply: str) -> None:
    assert review_verdict(SOSO, reply).accepted is True


def test_a_genuine_either_or_about_facts_is_not_a_menu() -> None:
    """Asking which of two things happened is not offering a menu of support."""
    reply = "זה היה בגלל הפגישה עצמה או מה שקרה אחריה?"

    assert review_verdict("היה לי בוקר מוזר", reply).accepted is True


def test_a_scale_in_work_mode_is_still_allowed() -> None:
    """Rating a deploy's risk is not a capacity survey."""
    assert contract_violations("דירוג הסיכון: 3 מתוך 10", WORK) == ()
