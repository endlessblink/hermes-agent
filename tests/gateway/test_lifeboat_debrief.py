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
BROAD_REQUESTS = (
    "אני רוצה לעשות דיבריף על אירועים מהתקופה האחרונה",
    "בוא נעבור על מה שקרה לי בזמן האחרון",
    "אני רוצה להסתכל על כמה דברים שקרו בשבועות האחרונים",
    "let's debrief the recent events",
)


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


# --- an interviewer picks the thread ----------------------------------------
#
# 2026-08-23 19:53, after being told to speak plainly, it asked "כשכתבת קודם
# 'הרבה מחשבות, תחושות ודברים' — מה היה הדבר הראשון שהיה לך בראש?". Noam:
# "ועכשיו שוב אתה לא מראיין אותי אלא מפיל עליי את האחריות". Making him select
# what to talk about is the open dump wearing a question mark.

@pytest.mark.parametrize(
    "reply",
    [
        "מה היה הדבר הראשון שהיה לך בראש?",
        "במה תרצה להתחיל?",
        "מה הכי חשוב לך לדבר עליו עכשיו?",
        "איפה נתחיל?",
        "what would you like to start with?",
        "what's the first thing that comes to mind?",
    ],
)
def test_making_him_choose_the_subject_is_rejected(reply: str) -> None:
    assert "handed_back_the_steering" in debrief_problems(reply, known_text=KNOWN)


@pytest.mark.parametrize(
    "reply",
    [
        "אמרת שהיא קראה לך מעיק. איפה זה עומד עכשיו?",
        "יש משהו בצד המשפחתי?",
        "לא ישנת טוב. זה נמשך גם אתמול?",
    ],
)
def test_a_question_that_names_its_own_subject_is_accepted(reply: str) -> None:
    assert debrief_problems(reply, known_text=KNOWN) == ()


@pytest.mark.parametrize("request_text", BROAD_REQUESTS)
@pytest.mark.parametrize(
    "reply",
    [
        "מה היה האירוע הראשון בתקופה הזאת?",
        "נתחיל מהאירוע האחרון מבין אלה שאתה רוצה לכלול בדיבריף. מה קרה בו?",
        "איזה אירוע תרצה להביא קודם?",
        "What would you like to cover first from the recent period?",
    ],
)
def test_broad_debrief_must_not_make_him_choose_or_supply_the_plan(reply: str, request_text: str) -> None:
    problems = debrief_problems(reply, request=request_text)

    assert "assistant_did_not_advance" in problems or "handed_back_the_steering" in problems


@pytest.mark.parametrize("request_text", BROAD_REQUESTS)
def test_broad_debrief_accepts_a_read_offered_for_correction(request_text: str) -> None:
    reply = "נשמע שאתה רוצה להסתכל על כמה אירועים מהתקופה האחרונה כמכלול, בלי לבחור אחד מראש. זה הכיוון?"

    assert debrief_problems(
        reply,
        request=request_text,
    ) == ()


@pytest.mark.parametrize("request_text", BROAD_REQUESTS)
def test_broad_debrief_accepts_an_assistant_chosen_scope(request_text: str) -> None:
    reply = "נתחיל באירוע האחרון בתקופה הזאת, ונישאר קודם עם מה שקרה בפועל. מה היה שם?"

    assert debrief_problems(
        reply,
        request=request_text,
    ) == ()


# --- no preamble about the conversation itself ------------------------------
#
# 2026-08-23 19:54: "נכון. ביקשת שאוביל ראיון, ואני ביקשתי ממך לבחור מאיפה
# להתחיל ולסדר בשבילי את החומר." then the actual question. Every correction
# produced a paragraph restating the correction before answering it. Earlier
# the same turn: "צודק, דיברתי אליך כאילו אני מנהל תיק ולא יושב איתך בשיחה."

@pytest.mark.parametrize(
    "reply",
    [
        "נכון. ביקשת שאוביל ראיון, ואני ביקשתי ממך לבחור. מתי דיברתם?",
        "צודק, דיברתי אליך כאילו אני מנהל תיק. מתי דיברתם?",
        "אתה צודק. אז אני מתחיל ספציפית: מתי דיברתם?",
        "You're right, I made you do the choosing. When did she say it?",
    ],
)
def test_restating_the_correction_before_answering_is_rejected(reply: str) -> None:
    assert "self_correction_preamble" in debrief_problems(reply, known_text=KNOWN)


def test_simply_asking_after_a_correction_is_accepted() -> None:
    """The fix for a bad turn is a good turn, not a paragraph about the bad one."""
    assert debrief_problems(
        "אמרת שהיא קראה לך מעיק. מתי היא אמרה את זה?", known_text=KNOWN
    ) == ()


# --- 2026-08-23 20:46, the first debrief after the rewrite ------------------
#
# "כן. תתחיל מהאירוע הראשון כפי שהוא קרה—מי היה שם, מה קרה בפועל, ומה נשאר
# איתך אחריו. מאיפה אתה רוצה להתחיל?" Noam: "this again drops all
# responsibility on me". Two faults: it assigns him the narration with a
# template to fill, then asks him where to start.

