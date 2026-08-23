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
    NO_READ_COOLDOWN,
    NO_READ_TEXT,
    build_editor_messages,
    clean_editor_output,
    edit_reply,
    no_read_allowed,
    record_no_read,
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


def test_the_editor_prompt_hands_over_no_hebrew_sentence_to_reuse() -> None:
    """A supplied example is the template returning through the back door."""
    joined = " ".join(m["content"] for m in build_editor_messages(USER, BLAND, material=MATERIAL))
    prompt_only = joined.replace(USER, "").replace(BLAND, "").replace(MATERIAL, "")

    assert not any("֐" <= ch <= "׿" for ch in prompt_only)


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

def test_a_bland_but_legal_draft_is_replaced_by_the_edit() -> None:
    """The failure he reported: a reply that breaks no rule and says nothing."""
    delivered, outcome = resolve_reply(
        USER,
        BLAND,
        rewrite=lambda *a, **k: "unused",
        edit=lambda _m: WITH_A_READ,
        material=MATERIAL,
    )

    assert delivered == WITH_A_READ
    assert outcome == "edited"


def test_a_failed_edit_never_replaces_a_draft_that_passed() -> None:
    delivered, outcome = resolve_reply(
        USER,
        WITH_A_READ,
        rewrite=lambda *a, **k: "unused",
        edit=lambda _m: CLOSING,
    )

    assert delivered == WITH_A_READ
    assert outcome == "draft_kept"


def test_the_editor_runs_even_when_the_reviewer_accepted_the_draft() -> None:
    seen = []

    def watching(messages):
        seen.append(messages)
        return WITH_A_READ

    resolve_reply(USER, BLAND, rewrite=lambda *a, **k: "unused", edit=watching)

    assert len(seen) == 1


def test_a_rejected_draft_whose_edit_also_fails_falls_through_to_the_rewrite() -> None:
    delivered, outcome = resolve_reply(
        USER,
        CLOSING,
        rewrite=lambda *a, **k: WITH_A_READ,
        edit=lambda _m: "תודה על השיתוף, נעצור כאן.",
    )

    assert delivered == WITH_A_READ
    assert outcome == "rewritten"


def test_when_nothing_survives_review_he_is_told_there_is_no_read() -> None:
    delivered, outcome = resolve_reply(
        USER,
        CLOSING,
        rewrite=lambda *a, **k: "תודה על השיתוף, נעצור כאן.",
        edit=lambda _m: "תמשיך משם איך שזה יוצא.",
    )

    assert delivered == NO_READ_TEXT
    assert outcome == "no_read"


# --- the repetition guard --------------------------------------------------

def test_the_admission_is_allowed_when_nothing_is_recorded(tmp_path) -> None:
    assert no_read_allowed(tmp_path, "chat-1", deliveries=1) is True


def test_the_admission_is_refused_again_straight_away(tmp_path) -> None:
    record_no_read(tmp_path, "chat-1", deliveries=4)

    assert no_read_allowed(tmp_path, "chat-1", deliveries=5) is False


def test_the_admission_returns_after_the_cooldown(tmp_path) -> None:
    record_no_read(tmp_path, "chat-1", deliveries=4)

    assert no_read_allowed(tmp_path, "chat-1", deliveries=4 + NO_READ_COOLDOWN) is True


def test_the_guard_is_per_session(tmp_path) -> None:
    record_no_read(tmp_path, "chat-1", deliveries=4)

    assert no_read_allowed(tmp_path, "chat-2", deliveries=5) is True


def test_unreadable_state_does_not_block_the_admission(tmp_path) -> None:
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "lifeboat-noread-chat-1.json").write_text("{ not json", encoding="utf-8")

    assert no_read_allowed(tmp_path, "chat-1", deliveries=1) is True


def test_a_used_admission_is_recorded_where_the_guard_reads_it(tmp_path) -> None:
    record_no_read(tmp_path, "chat-1", deliveries=7)
    saved = json.loads((tmp_path / "state" / "lifeboat-noread-chat-1.json").read_text("utf-8"))

    assert saved["deliveries_at_last_use"] == 7


def test_the_model_speaks_rather_than_repeating_the_admission(tmp_path) -> None:
    """The older, worse bug was one fixed sentence delivered again and again."""
    record_no_read(tmp_path, "chat-1", deliveries=4)
    failed_again = "תודה על השיתוף, נעצור כאן."

    delivered, outcome = resolve_reply(
        USER,
        CLOSING,
        rewrite=lambda *a, **k: failed_again,
        profile_home=tmp_path,
        session_key="chat-1",
        deliveries=5,
    )

    assert delivered == failed_again
    assert outcome == "rewrite_rejected"


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
