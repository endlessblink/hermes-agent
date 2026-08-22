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


#: Names that mark a scheduled job as Life-Boat proactive contact. Matched
#: loosely because these jobs are user-created and named in two languages.
_LIFEBOAT_JOB_MARKERS = ("lifeboat", "life-boat", "לייף בוט", "עיבוד רגשי", "ראיות יומי")


def _is_lifeboat_job(job: Mapping[str, Any]) -> bool:
    name = str(job.get("name") or "").casefold()
    return any(marker.casefold() in name for marker in _LIFEBOAT_JOB_MARKERS)


def proactive_delivery_problems(
    jobs: Any,
    *,
    topic: str,
) -> tuple[str, ...]:
    """Return every enabled Life-Boat job that would deliver somewhere else.

    Cadence is the user's preference. The destination is not: these messages
    are personal, and a bare platform target resolves to the home channel,
    which on this install is a different topic belonging to another assistant.
    Sending an invitation to process something emotional into the wrong topic
    is a boundary violation, so it is always reported.
    """
    problems: list[str] = []
    for job in jobs or ():
        if not isinstance(job, Mapping):
            continue
        if not _is_lifeboat_job(job) or not job.get("enabled"):
            continue
        deliver = str(job.get("deliver") or "")
        if deliver != topic:
            problems.append(
                f"{job.get('name')!r} delivers to {deliver!r} rather than the Life-Boat topic"
            )
    return tuple(problems)


def job_residency_problems(jobs: Any, *, store: str, topic: str) -> tuple[str, ...]:
    """Return Life-Boat jobs sitting in the wrong profile's cron store.

    A job runs as the profile whose store it lives in, and inherits that
    profile's skills and credentials. The nightly evidence summary asked for
    three skills that exist only under the Life-Boat profile while sitting in
    the base store, so it ran as the default profile, found none of them, and
    announced that in the middle of a support conversation.

    Judged by destination as well as by name: anything delivering into the
    Life-Boat topic belongs to the Life-Boat profile whatever it is called.
    """
    if str(store) == "life-advisor":
        return ()

    problems: list[str] = []
    for job in jobs or ():
        if not isinstance(job, Mapping):
            continue
        # Judged by name only. A job from another subsystem that happens to
        # alert into this topic needs its own profile's skills, so relocating
        # it would break it; whether it should be aimed here at all is a
        # separate question, and one for the user rather than a checker.
        if _is_lifeboat_job(job):
            problems.append(
                f"{job.get('name')!r} lives in the {store} cron store but belongs to "
                "the Life-Boat profile, whose skills and topic it uses"
            )
    return tuple(problems)
