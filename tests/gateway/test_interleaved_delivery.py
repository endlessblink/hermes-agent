"""A workout should arrive as exercise, demo, exercise, demo — in that order.

The model already writes it that way. The dispatch used to send the whole text
as one message and then every picture in a single batch afterwards, so the
reader got a wall of instructions followed by a pile of unlabelled clips with no
way to tell which belonged to which exercise.

Splitting is deliberately narrow: it only happens when text actually sits
between two pictures. A reply with one attachment, or a preamble followed by a
gallery, keeps its single message and one batch.
"""

from __future__ import annotations

import pytest

from gateway.platforms.base import split_interleaved_media


def _kinds(parts):
    return [kind for kind, _ in parts]


def test_a_workout_splits_into_alternating_parts(tmp_path):
    gif_a, gif_b = "/tmp/goblet.gif", "/tmp/swing.gif"
    content = (
        "1. Goblet Squat — 10 reps\n"
        f"MEDIA:{gif_a}\n"
        "Keep your chest up.\n\n"
        "2. Kettlebell Swing — 15 reps\n"
        f"MEDIA:{gif_b}\n"
        "Drive with the hips."
    )

    parts = split_interleaved_media(content)

    assert _kinds(parts) == ["text", "media", "text", "media", "text"]
    assert [p for k, p in parts if k == "media"] == [gif_a, gif_b]
    assert parts[0][1].startswith("1. Goblet Squat")


def test_a_single_attachment_is_left_alone():
    """One picture under one message needs no splitting."""
    assert split_interleaved_media("Here you go\nMEDIA:/tmp/a.gif") == []


def test_a_gallery_is_left_alone():
    """Several pictures with nothing between them belong in one batch."""
    content = "Here are the demos\nMEDIA:/tmp/a.gif\nMEDIA:/tmp/b.gif\nMEDIA:/tmp/c.gif"
    assert split_interleaved_media(content) == []


def test_a_trailing_note_after_a_gallery_is_left_alone():
    """Text after the last picture is not interleaving — it is a sign-off, and
    splitting on it would turn ordinary replies into two messages."""
    content = "Demos:\nMEDIA:/tmp/a.gif\nMEDIA:/tmp/b.gif\nTell me how it goes."
    assert split_interleaved_media(content) == []


def test_plain_text_is_left_alone():
    assert split_interleaved_media("No pictures here at all.") == []


def test_the_order_survives_pictures_with_no_text_between_some_of_them():
    """Partially interleaved still splits — the parts that do have text keep it."""
    content = (
        "First\nMEDIA:/tmp/a.gif\nMEDIA:/tmp/b.gif\nSecond\nMEDIA:/tmp/c.gif"
    )
    parts = split_interleaved_media(content)
    assert _kinds(parts) == ["text", "media", "media", "text", "media"]
