"""A reply may not ask him to narrate his own interior with nothing on the table.

Found in a real Telegram exchange, 2026-08-24 00:24. He wrote: "after the speed
dating I decided not to contact anyone even though they gave me two numbers."
The bot answered: "you got two numbers and still decided not to contact them --
what happened in you between getting the numbers and that decision?"

Every existing check passed it. Every fact came from him, nothing was invented,
it advanced the conversation, the language was ordinary. It was perfectly
grounded and still failed, because groundedness cannot see a handback. His
words for it: "this is still faulty", "the last sentence is like a therapist
again", "written like a person talking from afar".

That is the move rooted out here. His interior is what the assistant is meant to
guess at. Asking about the world is fine; asking him to report what went on
inside him, with no guess of the assistant's own offered, is not.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_editor import asks_him_to_narrate_his_interior, unsafe_draft_reason


HIS_MESSAGE = "אחרי הספידייט החלטתי לא לפנות לאף אחת למרות שנתנו לי שני מספרים"

#: The delivered reply, and its family.
INTERROGATIONS = [
    "קיבלת שני מספרים, ובכל זאת החלטת לא לפנות—מה קרה אצלך בין קבלת המספרים להחלטה הזאת?",
    "מה עבר לך בראש כשקיבלת את המספרים?",
    "איך זה הרגיש לך אחר כך?",
    "מה זה עשה לך?",
    "מה הכי נוכח אצלך עכשיו?",
    "what happened in you between those two moments?",
]

#: The same curiosity, after the assistant has committed to a guess. Allowed:
#: it is checking its own thinking, not outsourcing it.
READS_THEN_ASKS = [
    "נשמע שהמספרים דווקא הבהירו לך שזה לא מה שאתה מחפש, ולא שלא היה לך אומץ. קרוב?",
    "נדמה לי שההחלטה הזאת נפלה עוד לפני הספידייט, ומה שקרה שם רק אישר אותה — "
    "אם אני צודק, מה הרגשת כשקיבלת בכל זאת את המספרים? זה מדויק?",
]

#: Questions about the world. These must stay legal -- banning them would leave
#: the bot with nothing to ask, which is how the blank questions started.
ABOUT_THE_WORLD = [
    "מה אמרו לך כשנתנו את המספרים?",
    "מה עשית אחרי שיצאת משם?",
    "כמה זמן עבר עד שהחלטת?",
]


@pytest.mark.parametrize("reply", INTERROGATIONS)
def test_asking_him_to_report_his_interior_is_caught(reply: str) -> None:
    assert asks_him_to_narrate_his_interior(reply) is True
    assert unsafe_draft_reason(reply, HIS_MESSAGE) == "interior_interrogation"


@pytest.mark.parametrize("reply", READS_THEN_ASKS)
def test_the_same_question_is_fine_once_a_guess_is_offered(reply: str) -> None:
    assert asks_him_to_narrate_his_interior(reply) is False


@pytest.mark.parametrize("reply", ABOUT_THE_WORLD)
def test_questions_about_what_happened_stay_legal(reply: str) -> None:
    assert asks_him_to_narrate_his_interior(reply) is False
    assert unsafe_draft_reason(reply, HIS_MESSAGE) == ""


def test_the_exact_delivered_reply_would_now_be_revised() -> None:
    """The regression, named so it cannot return quietly."""
    delivered = (
        "קיבלת שני מספרים, ובכל זאת החלטת לא לפנות—מה קרה אצלך בין קבלת המספרים "
        "להחלטה הזאת?"
    )

    assert unsafe_draft_reason(delivered, HIS_MESSAGE) == "interior_interrogation"
