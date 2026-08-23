"""How much machinery sits between the model and him, as a setting he owns.

His observation at the end of the night, and the reason this exists: "you are a
better bot than it is -- you talked to me all night and I didn't feel like you
degraded." Same class of model; the difference is the wrapper. Bare mode removes
it. These tests hold the properties that make it safe to leave in his hands.
"""

from __future__ import annotations

import pytest

from gateway import lifeboat_mode


@pytest.fixture()
def mode_file(tmp_path, monkeypatch):
    path = tmp_path / "lifeboat-mode"
    monkeypatch.setattr(lifeboat_mode, "MODE_FILE", path)
    return path


def test_no_file_means_it_works_the_way_it_always_has(mode_file) -> None:
    """A missing setting must never silently change how it talks to him."""
    assert lifeboat_mode.current_mode() == lifeboat_mode.WRAPPED
    assert lifeboat_mode.is_bare() is False


def test_one_word_strips_the_wrapper(mode_file) -> None:
    mode_file.write_text("bare\n", encoding="utf-8")

    assert lifeboat_mode.is_bare() is True


def test_one_word_puts_it_back(mode_file) -> None:
    mode_file.write_text("wrapped", encoding="utf-8")

    assert lifeboat_mode.is_bare() is False


def test_an_empty_file_is_not_a_mode(mode_file) -> None:
    mode_file.write_text("   \n", encoding="utf-8")

    assert lifeboat_mode.is_bare() is False


def test_a_typo_does_not_strip_anything(mode_file) -> None:
    """Guessing at what he meant is how a bot ends up in a state he never chose."""
    mode_file.write_text("bear", encoding="utf-8")

    assert lifeboat_mode.is_bare() is False


def test_case_and_whitespace_are_forgiven(mode_file) -> None:
    mode_file.write_text("  BARE \n", encoding="utf-8")

    assert lifeboat_mode.is_bare() is True


# --- what bare mode actually hands the model ------------------------------

def test_bare_guidance_is_the_identity_and_the_harm_rules(tmp_path, monkeypatch) -> None:
    from gateway import lifeboat_followups, lifeboat_voice

    monkeypatch.setattr(lifeboat_mode, "MODE_FILE", tmp_path / "lifeboat-mode")
    (tmp_path / "lifeboat-mode").write_text("bare", encoding="utf-8")
    monkeypatch.setattr(lifeboat_voice, "ACTIVE_FILE", tmp_path / "voice")
    monkeypatch.setattr(lifeboat_voice, "VOICE_DIR", tmp_path / "voices")
    lifeboat_voice.ensure_voice_files()
    (tmp_path / "voice").write_text("friend", encoding="utf-8")

    guidance = lifeboat_followups.prepare_lifeboat_inbound_guidance(
        tmp_path, "bare-test", "היה לי יום קשה"
    )

    assert "מכיר אותו שנים" in guidance
    # No shape orders, no length budget, no stance guidance.
    for absent in ("characters and", "sentences.", "signal guidance", "numbered breakdowns"):
        assert absent not in guidance
    # The rules that matter if a reply gets them wrong stay.
    assert "Do not diagnose him" in guidance
    assert "real human support" in guidance


def test_wrapped_guidance_still_carries_everything(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(lifeboat_mode, "MODE_FILE", tmp_path / "lifeboat-mode")

    from gateway import lifeboat_followups

    guidance = lifeboat_followups.prepare_lifeboat_inbound_guidance(
        tmp_path, "wrapped-test", "היה לי יום קשה"
    )

    assert "sentences." in guidance
