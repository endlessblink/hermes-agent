"""Regression tests for the Life-Boat per-turn guidance.

Each test here locks in a defect that reached Noam in a real support conversation.
The behaviour they cover was measured in a sandbox replay, not guessed - see
``docs/runtime/live-tree-and-deploy.md``.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_psychology import (
    _CLOSING_ANCHOR,
    LifeBoatTrajectory,
    build_signal_guidance,
)


HEBREW_DISTRESS = "אני מרגיש שאני מאכזב את כולם"


def test_guidance_never_contains_the_user_text():
    """The guidance is ephemeral routing, never a carrier for what he wrote.

    ``build_signal_guidance`` classifies the message to pick a stance; if the message
    itself leaked into the returned string it would ride along into every downstream
    log, archive and prompt dump for a psychological-support conversation.
    """
    guidance = build_signal_guidance(HEBREW_DISTRESS)
    assert HEBREW_DISTRESS not in guidance
    for word in HEBREW_DISTRESS.split():
        assert word not in guidance


def test_closing_anchor_is_the_last_thing_the_model_reads():
    """Position is the point, not mere presence.

    The anchor addresses the one sentence that kept failing, and long conversations
    erode instructions that sit early in a long block. If a later section is appended
    after it, this test fails and the anchor must be moved back to the end.
    """
    guidance = build_signal_guidance(HEBREW_DISTRESS)
    assert guidance.rstrip().endswith(_CLOSING_ANCHOR.rstrip())


def test_multiple_threads_are_reflected_rather_than_handed_back_as_a_choice():
    """The pick-one-of-three question, measured at 4/8 replies before the fix.

    The contract used to only *forbid* an either/or question while also demanding the
    assistant stay with several threads at once - a collision whose easiest resolution
    is "is it X, Y or Z?". The instruction must now say what to do instead, which is
    the Motivational Interviewing move: reflect the several things and let his next
    message choose.
    """
    guidance = build_signal_guidance(HEBREW_DISTRESS)
    assert "reflect the several things" in guidance
    assert "let their next message do the choosing" in guidance
    assert "Do not ask them which one to take" in guidance
    assert "names no alternatives" in guidance


def test_self_state_question_is_answered_briefly_and_returned_to_the_user():
    """From the 2026-08-13 01:23 exchange.

    He asked the bot whether it was angry with him and got a paragraph about the bot's
    own intentions, leaving him managing its feelings while he was the one in pain.
    """
    guidance = build_signal_guidance(HEBREW_DISTRESS)
    assert "answer in one plain beat and return to" in guidance
    assert "their standing with you" in guidance


@pytest.mark.parametrize(
    "banned",
    [
        "numbered breakdowns",
        "quoted maxims presented as the takeaway",
        "menu of support options",
    ],
)
def test_packaging_shapes_stay_named_in_the_contract(banned: str):
    """These are the artifacts that were measured, so they must not silently drop out."""
    assert banned in build_signal_guidance(HEBREW_DISTRESS)


def test_crisis_signal_adds_safety_guidance_without_dropping_the_contract():
    """A safety turn must not become a bare crisis script.

    The contract still governs how the assistant talks; the crisis text is additive.
    """
    guidance = build_signal_guidance("אני לא רוצה לחיות")
    assert "immediate-safety signal" in guidance
    assert "ERAN 1201" in guidance
    assert "do not package what they said" in guidance


def test_recent_crisis_trajectory_is_carried_without_the_original_text():
    trajectory = LifeBoatTrajectory(recent_crisis_turns=2)
    guidance = build_signal_guidance("סתם יום רגיל", trajectory)
    assert "possible safety concern appeared recently" in guidance
    assert "סתם יום רגיל" not in guidance
