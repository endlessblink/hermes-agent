"""TASK-10: reject a bad draft, then ask the model for a better one.

An independent reviewer already existed but was never wired in, and when it
rejected a reply it substituted a sentence of its own -- including a stock
coaching question, the exact thing deleted under BUG-6. Its judgement is worth
keeping; its replacements are not.

So the reviewer here returns a verdict only. A rejected draft goes back to the
model with the specific problem named. If the rewrite is unavailable or fails
its own review, the model's own words are delivered and the failure is
recorded: never a sentence this code invented, and never silently.
"""

from __future__ import annotations

import logging

import pytest

from gateway.lifeboat_rewrite import (
    LifeBoatVerdict,
    build_rewrite_messages,
    resolve_reply,
    review_verdict,
)


LOOP_USER = "נפגשתי איתה, התבאסתי, ואז נכנסתי ללופ של ביקורת עצמית על זה שאני כישלון"
GOOD_REPLY = "איזה חלק במה שהיא אמרה גרם למחשבה על עצמך לקפוץ לשם?"
CLOSING_REPLY = "תודה על השיתוף, נעצור כאן."
HANDOFF_REPLY = "תמשיך משם איך שזה יוצא."


# --- the verdict carries judgement, never replacement prose ----------------

def test_a_good_reply_is_accepted() -> None:
    verdict = review_verdict(LOOP_USER, GOOD_REPLY)

    assert verdict.accepted is True


def test_a_premature_closure_is_rejected() -> None:
    verdict = review_verdict("היא עברה לברלין ואני מרגיש עצב וכבדות על מה שלא קרה", CLOSING_REPLY)

    assert verdict.accepted is False
    assert verdict.reason


def test_a_responsibility_handoff_is_rejected() -> None:
    verdict = review_verdict(
        "התוצאה בדייטים הפעילה בדידות ישנה ואני מרגיש ריק וחסר תקווה", HANDOFF_REPLY
    )

    assert verdict.accepted is False


def test_a_verdict_never_carries_a_replacement_reply() -> None:
    """The whole point: judgement only, no prose of its own."""
    verdict = review_verdict("היא עברה לברלין ואני מרגיש עצב וכבדות", CLOSING_REPLY)

    assert not hasattr(verdict, "response")
    assert "מה הכי חי" not in repr(verdict)


def test_a_verdict_is_a_plain_result_object() -> None:
    assert isinstance(review_verdict(LOOP_USER, GOOD_REPLY), LifeBoatVerdict)


def test_the_receipt_carries_no_conversation_text() -> None:
    verdict = review_verdict(LOOP_USER, CLOSING_REPLY)

    assert LOOP_USER not in verdict.receipt
    assert CLOSING_REPLY not in verdict.receipt


# --- the rewrite request ---------------------------------------------------

def test_the_rewrite_names_the_specific_problem() -> None:
    messages = build_rewrite_messages(LOOP_USER, CLOSING_REPLY, "premature_closure")

    joined = " ".join(m["content"] for m in messages)
    assert "premature_closure" in joined


def test_the_rewrite_carries_the_draft_and_the_user_turn() -> None:
    messages = build_rewrite_messages(LOOP_USER, CLOSING_REPLY, "premature_closure")

    joined = " ".join(m["content"] for m in messages)
    assert CLOSING_REPLY in joined
    assert LOOP_USER in joined


def test_the_rewrite_never_suggests_wording_to_use() -> None:
    """Handing the model a sentence is how templates come back."""
    messages = build_rewrite_messages(LOOP_USER, CLOSING_REPLY, "premature_closure")

    joined = " ".join(m["content"] for m in messages)
    for canned in ("מה הכי חי אצלך", "רוצה שנחשוב על צעד אחד קטן", "אני נשאר עם"):
        assert canned not in joined


# --- the delivery decision -------------------------------------------------

def test_an_accepted_draft_is_delivered_untouched() -> None:
    delivered, reason = resolve_reply(LOOP_USER, GOOD_REPLY, rewrite=lambda *a, **k: "unused")

    assert delivered == GOOD_REPLY
    assert reason == "accepted"


def test_an_accepted_but_temporally_unsupported_draft_is_repaired() -> None:
    calls = []

    delivered, reason = resolve_reply(
        "אני רוצה לעשות דיבריף על הימים האחרונים.",
        "נתחיל מאתמול בבוקר: מה קרה?",
        rewrite=lambda *a, **k: "ניקח רגע אחד מהימים האחרונים: מה עולה?",
        edit=lambda messages: calls.append(messages) or "ניקח רגע אחד מהימים האחרונים: מה עולה?",
    )

    assert calls
    assert delivered == "ניקח רגע אחד מהימים האחרונים: מה עולה?"
    assert reason == "edited"


