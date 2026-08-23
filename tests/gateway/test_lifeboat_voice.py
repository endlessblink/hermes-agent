"""Who the bot is, switchable by him without a deploy.

The bot spoke like a clinician because the only document describing its manner
called itself Personal Coaching and told it to be analytical and strategic.
Rules could not argue with that; nobody had ever chosen an identity for it.

The identity is text, so it lives in a text file he owns. These tests hold the
properties that make it safe to leave in his hands: an absent or unreadable
config never silently changes how the bot talks to him, and a voice he has
edited is never overwritten.
"""

from __future__ import annotations

import pytest

from gateway import lifeboat_voice


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(lifeboat_voice, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(lifeboat_voice, "ACTIVE_FILE", tmp_path / "lifeboat-voice")
    monkeypatch.setattr(lifeboat_voice, "VOICE_DIR", tmp_path / "lifeboat-voices")
    return tmp_path


def test_no_switch_file_leaves_the_bot_exactly_as_it_was(home) -> None:
    """An absent config must never change how it speaks to him at 2am."""
    assert lifeboat_voice.load_voice_text() == ""


def test_switching_is_one_word(home) -> None:
    lifeboat_voice.ensure_voice_files()
    (home / "lifeboat-voice").write_text("friend\n", encoding="utf-8")

    assert "close friend" in lifeboat_voice.load_voice_text()


def test_switching_back_is_the_same_one_word(home) -> None:
    lifeboat_voice.ensure_voice_files()
    (home / "lifeboat-voice").write_text("coach", encoding="utf-8")

    text = lifeboat_voice.load_voice_text()
    assert "helps him think about his life" in text
    assert "close friend" not in text


def test_turning_it_off_is_emptying_the_file(home) -> None:
    lifeboat_voice.ensure_voice_files()
    switch = home / "lifeboat-voice"
    switch.write_text("friend", encoding="utf-8")
    assert lifeboat_voice.load_voice_text()

    switch.write_text("", encoding="utf-8")
    assert lifeboat_voice.load_voice_text() == ""


def test_his_edits_are_never_overwritten(home) -> None:
    lifeboat_voice.ensure_voice_files()
    path = home / "lifeboat-voices" / "friend.md"
    path.write_text("You are Dana. You have known him since the army.", encoding="utf-8")

    lifeboat_voice.ensure_voice_files()

    assert path.read_text(encoding="utf-8").startswith("You are Dana")


def test_an_edited_voice_is_what_gets_used(home) -> None:
    lifeboat_voice.ensure_voice_files()
    (home / "lifeboat-voices" / "friend.md").write_text("You are Dana.", encoding="utf-8")
    (home / "lifeboat-voice").write_text("friend", encoding="utf-8")

    assert lifeboat_voice.load_voice_text() == "You are Dana."


def test_a_name_with_nothing_behind_it_does_not_pick_someone_else(home) -> None:
    """Speaking as a person he did not choose is worse than not switching."""
    (home / "lifeboat-voice").write_text("stranger", encoding="utf-8")

    assert lifeboat_voice.load_voice_text() == ""


def test_the_switch_is_case_and_whitespace_forgiving(home) -> None:
    lifeboat_voice.ensure_voice_files()
    (home / "lifeboat-voice").write_text("  FRIEND \n", encoding="utf-8")

    assert "close friend" in lifeboat_voice.load_voice_text()


def test_a_voice_file_is_shipped_for_each_name(home) -> None:
    lifeboat_voice.ensure_voice_files()

    for name in lifeboat_voice.DEFAULT_VOICES:
        assert (home / "lifeboat-voices" / f"{name}.md").is_file()


def test_no_voice_hands_the_model_a_hebrew_sentence_to_copy() -> None:
    """Descriptions of who is speaking, never replies to deliver."""
    for text in lifeboat_voice.DEFAULT_VOICES.values():
        assert not any("֐" <= ch <= "׿" for ch in text)


# --- every writer in the chain, not just the first ------------------------

def test_the_retry_path_speaks_as_the_chosen_person(home, monkeypatch) -> None:
    """On a rejected draft this is the voice he actually reads.

    The turn guidance and the editor were given an identity and this one was
    not, so a rejected draft came back sounding like a generic assistant --
    which is exactly what a live replay delivered.
    """
    from gateway import lifeboat_rewrite

    lifeboat_voice.ensure_voice_files()
    (home / "lifeboat-voice").write_text("friend", encoding="utf-8")

    messages = lifeboat_rewrite.build_rewrite_messages("שלום", "טיוטה", "premature_closure")

    assert "close friend" in messages[0]["content"]


def test_the_retry_path_is_unchanged_when_no_voice_is_chosen(home) -> None:
    from gateway import lifeboat_rewrite

    messages = lifeboat_rewrite.build_rewrite_messages("שלום", "טיוטה", "premature_closure")

    assert "close friend" not in messages[0]["content"]
