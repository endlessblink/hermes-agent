"""Durable eligibility gate for the office-work daily planning turn.

This module deliberately does not choose a delivery surface.  Gateway, cron,
and desktop callers can consume the returned claim and start the appropriate
interactive turn without racing one another or producing duplicate plans.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
import json
import os
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo


DAILY_ASSISTANT_PROFILE = "office-work"
DAILY_ASSISTANT_TIMEZONE = ZoneInfo("Asia/Jerusalem")
DAILY_ASSISTANT_START = time(9, 0)

ClaimStatus = Literal[
    "due", "not_due", "already_claimed", "already_completed", "ineligible_profile"
]
ClaimReason = Literal["scheduled", "launch_catch_up"]
RESERVATION_TTL = timedelta(minutes=15)


@dataclass(frozen=True)
class DailyPlanningClaim:
    status: ClaimStatus
    local_date: str
    reason: Optional[ClaimReason] = None
    reservation_id: Optional[str] = None

    @property
    def claimed(self) -> bool:
        return self.status == "due"


def claim_daily_planning_trigger(
    profile_name: str,
    profile_home: Path,
    *,
    now: Optional[datetime] = None,
    on_launch: bool = False,
) -> DailyPlanningClaim:
    """Atomically claim today's office-work planning turn when it is due.

    ``profile_home`` must be the selected profile's Hermes home.  The atomic
    date-directory creation is the durable compare-and-set: concurrent cron,
    gateway, and desktop callers can race safely, and only one receives
    ``status='due'``.
    """

    instant = now or datetime.now(tz=DAILY_ASSISTANT_TIMEZONE)
    if instant.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local_now = instant.astimezone(DAILY_ASSISTANT_TIMEZONE)
    local_date = local_now.date().isoformat()

    if profile_name.strip().lower() != DAILY_ASSISTANT_PROFILE:
        return DailyPlanningClaim("ineligible_profile", local_date)
    if local_now.time().replace(tzinfo=None) < DAILY_ASSISTANT_START:
        return DailyPlanningClaim("not_due", local_date)

    day_dir = Path(profile_home) / "state" / "daily-assistant" / "claims" / local_date
    day_dir.mkdir(parents=True, exist_ok=True)
    completed_path = day_dir / "completed"
    reservation_path = day_dir / "reservation.json"
    if completed_path.exists():
        return DailyPlanningClaim("already_completed", local_date)

    reservation_id = uuid4().hex
    payload = json.dumps(
        {"reservation_id": reservation_id, "created_at": local_now.isoformat()},
        separators=(",", ":"),
    ).encode("utf-8")
    for _attempt in range(2):
        try:
            fd = os.open(reservation_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if not _remove_stale_reservation(reservation_path, local_now):
                return DailyPlanningClaim("already_claimed", local_date)
            continue
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        if completed_path.exists():
            _remove_owned_reservation(reservation_path, reservation_id)
            return DailyPlanningClaim("already_completed", local_date)
        break
    else:
        return DailyPlanningClaim("already_claimed", local_date)

    reason: ClaimReason = "launch_catch_up" if on_launch else "scheduled"
    return DailyPlanningClaim("due", local_date, reason, reservation_id)


def complete_daily_planning_trigger(profile_home: Path, claim: DailyPlanningClaim) -> bool:
    """Commit a delivered planning turn and permanently suppress today's duplicate."""
    if not claim.claimed or not claim.reservation_id:
        return False
    day_dir = _claim_day_dir(profile_home, claim.local_date)
    reservation_path = day_dir / "reservation.json"
    if not _reservation_is_owned(reservation_path, claim.reservation_id):
        return False
    completed_path = day_dir / "completed"
    tmp_path = day_dir / f".completed-{claim.reservation_id}"
    tmp_path.write_text(claim.reservation_id, encoding="utf-8")
    os.replace(tmp_path, completed_path)
    _remove_owned_reservation(reservation_path, claim.reservation_id)
    return True


def abandon_daily_planning_trigger(profile_home: Path, claim: DailyPlanningClaim) -> bool:
    """Release a failed delivery reservation so the same day can be retried."""
    if not claim.claimed or not claim.reservation_id:
        return False
    return _remove_owned_reservation(
        _claim_day_dir(profile_home, claim.local_date) / "reservation.json",
        claim.reservation_id,
    )


def _claim_day_dir(profile_home: Path, local_date: str) -> Path:
    return Path(profile_home) / "state" / "daily-assistant" / "claims" / local_date


def _read_reservation(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _reservation_is_owned(path: Path, reservation_id: str) -> bool:
    return _read_reservation(path).get("reservation_id") == reservation_id


def _remove_owned_reservation(path: Path, reservation_id: str) -> bool:
    if not _reservation_is_owned(path, reservation_id):
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _remove_stale_reservation(path: Path, now: datetime) -> bool:
    raw = _read_reservation(path)
    try:
        created_at = datetime.fromisoformat(raw["created_at"])
        stale = created_at.tzinfo is not None and now - created_at >= RESERVATION_TTL
    except (KeyError, TypeError, ValueError):
        stale = True
    if not stale:
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return True
