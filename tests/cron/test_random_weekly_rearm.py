"""A recurring casual check-in must re-arm after it fires.

The Life-Boat spontaneous check-in is a random-weekly job with several slots a
week. It ran once and then marked itself completed and disabled with three
slots still ahead, so the casual contact simply stopped.

The cause is an argument mismatch: compute_next_run takes an ISO string for
last_run_at, and two call sites pass a datetime. The re-arm path is one of
them, so the moment a recurring job ran, computing its next slot failed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from cron.jobs import compute_next_run


TZ = ZoneInfo("Asia/Jerusalem")


def _weekly_schedule(occurrences):
    return {
        "kind": "random_weekly",
        "start_minute": 600,
        "end_minute": 1320,
        "count": 4,
        "display": "random weekly 4 10:00-22:00",
        "occurrences": [value.isoformat() for value in occurrences],
    }


def _upcoming(count=4):
    base = datetime.now(TZ)
    return [base + timedelta(days=offset, hours=1) for offset in range(count)]


def test_a_random_weekly_job_has_a_next_slot() -> None:
    schedule = _weekly_schedule(_upcoming())

    assert compute_next_run(schedule) is not None


def test_it_still_has_a_next_slot_after_running() -> None:
    """This is the case that failed: re-arming after a run."""
    occurrences = _upcoming()
    schedule = _weekly_schedule(occurrences)
    just_ran = occurrences[0].isoformat()

    assert compute_next_run(schedule, just_ran) is not None


def test_the_next_slot_is_after_the_run_that_just_happened() -> None:
    occurrences = _upcoming()
    schedule = _weekly_schedule(occurrences)

    result = compute_next_run(schedule, occurrences[0].isoformat())

    assert datetime.fromisoformat(result) > occurrences[0]


def test_a_datetime_does_not_silently_lose_the_schedule() -> None:
    """Passing a datetime is a caller slip; returning None retires the job."""
    occurrences = _upcoming()
    schedule = _weekly_schedule(occurrences)

    assert compute_next_run(schedule, occurrences[0]) is not None


def test_exhausted_occurrences_are_regenerated() -> None:
    past = [datetime.now(TZ) - timedelta(days=offset) for offset in range(1, 5)]
    schedule = _weekly_schedule(past)

    assert compute_next_run(schedule) is not None
