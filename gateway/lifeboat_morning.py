"""When the Life-Boat morning check-in is meant to run.

The verifier used to hardcode one cadence while the live job ran another, so it
reported a failure over a disagreement about intent rather than over anything
being broken. Intent needs somewhere to live.

Default is daily. Any cron expression overrides it, and "off" switches the
check-in away entirely without that counting as a fault -- proactive contact is
the user's call, not the checker's. The delivery topic is not negotiable in the
same way: cadence is a preference, but sending a personal check-in to the wrong
place is a boundary violation, so that is always reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


#: Every morning at 09:00, unless told otherwise.
DEFAULT_MORNING_SCHEDULE = "0 9 * * *"

#: Set to a cron expression to change the cadence, or to "off" to stop it.
MORNING_SCHEDULE_ENV = "LIFEBOAT_MORNING_SCHEDULE"

_OFF_VALUES = frozenset({"off", "none", "disabled", "0", "false", "no"})


@dataclass(frozen=True)
class MorningPolicy:
    """What the morning check-in is supposed to be doing."""

    enabled: bool
    schedule: str


def resolve_morning_policy(environ: Mapping[str, str] | None = None) -> MorningPolicy:
    """Read the intended cadence, defaulting to daily."""
    import os

    source = environ if environ is not None else os.environ
    raw = str(source.get(MORNING_SCHEDULE_ENV, "") or "").strip()
    if not raw:
        return MorningPolicy(True, DEFAULT_MORNING_SCHEDULE)
    if raw.casefold() in _OFF_VALUES:
        return MorningPolicy(False, DEFAULT_MORNING_SCHEDULE)
    return MorningPolicy(True, raw)


def morning_job_problems(
    job: Mapping[str, Any] | None,
    policy: MorningPolicy,
    *,
    topic: str,
) -> tuple[str, ...]:
    """Return every way the scheduled job disagrees with the intended policy."""
    if not policy.enabled:
        # Switched off on purpose: an absent or paused job is the point.
        return ()

    if not job:
        return ("the morning check-in is missing while the policy expects it enabled",)

    problems: list[str] = []

    if not job.get("enabled") or job.get("state") != "scheduled":
        problems.append("the morning check-in is disabled while the policy expects it enabled")

    expr = str((job.get("schedule") or {}).get("expr") or "").strip()
    if expr != policy.schedule:
        problems.append(
            f"the morning check-in schedule is {expr!r}, and the policy expects {policy.schedule!r}"
        )

    if str(job.get("deliver") or "") != topic:
        problems.append(
            f"the morning check-in delivers to {job.get('deliver')!r} rather than the Life-Boat topic"
        )

    return tuple(problems)
