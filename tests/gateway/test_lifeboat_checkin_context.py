"""What a check-in is allowed to open from.

The context feed handed the check-in whatever the user said most recently. On
2026-08-22 that was a day of bot debugging, so a support check-in would have
opened by asking about a deploy. Worse, an earlier version invented a quote
outright when it had nothing to draw on.

So the rule is: prefer material the person actually brought as their own, drop
operational chatter, and when nothing qualifies say so plainly rather than
reach for filler.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_checkin_context import (
    NOTHING_RECENT,
    is_operational,
    select_checkin_anchors,
)


EMOTIONAL = [
    "אני מרגיש ממש כבד היום",
    "היה לי קשה אחרי הפגישה עם אמא",
    "אני תקוע בלופ של ביקורת עצמית",
]
OPERATIONAL = [
    "תעצור את העבודה על הבוט דרך קודקס",
    "מה הסטטוס עכשיו?",
    "עובד?",
    "בדוק עכשיו מה השתנה",
    "תעשה ניתוח עצמי מקצה לקצה",
    "deploy the fix and run the tests",
    # Taken verbatim from the 2026-08-22 feed: a question about the
    # assistant's own diagnostics, which slipped through the first filter.
    "כרגע נראה שהן לא קורות. למה אין לך דרך לאבחן משהו כזה?",
    "אתה לא מצליח לזהות את זה לבד?",
]


@pytest.mark.parametrize("text", OPERATIONAL)
def test_operational_chatter_is_recognised(text: str) -> None:
    assert is_operational(text) is True


@pytest.mark.parametrize("text", EMOTIONAL)
def test_personal_material_is_not_operational(text: str) -> None:
    assert is_operational(text) is False


def test_a_day_of_debugging_yields_no_anchor() -> None:
    """This is the live case: nothing here is a support conversation."""
    assert select_checkin_anchors(OPERATIONAL) == ()


def test_personal_material_is_offered() -> None:
    anchors = select_checkin_anchors(EMOTIONAL)

    assert anchors
    assert any("כבד" in a or "קשה" in a or "לופ" in a for a in anchors)


def test_personal_material_is_preferred_over_operational() -> None:
    anchors = select_checkin_anchors(OPERATIONAL + EMOTIONAL)

    assert anchors
    for anchor in anchors:
        assert not is_operational(anchor)


def test_the_newest_personal_material_comes_last() -> None:
    anchors = select_checkin_anchors(EMOTIONAL)

    assert anchors[-1] == EMOTIONAL[-1]


def test_the_number_of_anchors_is_bounded() -> None:
    many = EMOTIONAL * 10

    assert len(select_checkin_anchors(many)) <= 4


def test_an_empty_feed_yields_nothing() -> None:
    assert select_checkin_anchors([]) == ()


def test_blank_turns_are_ignored() -> None:
    assert select_checkin_anchors(["", "   ", "\n"]) == ()


def test_the_nothing_recent_notice_forbids_inventing_a_callback() -> None:
    """The 2026-08-12 incident: it invented a quote rather than open plainly."""
    assert "invent" in NOTHING_RECENT.lower()
    assert "plainly" in NOTHING_RECENT.lower()


def test_a_long_turn_is_trimmed() -> None:
    anchors = select_checkin_anchors(["אני מרגיש כבד " + "מאוד " * 200])

    assert anchors
    assert len(anchors[0]) <= 300
