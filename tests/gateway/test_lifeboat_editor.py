"""The editing agent, and the guards that keep it from being worse than none.

Every test here drives a fake editor. The point is not what a model writes --
that is judged by reading real transcripts -- but that the delivery decision
around it is right: a good draft survives a bad edit, a failed edit never
reaches him as a third attempt, and the one fixed sentence in the system cannot
repeat.
"""

from __future__ import annotations

import json

import pytest

from gateway.lifeboat_editor import (
    build_editor_messages,
    clean_editor_output,
    edit_reply,
)
from gateway.lifeboat_rewrite import resolve_reply


USER = "היא אמרה לי שאני מוגזם ומאז אני לא מצליח להירדם"
BLAND = "כשאתה מסתכל על התקופה האחרונה בכללותה, איך אתה מרגיש שעברת אותה?"
WITH_A_READ = (
    "ממה שכתבת קודם זה נשמע שהמשפט שלה נחת כמו הוכחה, לא כמו עלבון. זה מדויק?"
)
CLOSING = "תודה על השיתוף, נעצור כאן."
MATERIAL = "MATERIAL YOU ALREADY HAVE.\n- 2026-08-21: היא אמרה שאני מוגזם"


# --- what the editor is asked ---------------------------------------------

def test_the_editor_is_given_his_material() -> None:
    messages = build_editor_messages(USER, BLAND, material=MATERIAL)

    joined = " ".join(m["content"] for m in messages)
    assert "היא אמרה שאני מוגזם" in joined
    assert BLAND in joined


def test_the_editor_is_told_plainly_when_there_is_no_material() -> None:
    joined = " ".join(m["content"] for m in build_editor_messages(USER, BLAND))

    assert "MATERIAL: none available" in joined


def test_the_editor_prompt_hands_over_no_sentence_to_reuse() -> None:
    """A supplied example is the template returning through the back door.

    The check used to be "no Hebrew at all in the prompt". That stopped working
    when the identity was rewritten in Hebrew -- which was done deliberately,
    because English instructions could not govern Hebrew register. Hebrew
    instructions are fine; a Hebrew sentence the bot could send him is not, so
    the test now asserts the thing it was really protecting.
    """
    joined = " ".join(m["content"] for m in build_editor_messages(USER, BLAND, material=MATERIAL))
    prompt_only = joined.replace(USER, "").replace(BLAND, "").replace(MATERIAL, "")

    hebrew_lines = [
        line for line in prompt_only.splitlines()
        if any("֐" <= ch <= "׿" for ch in line)
    ]
    for line in hebrew_lines:
        assert "?" not in line, f"a deliverable question reached the prompt: {line!r}"


def test_the_editor_brief_counts_a_chosen_concrete_step_as_progress() -> None:
    joined = " ".join(m["content"] for m in build_editor_messages(USER, BLAND))

    assert "one of two ways" in joined
    assert "concrete next step that you choose" in joined
    assert "make the choice concrete from what he actually wrote" in joined
    assert "Use a time or place only when he supplied it" in joined
    assert "anchor to the words or event he just gave you" in joined
    assert "yesterday evening" not in joined
    assert "choose one and move" in joined
    assert "all count as movement" in joined


def test_the_editor_brief_asks_for_close_everyday_hebrew() -> None:
    joined = " ".join(m["content"] for m in build_editor_messages(USER, BLAND))

    assert "close, attentive person" in joined
    assert "direct, warm, and ordinary" in joined


def test_historical_material_is_not_treated_as_current_evidence() -> None:
    joined = " ".join(
        m["content"]
        for m in build_editor_messages(USER, BLAND, material="- 2026-08-20: old fragment")
    )

    assert "HISTORICAL MATERIAL" in joined
    assert "not necessarily about this turn" in joined
    assert "do not combine isolated fragments" in joined


def test_editor_does_not_introduce_an_unsupported_temporal_anchor() -> None:
    result = edit_reply(
        "כן",
        "אני איתך. נתקדם צעד צעד.",
        edit=lambda _messages: "נתחיל מאתמול בערב: מה קרה שם?",
    )

    assert result.text == "אני איתך. נתקדם צעד צעד."
    assert result.available is True
    assert result.changed is False


def test_historical_temporal_word_does_not_license_a_current_anchor() -> None:
    result = edit_reply(
        "לא יודע",
        "אני איתך. נתקדם צעד צעד.",
        material="HISTORICAL USER TURNS: הבוקר היה עמוס",
        edit=lambda _messages: "נתחיל מהבוקר: מה קרה אחרי שקמת?",
    )

    assert result.text == "אני איתך. נתקדם צעד צעד."
    assert result.changed is False


@pytest.mark.parametrize("anchor", ["היום האחרון", "הרגע האחרון", "מהבוקר", "עד הערב"])
def test_relative_hebrew_time_anchor_requires_current_user_evidence(anchor: str) -> None:
    result = edit_reply(
        "לא יודע",
        "אני איתך. נתקדם צעד צעד.",
        edit=lambda _messages: f"נתחיל מ{anchor}: מה קרה שם?",
    )

    assert result.text == "אני איתך. נתקדם צעד צעד."
    assert result.changed is False


def test_unsupported_temporal_edit_gets_one_targeted_retry() -> None:
    replies = iter([
        "נתחיל מאתמול בערב: מה קרה שם?",
        "נתחיל ממה שקורה עכשיו: מה הדבר הראשון שעולה לך?",
    ])

    result = edit_reply(
        "לא יודע",
        "נתחיל מאתמול: מה קרה?",
        edit=lambda _messages: next(replies),
    )

    assert result.text == "נתחיל ממה שקורה עכשיו: מה הדבר הראשון שעולה לך?"
    assert result.changed is True


