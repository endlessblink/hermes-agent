"""BUG-6: a re-entry must reconnect to something concrete.

Returning from a technical detour, Life-Boat kept opening with a broad question
that would fit any conversation with anyone. A literal ban list does not solve
this: the model simply rewords, and the acceptance example for the bug is
already a reword of the sentence that was banned.

What makes these replies wrong is not their wording but their shape -- a short
generic question that names nothing from the conversation it is re-entering.
That is what these tests pin.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_reentry import is_contextless_reentry


UNRESOLVED = "דיברנו על הראיון ואיך שהמנהל הגיב לי, וזה עדיין יושב עליי"


@pytest.mark.parametrize(
    "reply",
    [
        "מה הכי חי אצלך עכשיו, אם בכלל?",
        "מה חי בך כרגע?",                      # the acceptance example
        "מה הכי חי אצלך עכשיו?",
        "מה הכי חי אצלך?",
        "מה עולה בך עכשיו?",
        "מה נוכח אצלך עכשיו?",
        "רוצה שנחשוב על צעד אחד קטן, או שעדיף להישאר רגע עם מה שזה מעורר?",
        "רוצה לחשוב על צעד, או להישאר עם התחושה?",
        "What's most alive for you right now?",
    ],
)
def test_a_generic_reentry_is_rejected(reply: str) -> None:
    assert is_contextless_reentry(reply, user_text=UNRESOLVED) is True


@pytest.mark.parametrize(
    "reply",
    [
        "מה קרה עם המנהל אחרי הפגישה?",
        "מה הכי חי אצלך עכשיו לגבי התוצאה של הראיון?",
        "כשאתה אומר שזה יושב עליך — על מה בדיוק זה יושב?",
        "הראיון עצמו או התגובה של המנהל?",
        # Echoing the user's own phrase is a way of naming their material.
        "איך זה יושב איתך עכשיו?",
    ],
)
def test_a_re_entry_that_names_the_material_is_kept(reply: str) -> None:
    assert is_contextless_reentry(reply, user_text=UNRESOLVED) is False


def test_a_substantive_reply_is_not_a_reentry() -> None:
    reply = (
        "נשמע שהתגובה של המנהל נחתה חזק יותר מהראיון עצמו. "
        "מה היה בה שהכי נשאר איתך?"
    )

    assert is_contextless_reentry(reply, user_text=UNRESOLVED) is False


def test_a_long_reply_is_not_a_reentry_even_if_it_ends_generically() -> None:
    """A generic tail is a contract violation, not a contextless re-entry."""
    reply = (
        "בדקתי את המסירה ומצאתי שהתשובה הישנה נדחפה אחרי החדשה. "
        "תיקנתי את זה והבדיקות עוברות. מה הכי חי אצלך עכשיו?"
    )

    assert is_contextless_reentry(reply, user_text=UNRESOLVED) is False


def test_a_statement_is_never_a_reentry() -> None:
    assert is_contextless_reentry("אני איתך בזה.", user_text=UNRESOLVED) is False


def test_an_empty_reply_is_not_a_reentry() -> None:
    assert is_contextless_reentry("", user_text=UNRESOLVED) is False


def test_with_no_prior_material_a_generic_opener_is_allowed() -> None:
    """Opening a fresh conversation broadly is fine; re-entering one is not."""
    assert is_contextless_reentry("מה הכי חי אצלך עכשיו?", user_text="") is False


def test_a_crisis_clarification_is_never_treated_as_a_generic_reentry() -> None:
    safety = "אתה בטוח שאתה בטוח עכשיו, או שיש סכנה שתפעל על זה?"

    assert is_contextless_reentry(safety, user_text="אני לא רוצה לחיות") is False
