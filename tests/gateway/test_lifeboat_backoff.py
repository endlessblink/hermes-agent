"""How often a parked subject may be raised again.

Offering the same subject repeatedly to someone who is not engaging with it is
nagging, and retiring it the moment they ignore it once is forgetting. So the
wait grows each time a subject is offered and left alone, and stops growing at a
month — past that it is no longer offered but is never deleted, because Noam
asked for it to be remembered rather than dropped.

Engagement resets the clock. Saying to drop it ends it for good.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gateway.lifeboat_backoff import (
    ARCHIVE_AFTER,
    OfferRecord,
    next_wait,
    record_dismissal,
    record_engagement,
    record_offer,
    should_offer,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _record(**kw):
    return OfferRecord(subject_id="q-1", **kw)


# --- how the wait grows -----------------------------------------------------

def test_a_subject_never_offered_may_be_offered() -> None:
    assert should_offer(_record(), now=NOW) is True


def test_the_wait_grows_with_each_unanswered_offer() -> None:
    waits = [next_wait(n) for n in range(0, 5)]

    assert waits == sorted(waits)
    assert len(set(waits)) > 1


def test_the_first_wait_is_short() -> None:
    assert next_wait(1) <= timedelta(days=3)


def test_the_wait_stops_growing_at_a_month() -> None:
    assert next_wait(50) <= ARCHIVE_AFTER


def test_a_subject_offered_moments_ago_is_not_offered_again() -> None:
    record = _record(last_offered=NOW - timedelta(hours=1), offers_without_engagement=1)

    assert should_offer(record, now=NOW) is False


def test_a_subject_becomes_offerable_once_its_wait_has_passed() -> None:
    record = _record(last_offered=NOW - timedelta(days=40), offers_without_engagement=2)

    assert should_offer(record, now=NOW) is True


# --- the month cap ----------------------------------------------------------

def test_a_long_ignored_subject_stops_being_offered() -> None:
    """Noam's rule: at a month it can stop coming up."""
    record = _record(last_offered=NOW - timedelta(days=365), offers_without_engagement=12)

    assert should_offer(record, now=NOW) is False


def test_a_long_ignored_subject_is_not_deleted() -> None:
    """His other rule: it still needs to be remembered."""
    record = _record(offers_without_engagement=12)

    assert record.archived is True
    assert record.dismissed is False


def test_an_archived_subject_can_still_be_looked_up() -> None:
    record = _record(offers_without_engagement=99)

    assert record.subject_id == "q-1"


# --- what engagement and dismissal do ---------------------------------------

def test_engagement_resets_the_wait() -> None:
    record = record_engagement(_record(offers_without_engagement=4), now=NOW)

    assert record.offers_without_engagement == 0
    assert record.archived is False


def test_engagement_makes_a_subject_offerable_again() -> None:
    record = record_engagement(
        _record(last_offered=NOW - timedelta(days=400), offers_without_engagement=12), now=NOW
    )

    assert should_offer(record, now=NOW + timedelta(days=2)) is True


def test_dismissal_ends_it_permanently() -> None:
    record = record_dismissal(_record(), now=NOW)

    assert record.dismissed is True
    assert should_offer(record, now=NOW + timedelta(days=3650)) is False


def test_an_offer_is_counted() -> None:
    record = record_offer(_record(), now=NOW)

    assert record.offers_without_engagement == 1
    assert record.last_offered == NOW


def test_offering_twice_counts_twice() -> None:
    record = record_offer(record_offer(_record(), now=NOW), now=NOW + timedelta(days=5))

    assert record.offers_without_engagement == 2


# --- persistence ------------------------------------------------------------

def test_a_record_round_trips() -> None:
    record = record_offer(_record(), now=NOW)

    assert OfferRecord.from_dict(record.to_dict()) == record


def test_an_unknown_record_reads_as_never_offered() -> None:
    assert OfferRecord.from_dict({"subject_id": "q-9"}).offers_without_engagement == 0


# --- corrections drive the back-off -----------------------------------------

from gateway.lifeboat_backoff import apply_correction  # noqa: E402
from gateway.lifeboat_correction import Correction  # noqa: E402


def test_saying_it_passed_stops_it_being_offered_soon() -> None:
    record = apply_correction(_record(), Correction.PASSED, now=NOW)

    assert should_offer(record, now=NOW + timedelta(days=1)) is False


def test_saying_drop_it_ends_it_for_good() -> None:
    record = apply_correction(_record(), Correction.DISMISSED, now=NOW)

    assert record.dismissed is True
    assert should_offer(record, now=NOW + timedelta(days=3650)) is False


def test_engaging_makes_it_live_again() -> None:
    stale = _record(last_offered=NOW - timedelta(days=200), offers_without_engagement=9)

    record = apply_correction(stale, Correction.ENGAGED, now=NOW)

    assert record.offers_without_engagement == 0
    assert record.archived is False


def test_a_misreading_does_not_retire_the_subject() -> None:
    """He corrected the interpretation, not the topic; it stays live."""
    record = apply_correction(_record(offers_without_engagement=2), Correction.MISREAD, now=NOW)

    assert record.dismissed is False
    assert record.offers_without_engagement == 0


def test_ordinary_conversation_changes_nothing() -> None:
    before = _record(offers_without_engagement=2, last_offered=NOW)

    assert apply_correction(before, Correction.NONE, now=NOW) == before
