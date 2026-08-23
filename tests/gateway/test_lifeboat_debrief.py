"""A debrief that leads, without inventing what it does not know.

The open dump was a correct instruction that cost Noam more than a short
interview would. So the debrief leads instead: one question at a time, anchored
where it can be, and free to walk into ground it knows nothing about.

The limit is on claims, not subjects. Anything stated as already true about him
must come from something he said or wrote — that is the fabrication the
2026-08-12 invented quote taught. A question into unknown territory asserts
nothing and is allowed, provided it does not smuggle the assumption in.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_debrief import (
    DebriefState,
    build_debrief_guidance,
    debrief_problems,
    is_debrief_request,
    load_life_areas,
    next_area,
    wants_open_dump,
)


KNOWN = "היא אמרה לי שאני מעיק ואני לא מפסיק לחשוב על זה. גם לא ישנתי טוב."


# --- entering and leaving ---------------------------------------------------

@pytest.mark.parametrize(
    "text",
    ["בוא נעשה דיבריף", "אני רוצה דיבריף על היום", "/debrief", "let's do a debrief"],
)
def test_a_debrief_is_recognised(text: str) -> None:
    assert is_debrief_request(text) is True


@pytest.mark.parametrize("text", ["מה שלומך?", "תסביר לי מה קרה בקוד", ""])
def test_ordinary_messages_are_not_debriefs(text: str) -> None:
    assert is_debrief_request(text) is False


@pytest.mark.parametrize(
    "text",
    ["אני רוצה פשוט לשפוך הכל", "תן לי לדבר בלי שאלות", "let me just dump it all"],
)
def test_he_can_take_the_lead_back(text: str) -> None:
    assert wants_open_dump(text) is True


def test_asking_a_question_is_not_asking_to_dump() -> None:
    assert wants_open_dump("מה הדרך הכי נכונה לעשות דיבריף?") is False


# --- one question, and it must ask something --------------------------------

def test_two_questions_in_one_turn_are_rejected() -> None:
    reply = "איפה זה עמד כשעצרת? ומה הרגשת אז?"

    assert "too_many_questions" in debrief_problems(reply, known_text=KNOWN)


def test_a_turn_that_asks_nothing_is_rejected() -> None:
    reply = "אני שומע שזה היה יום כבד."

    assert "no_question" in debrief_problems(reply, known_text=KNOWN)


# --- claims must be sourced; questions need not be --------------------------

def test_stating_something_he_never_said_is_rejected() -> None:
    reply = "הפגישה עם הבוס שלך הייתה קשה. איך זה נחת אצלך?"

    assert "unsourced_continuity" in debrief_problems(reply, known_text=KNOWN)


def test_restating_what_he_did_say_is_fine() -> None:
    reply = "אמרת שהיא קראה לך מעיק. איפה זה עומד עכשיו?"

    assert debrief_problems(reply, known_text=KNOWN) == ()


def test_an_open_question_into_unknown_ground_is_allowed() -> None:
    """It knows nothing about his family. Asking is not a claim."""
    reply = "יש משהו בצד המשפחתי שלא נגענו בו?"

    assert debrief_problems(reply, known_text=KNOWN) == ()


@pytest.mark.parametrize(
    "reply",
    [
        "איך היה עם אחותך השבוע?",
        "מה קרה עם הפרויקט החדש?",
        "how did it go with your father?",
    ],
)
def test_a_question_that_smuggles_in_an_assumption_is_rejected(reply: str) -> None:
    assert "presupposed_event" in debrief_problems(reply, known_text=KNOWN)


def test_the_same_shape_is_fine_when_he_did_raise_it() -> None:
    known = KNOWN + " דיברתי עם אחותי אתמול."

    assert debrief_problems("איך היה עם אחותך?", known_text=known) == ()


# --- one area, never a sweep ------------------------------------------------

def test_walking_through_several_areas_at_once_is_rejected() -> None:
    reply = "נעבור על העבודה, הכסף, השינה והמשפחה — במה נתחיל?"
    areas = ("עבודה", "כסף", "שינה", "משפחה")

    assert "area_sweep" in debrief_problems(reply, known_text=KNOWN, areas=areas)


def test_one_area_is_not_a_sweep() -> None:
    areas = ("עבודה", "כסף", "שינה", "משפחה")

    assert debrief_problems(
        "יש משהו בכיוון השינה?", known_text=KNOWN, areas=areas
    ) == ()


# --- choosing where to go next ----------------------------------------------

def test_the_quietest_area_comes_first() -> None:
    areas = ("עבודה", "כסף", "משפחה")
    state = DebriefState(active=True, areas_opened=("עבודה",))

    assert next_area(areas, state, recent_text="דיברנו הרבה על כסף") == "משפחה"


def test_an_area_already_opened_this_debrief_is_not_reopened() -> None:
    areas = ("עבודה",)
    state = DebriefState(active=True, areas_opened=("עבודה",))

    assert next_area(areas, state, recent_text="") is None


# --- the map ----------------------------------------------------------------

def test_the_area_map_falls_back_when_the_vault_is_unreadable() -> None:
    assert len(load_life_areas(None)) >= 4


def test_the_area_map_is_read_from_the_note_when_present() -> None:
    note = "# Life areas\n\n## Areas\n\n- עבודה\n- שינה\n- כסף\n- משפחה\n- בית\n"

    assert load_life_areas(note) == ("עבודה", "שינה", "כסף", "משפחה", "בית")


# --- what the model is told -------------------------------------------------

def test_the_guidance_never_supplies_example_wording() -> None:
    """Handing it a sentence is how the canned opener came back last time."""
    guidance = build_debrief_guidance(anchors=("היא קראה לי מעיק",), area="משפחה")

    assert "היא קראה לי מעיק" in guidance
    assert "משפחה" in guidance
    assert "?" not in guidance.split("ANCHORS")[0]


def test_only_the_areas_section_is_read() -> None:
    """The note explains itself in prose, and that prose uses bullets too."""
    note = (
        "# Life-Boat Life Areas\n\nSome preamble.\n\n"
        "## Areas\n\n- עבודה\n- שינה\n\n"
        "## What the bot may and may not do\n\n"
        "- It may ask about any area here.\n"
        "- It may not state anything as already true.\n"
    )

    assert load_life_areas(note) == ("עבודה", "שינה")


# --- it must not sound like a clinician -------------------------------------
#
# The first real debrief opened with "אני אראיין אותך שאלה אחת בכל פעם, אשמור
# את כל החוטים שכבר פתחנו ואשאיר מקום למה שחדש—בלי שתצטרך לנהל את המבנה", then
# "החוט הפעיל שנשאר הוא...". Noam's words: too much like a therapist, and that
# creates distance. Two separate faults live in that: narrating its own method,
# and the clinical vocabulary itself.

@pytest.mark.parametrize(
    "reply",
    [
        "אני אראיין אותך שאלה אחת בכל פעם. איפה זה עומד?",
        "אשמור את כל החוטים שכבר פתחנו ואשאיר מקום למה שחדש. מה עכשיו?",
        "בלי שתצטרך לנהל את המבנה. מה קורה?",
        "I'll interview you one question at a time. Where does it stand?",
    ],
)
def test_narrating_its_own_method_is_rejected(reply: str) -> None:
    assert "method_narration" in debrief_problems(reply, known_text=KNOWN)


@pytest.mark.parametrize(
    "reply",
    [
        "החוט הפעיל שנשאר הוא מה שאמרת. מה איתו?",
        "אני אחזיק לך מקום לזה. מה עולה?",
        "מה זה מפעיל אצלך?",
        "כדאי לעבד את זה. מה מרגיש?",
    ],
)
def test_clinical_vocabulary_is_rejected(reply: str) -> None:
    assert "therapist_register" in debrief_problems(reply, known_text=KNOWN)


def test_asking_plainly_is_accepted() -> None:
    reply = "אמרת שהיא קראה לך מעיק. איפה זה עומד עכשיו?"

    assert debrief_problems(reply, known_text=KNOWN) == ()


def test_an_ordinary_open_question_stays_accepted() -> None:
    assert debrief_problems("יש משהו בצד המשפחתי?", known_text=KNOWN) == ()
