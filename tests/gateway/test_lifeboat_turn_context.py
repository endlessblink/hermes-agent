"""The turn gets material, not only rules.

Guidance was 100% prohibitions and 0% content, so the bot could not be specific
about his week even when he asked it to debrief his week.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.lifeboat_turn_context import build_turn_context, recent_user_turns


def _write(root: Path, name: str, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(body, encoding="utf-8")


def _entry(stamp: str, said: str, replied: str = "בסדר.") -> str:
    return (
        f"## {stamp} — session `s1` — platform `telegram`\n\n"
        f"### User\n\n{said}\n\n### Assistant\n\n{replied}\n\n---\n\n"
    )


def test_his_own_words_come_back(tmp_path: Path) -> None:
    _write(tmp_path, "2026-08-23.md", _entry("2026-08-23T19:36", "[The True Noam] היה ספיד־דייט והוא נגמר רע"))

    turns = recent_user_turns(tmp_path, legacy_dir=tmp_path / 'none')

    assert turns and "ספיד" in turns[0][1]
    assert "The True Noam" not in turns[0][1]


def test_the_bots_own_replies_are_not_treated_as_evidence(tmp_path: Path) -> None:
    _write(tmp_path, "2026-08-23.md", _entry("2026-08-23T19:36", "משהו קרה", "החוט הפעיל הוא"))

    assert all("החוט" not in line
               for _, line in recent_user_turns(tmp_path, legacy_dir=tmp_path / 'none'))


def test_bug_talk_is_dropped(tmp_path: Path) -> None:
    _write(tmp_path, "2026-08-23.md", _entry("2026-08-23T10:00", "[The True Noam] תבדוק את הבאג בגייטוויי"))

    assert recent_user_turns(tmp_path, legacy_dir=tmp_path / 'none') == ()


def test_the_context_names_what_it_is(tmp_path: Path) -> None:
    _write(tmp_path, "2026-08-23.md", _entry("2026-08-23T19:36", "[The True Noam] היה ספיד־דייט"))

    block = build_turn_context(
        transcript_dir=tmp_path, legacy_dir=tmp_path / 'none',
        queue_text='', journal_entries=[])

    assert "MATERIAL YOU ALREADY HAVE" in block
    assert "ספיד" in block


def test_ordinary_disclosure_does_not_receive_stored_material(tmp_path: Path) -> None:
    _write(tmp_path, "2026-08-23.md", _entry("2026-08-23T19:36", "היה ספיד־דייט"))

    block = build_turn_context(
        transcript_dir=tmp_path,
        legacy_dir=tmp_path / "none",
        request_text="היום בעבודה היה לי קשה להתרכז",
        queue_text="",
        journal_entries=[],
    )

    assert block == ""


def test_explicit_checkin_request_can_receive_stored_material(tmp_path: Path) -> None:
    _write(tmp_path, "2026-08-23.md", _entry("2026-08-23T19:36", "היה ספיד־דייט"))

    block = build_turn_context(
        transcript_dir=tmp_path,
        legacy_dir=tmp_path / "none",
        request_text="בוא נעשה סיכום של הימים האחרונים",
        queue_text="",
        journal_entries=[],
    )

    assert "ספיד" in block


def test_an_empty_hand_is_admitted_not_papered_over(tmp_path: Path) -> None:
    assert build_turn_context(
        transcript_dir=tmp_path, legacy_dir=tmp_path / 'none',
        queue_text='', journal_entries=[]) == ""


def test_the_older_mixed_log_is_read_but_filtered(tmp_path: Path) -> None:
    """His history predates the split, and reading it is safe where moving was not."""
    from gateway.lifeboat_turn_context import recent_user_turns

    old = tmp_path / "default"
    _write(old, "2026-08-20.md", _entry("2026-08-20T10:00", "[The True Noam] היה לי יום קשה עם ההורים"))
    _write(old, "2026-08-21.md", _entry("2026-08-21T11:00", "[The True Noam] תריץ את הטסטים בגייטוויי"))
    new = tmp_path / "life-boat"
    _write(new, "2026-08-23.md", _entry("2026-08-23T19:00", "[The True Noam] היה ספיד־דייט"))

    turns = recent_user_turns(new, legacy_dir=old)
    said = " ".join(line for _, line in turns)

    assert "ההורים" in said
    assert "ספיד" in said
    assert "טסטים" not in said


def test_the_same_line_is_not_shown_twice(tmp_path: Path) -> None:
    """Tonight's turns were copied, not moved, so both logs hold them."""
    from gateway.lifeboat_turn_context import recent_user_turns

    old = tmp_path / "default"
    new = tmp_path / "life-boat"
    _write(old, "2026-08-23.md", _entry("2026-08-23T19:36", "[The True Noam] היה ספיד־דייט"))
    _write(new, "2026-08-23.md", _entry("2026-08-23T19:36", "[The True Noam] היה ספיד־דייט"))

    assert len(recent_user_turns(new, legacy_dir=old)) == 1


@pytest.mark.parametrize(
    "said",
    [
        "[IMPORTANT: The user has not responded in a while]",
        "[Background process finished]",
        "[System note: compression applied]",
    ],
)
def test_injected_notes_are_not_treated_as_his_words(tmp_path: Path, said: str) -> None:
    from gateway.lifeboat_turn_context import recent_user_turns

    _write(tmp_path, "2026-08-23.md", _entry("2026-08-23T19:36", said))

    assert recent_user_turns(tmp_path, legacy_dir=tmp_path / "none") == ()
