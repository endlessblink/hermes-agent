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

    assert "known him for years" in lifeboat_voice.load_voice_text()


def test_switching_back_is_the_same_one_word(home) -> None:
    lifeboat_voice.ensure_voice_files()
    (home / "lifeboat-voice").write_text("coach", encoding="utf-8")

    text = lifeboat_voice.load_voice_text()
    assert "helps him think about his life" in text
    assert "known him for years" not in text


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

    assert "known him for years" in lifeboat_voice.load_voice_text()


def test_a_voice_file_is_shipped_for_each_name(home) -> None:
    lifeboat_voice.ensure_voice_files()

    for name in lifeboat_voice.DEFAULT_VOICES:
        assert (home / "lifeboat-voices" / f"{name}.md").is_file()


#: Hebrew that describes how to sound is safe. Hebrew that could be sent to him
#: is not: hand the model a Hebrew sentence and it will deliver that sentence.
def test_no_hebrew_in_a_voice_could_be_sent_as_a_message() -> None:
    """The regression, named. On 2026-08-24 the voice said, in Hebrew, to ask
    whether something significant happened, whether something caused hard
    feelings, or whether something was stuck in a loop -- and the bot sent him
    exactly that, as a question. His words: "I don't want hardcoding!!!"

    Hebrew survives here only where it cannot be lifted: the register rules,
    which are a list of things not to say.
    """
    for name, text in lifeboat_voice.DEFAULT_VOICES.items():
        for line in text.splitlines():
            if not any("֐" <= ch <= "׿" for ch in line):
                continue
            assert "?" not in line, f"{name}: a deliverable question: {line!r}"
            # A Hebrew line is allowed only when it is telling the bot what not
            # to say. Anything else is phrasing it can hand to him.
            assert "אל תשתמש" in line or "לא ״" in line or "כתוב" in line, (
                f"{name}: Hebrew that is not a register rule can be copied: {line!r}"
            )


def test_the_things_it_looks_for_are_not_written_in_hebrew() -> None:
    """Content in Hebrew gets delivered; content in English must be reworded."""
    for name, text in lifeboat_voice.DEFAULT_VOICES.items():
        assert "significant" in text, f"{name}: what it looks for should be English"
        assert "משמעותי" not in text, f"{name}: that phrasing will come back verbatim"


def test_no_voice_hands_the_model_a_reply_to_copy() -> None:
    """Instructions in Hebrew are fine; a deliverable sentence is not.

    The voices are written in Hebrew because English instructions could not
    govern Hebrew register -- "left him with hard feelings" came back as
    "נשאר איתך", the therapist idiom. So the old check (no Hebrew at all) is
    replaced by the thing it was really protecting: no sentence in here may be
    something the bot could send him as a reply.
    """
    for text in lifeboat_voice.DEFAULT_VOICES.values():
        assert "?" not in text and "\u003f" not in text


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

    assert "known him for years" in messages[0]["content"]


def test_the_retry_path_is_unchanged_when_no_voice_is_chosen(home) -> None:
    from gateway import lifeboat_rewrite

    messages = lifeboat_rewrite.build_rewrite_messages("שלום", "טיוטה", "premature_closure")

    assert "known him for years" not in messages[0]["content"]


# --- improving a voice he has not touched ---------------------------------

def test_an_untouched_voice_is_refreshed_when_the_default_improves(home) -> None:
    """Otherwise an improvement stays in the source and never reaches him.

    The purpose paragraph was added to the shipped voices and did nothing,
    because the files already existed and the writer refused to overwrite.
    """
    from gateway.lifeboat_voice import SUPERSEDED_DEFAULTS

    lifeboat_voice.ensure_voice_files()
    path = home / "lifeboat-voices" / "friend.md"
    path.write_text(SUPERSEDED_DEFAULTS[0] + "\n", encoding="utf-8")

    lifeboat_voice.ensure_voice_files()

    assert "What you are looking for" in path.read_text(encoding="utf-8")


def test_a_voice_he_edited_is_still_never_touched(home) -> None:
    lifeboat_voice.ensure_voice_files()
    path = home / "lifeboat-voices" / "friend.md"
    path.write_text("You are Dana. You have known him since the army.", encoding="utf-8")

    lifeboat_voice.ensure_voice_files()

    assert path.read_text(encoding="utf-8") == "You are Dana. You have known him since the army."
