"""Judge the bot by what it actually said, not by what the code should do.

Four times in one session I reported a fix as verified because the tests passed
and the build was live, and each time the delivered message was still wrong.
Every miss lived in the gap between "the component is correct" and "the message
was good" — a gap no unit test crosses.

The transcripts were available the whole time. These checks read them, so
"verified" can mean the delivered text was inspected rather than inferred.

Fixtures are synthetic; the real log is only read by the script.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_behaviour import (
    behaviour_problems,
    parse_turns,
)


LOG = """# Hermes Turn Log — life-advisor — 2026-08-23

## 2026-08-23T09:00:00 — session `cron_abc_1` — platform `cron`

### Assistant
בוקר טוב נועם. אני כאן, והדלת פתוחה.

מה שלומך הבוקר?

---

## 2026-08-23T12:00:00 — session `agent:life-advisor` — platform `telegram`

### User
משהו קרה היום

### Assistant
מה קרה עם המנהל אחרי הפגישה?

---
"""


def test_turns_are_parsed() -> None:
    assert len(parse_turns(LOG)) == 2


def test_a_turn_knows_its_platform() -> None:
    turns = parse_turns(LOG)

    assert turns[0].platform == "cron"
    assert turns[1].platform == "telegram"


def test_the_assistant_text_is_captured() -> None:
    assert "הדלת פתוחה" in parse_turns(LOG)[0].assistant


# --- the invariants I claimed to have fixed ---------------------------------

def test_a_stock_comfort_line_is_reported() -> None:
    """'I am here, the door is open' — banned by both the prompt and the skill."""
    problems = behaviour_problems(parse_turns(LOG))

    assert any("stock comfort" in p for p in problems)


def test_a_bare_generic_checkin_is_reported() -> None:
    problems = behaviour_problems(parse_turns(LOG))

    assert any("generic" in p for p in problems)


def test_a_grounded_reply_is_not_reported() -> None:
    turns = [t for t in parse_turns(LOG) if t.platform == "telegram"]

    assert behaviour_problems(turns) == ()


def test_a_banned_reentry_sentence_is_reported() -> None:
    log = LOG.replace("מה קרה עם המנהל אחרי הפגישה?", "מה הכי חי אצלך עכשיו, אם בכלל?")

    assert any("re-entry" in p for p in behaviour_problems(parse_turns(log)))


def test_an_engine_notice_is_reported() -> None:
    log = LOG.replace("מה קרה עם המנהל אחרי הפגישה?", "⚡ Interrupting current task. now")

    assert any("engine notice" in p for p in behaviour_problems(parse_turns(log)))


def test_a_repeated_reply_is_reported() -> None:
    log = LOG.replace(
        "מה קרה עם המנהל אחרי הפגישה?",
        "בוקר טוב נועם. אני כאן, והדלת פתוחה.\n\nמה שלומך הבוקר?",
    )

    assert any("repeat" in p for p in behaviour_problems(parse_turns(log)))


def test_a_clean_log_reports_nothing() -> None:
    clean = """## 2026-08-23T09:00:00 — session `cron_x` — platform `cron`

### Assistant
בוקר טוב. חשבתי על הראיון שסיפרת עליו — מה קרה שם בסוף?

---
"""

    assert behaviour_problems(parse_turns(clean)) == ()


def test_an_empty_log_reports_nothing() -> None:
    assert behaviour_problems(parse_turns("")) == ()


def test_every_problem_names_its_turn() -> None:
    for problem in behaviour_problems(parse_turns(LOG)):
        assert "2026-08-23T" in problem
