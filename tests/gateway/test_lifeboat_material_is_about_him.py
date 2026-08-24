"""Material handed to a turn must be his life, not his instructions to the bot.

Found by reading a live replay on 2026-08-23, not by a test. The bot opened a
debrief with "the last two days were much better than the ones before" -- a
confident, specific, false claim about his week. Its source was a line he had
written to praise two bot replies during development. It had been collected as
"what he said recently" and handed to the editing agent as material about him.

The work-talk filter could not have caught it: there is no bug, deploy, or code
word in the sentence. Lengthening that keyword list would not have helped, and
lengthening keyword lists is the failure mode of this whole subsystem. The rule
tested here is structural instead -- a sentence whose subject is the assistant
is not evidence about him.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_checkin_context import is_addressed_to_the_assistant, is_operational


#: The exact lines the replay found in his material, with what each one is.
CONTAMINATION = [
    "השניים האחרונים הרבה יותר טובים! בבקשה תשמור ותדאג תמיד להשתמש במה שלמדת עכשיו",
    "after you succsesfuly impletmnt this - I want this to be exposed to telegram agents "
    "like the orchestrator from telegram",
    "Hey, tell me about yourself!",
    "דבר אליי בבקשה יותר באופן חברי ופחות כמו מטפל מרוחק שמשתמש במושגים",
    "ועכשיו שוב אתה לא מראיין אותי אלא מפיל עליי את האחראיות",
    "אני מעדיף שתראיין אותי בטווח של מה שכבר דיברנו עליו",
    "בוא נתחיל שוב את הדיבריף בבקשה",
]

#: Real life material, which must survive. If the filter eats these the bot is
#: left with an empty hand for the wrong reason, and an empty hand is what
#: produced the blank questions in the first place.
HIS_LIFE = [
    "היה לי יום ממש קשה היום",
    "נפגשתי איתה אתמול והתבאסתי",
    "כמעט לא יצאתי מהבית השבוע",
    "אני מרגיש שאני מאכזב את כולם",
    "לא הצלחתי להירדם אחרי מה שהיא אמרה",
    "יש לי ראיון בשבוע הבא ואני בלחץ",
]


@pytest.mark.parametrize("line", CONTAMINATION)
def test_talk_addressed_to_the_bot_is_not_material_about_him(line: str) -> None:
    assert is_addressed_to_the_assistant(line) is True
    assert is_operational(line) is True


@pytest.mark.parametrize("line", HIS_LIFE)
def test_his_own_life_still_gets_through(line: str) -> None:
    assert is_addressed_to_the_assistant(line) is False
    assert is_operational(line) is False


def test_the_praise_that_became_a_false_week(pytestconfig) -> None:
    """The specific regression, named so it cannot come back unnoticed."""
    praise = "השניים האחרונים הרבה יותר טובים! בבקשה תשמור ותדאג תמיד להשתמש במה שלמדת עכשיו"

    assert is_operational(praise) is True


def test_an_empty_line_is_not_classified_as_anything() -> None:
    assert is_addressed_to_the_assistant("") is False
    assert is_addressed_to_the_assistant(None) is False


# --- text this system wrote, filed as text he wrote ------------------------

from gateway.lifeboat_turn_context import is_engine_block  # noqa: E402

ENGINE_BLOCKS = [
    "# Suggestion discipline Local time: Saturday 2026-08-22 22:20. Suggestions voiced today: 0/2.",
    "## Context",
    "Local time: Sunday 2026-08-23 21:40",
    "[IMPORTANT] background process finished",
]


@pytest.mark.parametrize("line", ENGINE_BLOCKS)
def test_engine_text_is_not_treated_as_something_he_said(line: str) -> None:
    assert is_engine_block(line) is True


@pytest.mark.parametrize("line", HIS_LIFE)
def test_his_messages_are_not_mistaken_for_engine_text(line: str) -> None:
    assert is_engine_block(line) is False


def test_a_hash_inside_a_sentence_is_still_his() -> None:
    assert is_engine_block("כתבתי לו #פוסט על זה") is False


# --- the log that could not be sieved --------------------------------------

def test_the_shared_general_purpose_log_is_no_longer_read() -> None:
    """Not a preference: two filters were added and each revealed a new layer.

    That log is where he works. Separating a man's life from his work by
    vocabulary, when both were typed into the same box, does not converge -- and
    every pass at it ended with a confident false statement about his week.
    """
    from gateway import lifeboat_turn_context

    assert lifeboat_turn_context.LEGACY_DIR is None


def test_material_can_come_back_empty_without_erroring(tmp_path) -> None:
    """An empty hand is a legitimate answer, and must not break the turn."""
    from gateway.lifeboat_turn_context import build_turn_context

    material = build_turn_context(
        transcript_dir=tmp_path, queue_text="", journal_entries=[]
    )

    assert material == ""


# --- when he asks about a period, the material is the subject --------------

def test_a_debrief_request_makes_the_material_the_subject() -> None:
    """He asked to debrief two days; it opened "what happened yesterday morning?"

    It held ten lines including the whole conversation about his speed dating and
    ignored all of it, because the heading told it to use material only when his
    new message connects to it. His request named a period, not an event, so
    nothing connected and it asked a cold question about days it already knew
    something about. Guarding against invention had become ignoring what it knows.
    """
    from gateway.lifeboat_turn_context import build_turn_context

    material = build_turn_context(
        about_a_period=True,
        transcript_dir=None,
        queue_text="",
        journal_entries=["אתמול הלכתי לספידייט"],
    )

    assert "HE HAS ASKED ABOUT THIS PERIOD" in material
    assert "Begin from something in here" in material


def test_broad_debrief_does_not_select_an_unrelated_old_event() -> None:
    """A period-wide request must not turn the loudest old topic into its subject."""
    from gateway.lifeboat_turn_context import build_turn_context

    material = build_turn_context(
        about_a_period=True,
        request_text="אני רוצה לעשות דיבריף על אירועים מהתקופה האחרונה",
        transcript_dir=None,
        queue_text="- id: old\n  status: active\n  added: 2026-08-20\n  topic: שיבארי\n  next_point: החלטה",
        journal_entries=[],
    )

    assert "PERIOD-WIDE DEBRIEF" in material
    assert "שיבארי" not in material
    assert "old event" in material


def test_specific_debrief_can_still_receive_material() -> None:
    """The boundary is relevance, not a blanket loss of continuity."""
    from gateway.lifeboat_turn_context import build_turn_context

    material = build_turn_context(
        about_a_period=True,
        request_text="אני רוצה לעשות דיבריף על הספידייט",
        transcript_dir=None,
        queue_text="- id: date\n  status: active\n  added: 2026-08-20\n  topic: הספידייט\n  next_point: שני מספרים",
        journal_entries=[],
    )

    assert "ספידייט" in material
    assert "PERIOD-WIDE DEBRIEF" not in material


def test_an_ordinary_turn_keeps_the_material_as_background() -> None:
    """Outside a period question, older fragments must not be dragged in."""
    from gateway.lifeboat_turn_context import build_turn_context

    material = build_turn_context(
        transcript_dir=None, queue_text="", journal_entries=["אתמול הלכתי לספידייט"]
    )

    assert "optional historical context" in material
    assert "HE HAS ASKED ABOUT THIS PERIOD" not in material


def test_neither_heading_invites_invention() -> None:
    from gateway.lifeboat_turn_context import build_turn_context

    for about_a_period in (True, False):
        material = build_turn_context(
            about_a_period=about_a_period,
            transcript_dir=None,
            queue_text="",
            journal_entries=["אתמול הלכתי לספידייט"],
        )
        assert "not here" in material or "not a current account" in material