def test_therapist_handoff_edit_gets_rejected_and_retried() -> None:
    replies = iter([
        "אני כאן איתך, בקצב שלך.",
        "נתחיל ממה שקורה עכשיו: מה הדבר הראשון שעולה לך?",
    ])

    result = edit_reply(
        "אוקיי",
        "מה הדבר הראשון שעולה לך מהימים האחרונים?",
        edit=lambda _messages: next(replies),
    )

    assert result.text == "נתחיל ממה שקורה עכשיו: מה הדבר הראשון שעולה לך?"
    assert result.changed is True


def test_a_rejection_reason_is_passed_on_when_there_is_one() -> None:
    joined = " ".join(
        m["content"] for m in build_editor_messages(USER, CLOSING, reason="premature_closure")
    )

    assert "premature_closure" in joined


# --- reading what it returns ----------------------------------------------

@pytest.mark.parametrize(
    "raw",
    ["```\n" + WITH_A_READ + "\n```", '"' + WITH_A_READ + '"', "  " + WITH_A_READ + "  "],
)
def test_model_wrappers_are_stripped(raw: str) -> None:
    assert clean_editor_output(raw) == WITH_A_READ


def test_an_editor_that_raises_leaves_the_draft_alone() -> None:
    def broken(_messages):
        raise RuntimeError("no auxiliary provider configured")

    result = edit_reply(USER, BLAND, edit=broken)

    assert result.text == BLAND
    assert result.available is False


def test_an_editor_that_returns_nothing_leaves_the_draft_alone() -> None:
    result = edit_reply(USER, BLAND, edit=lambda _m: "   ")

    assert result.text == BLAND
    assert result.available is False


# --- the delivery decision -------------------------------------------------

def test_a_bland_but_legal_draft_is_preserved_without_a_review_failure() -> None:
    """A passing draft must not be replaced by an unmeasured second writer."""
    delivered, outcome = resolve_reply(
        USER,
        BLAND,
        rewrite=lambda *a, **k: "unused",
        edit=lambda _m: WITH_A_READ,
        material=MATERIAL,
    )

    assert delivered == BLAND
    assert outcome == "accepted"


def test_a_failed_edit_never_replaces_a_draft_that_passed() -> None:
    delivered, outcome = resolve_reply(
        USER,
        WITH_A_READ,
        rewrite=lambda *a, **k: "unused",
        edit=lambda _m: CLOSING,
    )

    assert delivered == WITH_A_READ
    assert outcome == "accepted"


def test_the_editor_does_not_touch_an_accepted_draft() -> None:
    seen = []

    def watching(messages):
        seen.append(messages)
        return WITH_A_READ

    resolve_reply(USER, BLAND, rewrite=lambda *a, **k: "unused", edit=watching)

    assert not seen


def test_editor_can_repair_after_an_unsafe_first_attempt() -> None:
    attempts = []

    def editor(messages):
        attempts.append(1)
        return "נתחיל מאתמול בבוקר: מה קרה?" if len(attempts) == 1 else WITH_A_READ

    from gateway.lifeboat_editor import edit_reply

    result = edit_reply(
        "אני רוצה לעשות דיבריף על הימים האחרונים.",
        "ניקח את מה שסיפרת.",
        edit=editor,
        reason="unsupported_temporal_anchor",
    )

    assert result.available is True
    assert result.changed is True
    assert result.text == WITH_A_READ
    assert len(attempts) == 2


def test_a_rejected_draft_whose_edit_also_fails_preserves_responsive_main_reply() -> None:
    delivered, outcome = resolve_reply(
        USER,
        CLOSING,
        rewrite=lambda *a, **k: WITH_A_READ,
        edit=lambda _m: "תודה על השיתוף, נעצור כאן.",
    )

    assert delivered == CLOSING
    assert outcome == "editor_rejected_fallback"


# --- the kill switch -------------------------------------------------------

def test_the_editor_is_on_unless_the_flag_exists(tmp_path, monkeypatch) -> None:
    from gateway import lifeboat_editor

    monkeypatch.setattr(lifeboat_editor, "DISABLE_FLAG", tmp_path / "lifeboat-editor-off")

    assert lifeboat_editor.editor_enabled() is True


def test_touching_the_flag_turns_the_editor_off(tmp_path, monkeypatch) -> None:
    from gateway import lifeboat_editor

    flag = tmp_path / "lifeboat-editor-off"
    flag.touch()
    monkeypatch.setattr(lifeboat_editor, "DISABLE_FLAG", flag)

    assert lifeboat_editor.editor_enabled() is False


def test_runtime_receipt_identifies_the_loaded_editor() -> None:
    from gateway.lifeboat_editor import runtime_receipt

    receipt = runtime_receipt()

    assert receipt["module"].endswith("gateway/lifeboat_editor.py")
    assert len(receipt["sha256"]) == 64
    assert isinstance(receipt["pid"], int)
    assert isinstance(receipt["editor_enabled"], bool)


# --- the delivery counter --------------------------------------------------

def test_deliveries_are_counted_per_session(tmp_path) -> None:
    from gateway.lifeboat_editor import bump_delivery_count

    assert bump_delivery_count(tmp_path, "chat-1") == 1
    assert bump_delivery_count(tmp_path, "chat-1") == 2
    assert bump_delivery_count(tmp_path, "chat-2") == 1


def test_the_counter_survives_an_unreadable_file(tmp_path) -> None:
    from gateway.lifeboat_editor import bump_delivery_count

    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "lifeboat-deliveries-chat-1.json").write_text("{ nope", encoding="utf-8")

    assert bump_delivery_count(tmp_path, "chat-1") == 1
