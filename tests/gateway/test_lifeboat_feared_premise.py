"""BUG-25: a feared reading must stay a feared reading.

When someone says they are afraid they are valued only for what they provide,
the reply must not hand that fear back as established fact about the other
person. The other person's motives remain unknown, and "I have not received
signs of care" is not the same claim as "there is no care".

Each test names the acceptance clause it covers, so the criterion and the
regression stay connected.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_rewrite import review_verdict


FEAR = "אני חושש שהיא מעריכה אותי רק בגלל מה שאני נותן לה"
HOPELESS = "אני מרגיש שאין לי ערך מעבר למה שאני מספק"


@pytest.mark.parametrize(
    "draft",
    [
        # acceptance: reject promoting "maybe/I fear" into "when/because/if"
        "כשהיא כבר לא תצטרך אותך, היא לא תראה בך ערך.",
        "כש היא לא תצטרך אותך יותר, זה ייגמר.",
        "ברגע שלא תהיה לה תועלת ממך, הקשר ייגמר.",
        "היא מעריכה אותך רק בגלל התועלת שאתה מביא.",
        "אם היא כבר לא צריכה אותך, אין סיבה שתישאר.",
        "because she no longer needs you, there is nothing left.",
    ],
)
def test_a_fear_promoted_into_a_premise_is_rejected(draft: str) -> None:
    verdict = review_verdict(FEAR, draft)

    assert verdict.accepted is False, f"accepted a feared premise: {draft}"


@pytest.mark.parametrize(
    "draft",
    [
        "זה פחד, לא עובדה על מה שהיא מרגישה. מה גרם לו לעלות דווקא עכשיו?",
        "אין לי דרך לדעת מה היא מרגישה. מה שכן תיארת זה שלא קיבלת סימנים של הערכה.",
        "מה שאתה מתאר הוא חשש. מה קרה היום שהחזיק אותו?",
    ],
)
def test_the_same_content_kept_as_a_fear_is_accepted(draft: str) -> None:
    assert review_verdict(FEAR, draft).accepted is True


def test_asking_the_user_to_justify_their_worth_is_rejected() -> None:
    """acceptance: reject questions asking what value/reason remains for connection."""
    draft = "אז איזה ערך נשאר לה להישאר איתך?"

    assert review_verdict(HOPELESS, draft).accepted is False


def test_missing_care_is_not_proof_that_care_is_absent() -> None:
    """acceptance: distinguish missing communicated care from proof of absence."""
    draft = "אם לא קיבלת ממנה אכפתיות, סימן שאין לה אכפתיות כלפיך."

    assert review_verdict(FEAR, draft).accepted is False


def test_naming_what_is_missing_without_concluding_is_accepted() -> None:
    draft = "לא קיבלת ממנה סימנים של אכפתיות היום. זה חסר אמיתי, והוא לא אומר מה היא מרגישה."

    assert review_verdict(FEAR, draft).accepted is True


def test_a_neutral_reply_to_an_unrelated_turn_is_unaffected() -> None:
    """The rule must not fire on conversations that have nothing to do with it."""
    draft = "מה קרה בפגישה עצמה?"

    assert review_verdict("היה לי יום ארוך בעבודה", draft).accepted is True
