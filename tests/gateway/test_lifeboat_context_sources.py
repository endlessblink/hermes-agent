"""Reading the notes Life-Boat already keeps, so a reply can be continuous.

The bot kept a journal, a weekly rollup and a queue of parked subjects, and
consulted none of them — it scraped the last few chat messages instead. That is
why a check-in could open by asking about a deploy while a real subject sat
parked for a month.

Fixtures here are synthetic. Tests must never read the real vault.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_context_sources import (
    QueueItem,
    build_context_block,
    parse_queue,
    recent_journal_lines,
)


QUEUE = """
## Queue contract
Some prose about rules.

## Items

- id: q-1
  status: active
  added: 2026-08-01
  topic: the interview and how the manager reacted
  next_point: what made the thought jump to being a failure

- id: q-2
  status: pending
  added: 2026-07-15
  topic: loneliness after the move
  next_point: whether it predates the move

- id: q-3
  status: done
  added: 2026-06-01
  topic: something already finished
  next_point: nothing
"""

JOURNAL = """# Daily Evidence Journal — 2026-08-22
## דברים שנעשו / שניגשתי אליהם
went out, cleaned, watered the plants
## דפוס ששמתי לב אליו
self-criticism attacks from both directions at once
## משפט מאוזן / שימושי יותר
I know there are people for whom this is different about me
"""


# --- the queue --------------------------------------------------------------

def test_the_queue_parses_into_items() -> None:
    assert len(parse_queue(QUEUE)) == 3


def test_an_item_keeps_its_topic_and_next_point() -> None:
    item = next(i for i in parse_queue(QUEUE) if i.id == "q-1")

    assert "interview" in item.topic
    assert "failure" in item.next_point


def test_the_active_item_is_identifiable() -> None:
    active = [i for i in parse_queue(QUEUE) if i.status == "active"]

    assert len(active) == 1
    assert active[0].id == "q-1"


def test_finished_items_are_not_offered() -> None:
    offerable = [i for i in parse_queue(QUEUE) if i.status in ("active", "pending")]

    assert {i.id for i in offerable} == {"q-1", "q-2"}


def test_an_empty_queue_parses_to_nothing() -> None:
    assert parse_queue("") == ()


def test_malformed_queue_text_does_not_raise() -> None:
    assert parse_queue("## Items\n- id:\n  garbage\n") is not None


# --- the journal ------------------------------------------------------------

def test_the_pattern_line_is_extracted() -> None:
    lines = recent_journal_lines([("2026-08-22", JOURNAL)])

    assert any("both directions" in line for line in lines)


def test_the_balanced_sentence_is_extracted() -> None:
    lines = recent_journal_lines([("2026-08-22", JOURNAL)])

    assert any("people for whom this is different" in line for line in lines)


def test_routine_activity_is_not_treated_as_a_pattern() -> None:
    """'watered the plants' is a thing done, not something to open a reply on."""
    lines = recent_journal_lines([("2026-08-22", JOURNAL)])

    assert not any("watered the plants" in line for line in lines)


def test_each_line_carries_its_date() -> None:
    lines = recent_journal_lines([("2026-08-22", JOURNAL)])

    assert all("2026-08-22" in line for line in lines)


def test_entries_are_bounded() -> None:
    many = [(f"2026-08-{day:02d}", JOURNAL) for day in range(1, 29)]

    assert len(recent_journal_lines(many)) <= 12


# --- the assembled block ----------------------------------------------------

def test_the_block_names_the_active_subject() -> None:
    block = build_context_block(queue_text=QUEUE, journal_entries=[("2026-08-22", JOURNAL)])

    assert "interview" in block


def test_the_block_is_empty_when_there_is_nothing() -> None:
    assert build_context_block(queue_text="", journal_entries=[]) == ""


def test_the_block_forbids_inventing() -> None:
    block = build_context_block(queue_text=QUEUE, journal_entries=[("2026-08-22", JOURNAL)])

    assert "only" in block.lower()
    assert "invent" in block.lower()


def test_the_block_states_that_material_may_be_stale() -> None:
    block = build_context_block(queue_text=QUEUE, journal_entries=[("2026-08-22", JOURNAL)])

    assert "passed" in block.lower() or "still" in block.lower()


def test_a_broken_source_yields_no_block_rather_than_an_error() -> None:
    assert build_context_block(queue_text=None, journal_entries=None) == ""
