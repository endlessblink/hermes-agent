"""How often a parked subject may be raised again.

Offering the same subject repeatedly to someone who is not taking it up is
nagging; retiring it the moment they ignore it once is forgetting. Noam asked
for neither: the wait should grow, and at a month the subject should stop being
offered while still being remembered.

So the wait grows with each unanswered offer and stops growing at a month. Past
that the subject is archived — never offered, never deleted, still there if he
raises it himself. Engagement resets the clock entirely. Saying to drop it ends
it for good.

Nothing here infers how he feels. It counts offers and looks at dates, which is
why it can be relied on.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


#: The wait after each successive unanswered offer. It stops growing at a
#: month: beyond that the subject is archived rather than waited on longer,
#: because a subject raised twice a year is neither remembered nor offered.
_WAITS = (
    timedelta(days=1),
    timedelta(days=3),
    timedelta(days=7),
    timedelta(days=14),
    timedelta(days=30),
)
ARCHIVE_AFTER = _WAITS[-1]

#: Offers without engagement at which a subject stops being offered.
_ARCHIVE_AT_OFFERS = len(_WAITS)


@dataclass(frozen=True)
class OfferRecord:
    """What has happened with one parked subject."""

    subject_id: str
    last_offered: datetime | None = None
    offers_without_engagement: int = 0
    dismissed: bool = False

    @property
    def archived(self) -> bool:
        """True when it has been offered enough times to stop offering it.

        Archived is not deleted. The record stays so the subject can still be
        found if he brings it up himself.
        """
        return self.offers_without_engagement >= _ARCHIVE_AT_OFFERS

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "last_offered": self.last_offered.isoformat() if self.last_offered else None,
            "offers_without_engagement": self.offers_without_engagement,
            "dismissed": self.dismissed,
            "version": 1,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "OfferRecord":
        data = value or {}
        raw = data.get("last_offered")
        last: datetime | None = None
        if raw:
            try:
                last = datetime.fromisoformat(str(raw))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                last = None
        try:
            offers = int(data.get("offers_without_engagement") or 0)
        except (TypeError, ValueError):
            offers = 0
        return cls(
            subject_id=str(data.get("subject_id") or ""),
            last_offered=last,
            offers_without_engagement=max(0, offers),
            dismissed=bool(data.get("dismissed")),
        )


def next_wait(offers_without_engagement: int) -> timedelta:
    """How long to wait before this subject may be offered again."""
    if offers_without_engagement <= 0:
        return timedelta(0)
    index = min(offers_without_engagement, len(_WAITS)) - 1
    return _WAITS[index]


def should_offer(record: OfferRecord, *, now: datetime) -> bool:
    """Return True when this subject may be raised again right now."""
    if record.dismissed or record.archived:
        return False
    if record.last_offered is None:
        return True
    return (now - record.last_offered) >= next_wait(record.offers_without_engagement)


def record_offer(record: OfferRecord, *, now: datetime) -> OfferRecord:
    """Note that the subject was offered, without knowing yet if it landed."""
    return replace(
        record,
        last_offered=now,
        offers_without_engagement=record.offers_without_engagement + 1,
    )


def record_engagement(record: OfferRecord, *, now: datetime) -> OfferRecord:
    """He took it up. The subject is live again and the wait starts over."""
    return replace(record, offers_without_engagement=0, last_offered=now)


def record_dismissal(record: OfferRecord, *, now: datetime) -> OfferRecord:
    """He asked to drop it. That is final, and not a wait."""
    return replace(record, dismissed=True, last_offered=now)


def apply_correction(record: OfferRecord, correction, *, now: datetime) -> OfferRecord:
    """Turn what Noam said about a subject into what happens to it next.

    A misreading is treated as engagement, not as rejection: he corrected the
    interpretation, which means the subject is live enough to be worth
    correcting. Retiring it there would punish him for engaging.
    """
    from gateway.lifeboat_correction import Correction

    if correction == Correction.DISMISSED:
        return record_dismissal(record, now=now)
    if correction == Correction.PASSED:
        # Done, not forbidden. Archived so it stops being offered while the
        # record survives for him to reopen.
        return replace(
            record,
            last_offered=now,
            offers_without_engagement=max(record.offers_without_engagement, _ARCHIVE_AT_OFFERS),
        )
    if correction in (Correction.ENGAGED, Correction.MISREAD):
        return record_engagement(record, now=now)
    return record