@pytest.mark.parametrize(
    "reply",
    [
        "מאיפה אתה רוצה להתחיל?",
        "ממה אתה מעדיף להתחיל?",
        "במה אתה רוצה שנתחיל?",
        "where do you want to start?",
    ],
)
def test_asking_where_he_wants_to_start_is_rejected(reply: str) -> None:
    assert "handed_back_the_steering" in debrief_problems(reply, known_text=KNOWN)


@pytest.mark.parametrize(
    "reply",
    [
        "תתחיל מהאירוע הראשון כפי שהוא קרה—מי היה שם, מה קרה בפועל, "
        "ומה נשאר איתך אחריו. מה קרה?",
        "ספר לי מההתחלה: מה קרה, מה הרגשת, ומה נשאר. נו?",
        "start with the first event: who was there, what happened, "
        "and what stayed with you. so?",
    ],
)
def test_assigning_him_the_narration_with_a_template_is_rejected(reply: str) -> None:
    assert "assigned_him_the_telling" in debrief_problems(reply, known_text=KNOWN)


def test_asking_about_one_thing_is_still_accepted() -> None:
    assert debrief_problems(
        "אמרת שהיא קראה לך מעיק. מתי היא אמרה את זה?", known_text=KNOWN
    ) == ()


# --- it must be able to think out loud --------------------------------------
#
# Noam, 2026-08-23: "its that the bot is throwing responsiblility on me instead
# of trying to understand". Every guard in place made a blank question the only
# legal move. A read he can correct in one word is not a claim about his life;
# it is the bot doing the work and showing it.

@pytest.mark.parametrize(
    "reply",
    [
        "ממה שכתבת קודם זה נשמע שהמשפט שלה נחת כמו הוכחה, לא כמו עלבון. זה מדויק?",
        "אולי מה שמכביד זה לא המשפט עצמו אלא מה שהוא אישר. קרוב?",
        "נדמה לי שזה פגע בדיוק במקום שכבר היה פתוח. טועה?",
        "it sounds like it landed as proof rather than as an insult. is that right?",
    ],
)
def test_a_read_he_can_correct_is_allowed(reply: str) -> None:
    assert debrief_problems(reply, known_text=KNOWN) == ()


@pytest.mark.parametrize(
    "reply",
    [
        "הפגישה עם הבוס שלך הייתה קשה. איך זה נחת?",
        "אתה נמנע מזה כבר שבועות. נכון?",
    ],
)
def test_a_flat_assertion_is_still_rejected(reply: str) -> None:
    """Stating it as settled fact is the old failure; hedging is the difference."""
    assert "unsourced_continuity" in debrief_problems(reply, known_text=KNOWN)


# --- the guidance actually reaches the turn ---------------------------------
#
# Built and never connected, twice over. 2026-08-23 20:54, asked to restart the
# debrief, it re-entered one thread from the previous session instead of
# opening the period he asked about.

def test_a_broad_debrief_request_is_not_one_subject() -> None:
    assert is_debrief_request("בוא נתחיל שוב את הדיבריף בבקשה") is True
    assert is_debrief_request("אני רוצה לעשות דיבריף על אירועים מהתקופה האחרונה") is True


@pytest.mark.parametrize(
    "reply",
    [
        "חוזרים לספיד-דייט ולמחשבה ש„לעולם לא תהיה הדדיות”. נתחיל משם?",
        "נמשיך מאיפה שעצרנו עם ההודעה שלה. טוב?",
    ],
)
def test_reopening_one_prior_thread_on_a_fresh_debrief_is_rejected(reply: str) -> None:
    problems = debrief_problems(
        reply, known_text=KNOWN, request="בוא נתחיל שוב את הדיבריף"
    )

    assert "narrowed_a_broad_debrief" in problems


def test_broad_debrief_does_not_assert_an_old_named_topic() -> None:
    problems = debrief_problems(
        "נראה לי שהשיבארי ומרי תפסו בתקופה הזאת יותר מדי מקום. זה מדויק או לא?",
        known_text=(
            "אני רוצה לעשות דיבריף על אירועים מהתקופה האחרונה. "
            "אחרי השיבארי דיברתי עם מרי וקיבלתי שני מספרים."
        ),
        request="אני רוצה לעשות דיבריף על אירועים מהתקופה האחרונה",
    )

    assert "selected_stale_broad_debrief_topic" in problems


def test_a_narrow_request_may_go_straight_to_that_thread() -> None:
    problems = debrief_problems(
        "נשמע שזה עדיין פתוח מאז. זה מדויק?",
        known_text=KNOWN,
        request="בוא נדבר על מה שהיא אמרה",
    )

    assert problems == ()


def test_the_gateway_injects_the_debrief_guidance(tmp_path) -> None:
    from gateway.lifeboat_followups import prepare_lifeboat_inbound_guidance

    guidance = prepare_lifeboat_inbound_guidance(
        tmp_path, "sess", "בוא נתחיל שוב את הדיבריף בבקשה"
    )

    assert "DEBRIEF SHAPE" in guidance