def test_a_rejected_draft_is_replaced_by_the_models_rewrite() -> None:
    delivered, reason = resolve_reply(
        LOOP_USER, CLOSING_REPLY, rewrite=lambda *a, **k: GOOD_REPLY
    )

    assert delivered == GOOD_REPLY
    assert reason == "rewritten"


def test_a_failed_rewrite_fails_closed_and_never_invents_fallback() -> None:
    second_draft = "מספיק להיום, סיימנו."

    delivered, reason = resolve_reply(
        LOOP_USER, CLOSING_REPLY, rewrite=lambda *a, **k: second_draft
    )

    assert delivered == CLOSING_REPLY
    assert reason == "rewrite_rejected_fallback"


def test_an_unavailable_rewrite_fails_closed() -> None:
    def unavailable(*args, **kwargs):
        raise RuntimeError("no auxiliary provider configured")

    delivered, reason = resolve_reply(LOOP_USER, CLOSING_REPLY, rewrite=unavailable)

    assert delivered == CLOSING_REPLY
    assert reason == "rewrite_unavailable"


def test_an_empty_rewrite_fails_closed() -> None:
    delivered, reason = resolve_reply(LOOP_USER, CLOSING_REPLY, rewrite=lambda *a, **k: "   ")

    assert delivered == CLOSING_REPLY
    assert reason == "rewrite_unavailable"


def test_a_rejected_draft_is_never_delivered_silently(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="gateway.lifeboat_rewrite"):
        resolve_reply(LOOP_USER, CLOSING_REPLY, rewrite=lambda *a, **k: "מספיק להיום, סיימנו.")

    assert "rewrite_rejected" in caplog.text
    assert CLOSING_REPLY not in caplog.text


def test_at_most_two_rewrites_are_attempted_and_second_can_pass() -> None:
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return "מספיק להיום, סיימנו." if len(calls) == 1 else GOOD_REPLY

    delivered, reason = resolve_reply(LOOP_USER, CLOSING_REPLY, rewrite=counting)

    assert len(calls) == 2
    assert delivered == GOOD_REPLY
    assert reason == "rewritten"


def test_failed_editor_repairs_do_not_fall_through_to_a_third_writer() -> None:
    editor_calls = []
    rewrite_calls = []

    def editor(messages):
        editor_calls.append(1)
        return "מספיק להיום, סיימנו."

    def rewrite(messages):
        rewrite_calls.append(1)
        return GOOD_REPLY

    delivered, reason = resolve_reply(
        LOOP_USER,
        CLOSING_REPLY,
        rewrite=rewrite,
        edit=editor,
    )

    assert delivered == CLOSING_REPLY
    assert reason == "editor_rejected_fallback"
    assert len(editor_calls) == 2
    assert rewrite_calls == []


def test_an_empty_draft_is_left_alone() -> None:
    delivered, reason = resolve_reply(LOOP_USER, "", rewrite=lambda *a, **k: GOOD_REPLY)

    assert delivered == ""
    assert reason == "accepted"


def test_semantic_shadow_is_observational_only() -> None:
    seen = []

    def semantic_checker(messages):
        seen.extend(messages)
        return (
            '{"pass":false,"repeated_request":true,"invented_user_goal":false,'
            '"responsibility_handoff":false,"concrete_continuation":false,'
            '"evidence_turn_ids":["u1"],"reason":"repeat"}'
        )

    draft = "נמשיך ממה שסיפרת."
    delivered, outcome = resolve_reply(
        "כן",
        draft,
        rewrite=lambda *a, **k: "unused",
        semantic_checker=semantic_checker,
        recent_turns=[{"id": "u1", "role": "user", "content": "כבר סיפרתי"}],
        trusted_state="u1: explicit user evidence",
    )

    assert delivered == draft
    assert outcome == "accepted"
    assert len(seen) == 2
    assert "u1: explicit user evidence" in seen[1]["content"]


@pytest.mark.parametrize("ack", ["בסדר", "👍"])
@pytest.mark.parametrize("prior", ["מה קרה אחרי זה?", "👍"])
def test_bare_ack_after_an_assistant_reply_must_be_repaired(ack: str, prior: str) -> None:
    delivered, outcome = resolve_reply(
        "אוקיי",
        ack,
        rewrite=lambda *a, **k: "נמשיך עם מה שסיפרת קודם: מה קרה אחרי שקיבלת את המספרים?",
        edit=lambda *a, **k: "נמשיך עם מה שסיפרת קודם: מה קרה אחרי שקיבלת את המספרים?",
        semantic_enforce=True,
        recent_turns=[
            {"id": "a1", "role": "assistant", "content": prior},
        ],
    )

    assert delivered != ack
    assert outcome == "edited"
