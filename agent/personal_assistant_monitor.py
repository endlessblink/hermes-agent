"""Deterministic, profile-scoped FlowState change monitor.

The monitor performs no model calls.  It stores only task metadata needed to
detect consequence or plan changes and queues candidate events for a gateway
to evaluate later.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Iterator, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

from utils import atomic_json_write


SCHEMA_VERSION = 1
LEASE_TTL = timedelta(minutes=10)
ACKED_HISTORY_LIMIT = 128
JERUSALEM = ZoneInfo("Asia/Jerusalem")


def _monitor_dir(profile_home: Path) -> Path:
    return Path(profile_home) / "state" / "personal-assistant-monitor"


@contextmanager
def _locked(profile_home: Path) -> Iterator[Path]:
    root = _monitor_dir(profile_home)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "monitor.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield root
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except (OSError, ValueError, TypeError):
        return default


def _task_metadata(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    allowed = (
        "id", "title", "status", "priority", "dueDate", "scheduledDate",
        "scheduledTime", "duration", "progress", "blocked", "blocker",
    )
    tasks = []
    for value in raw[:100]:
        if not isinstance(value, Mapping) or not value.get("id"):
            continue
        task = {key: value[key] for key in allowed if key in value}
        tasks.append(task)
    return sorted(tasks, key=lambda task: str(task["id"]))


def _normalize_context(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = raw.get("result", raw)
    if not isinstance(payload, Mapping):
        return {}
    pressure = payload.get("taskPressure")
    safe_pressure = {}
    if isinstance(pressure, Mapping):
        for key in ("overdue", "dueToday", "dueSoon"):
            if isinstance(pressure.get(key), (int, float)):
                safe_pressure[key] = pressure[key]
    drift = payload.get("scheduleDriftMinutes")
    return {
        "taskPressure": safe_pressure,
        "scheduleDriftMinutes": drift if isinstance(drift, (int, float)) else 0,
        "tasks": _task_metadata(payload.get("tasks")),
    }


def _event(
    kind: str,
    evidence: Mapping[str, Any],
    now: datetime,
    occurrence: int,
) -> dict[str, Any]:
    canonical = json.dumps(
        {
            "version": SCHEMA_VERSION,
            "kind": kind,
            "evidence": evidence,
            "occurrence": occurrence,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    event_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return {
        "id": event_id,
        "version": SCHEMA_VERSION,
        "kind": kind,
        "occurrence": occurrence,
        "evidence": dict(evidence),
        "created_at": now.isoformat(),
        "lease": None,
        "acked": False,
    }


def _candidate_events(
    previous: dict[str, Any],
    current: dict[str, Any],
    now: datetime,
    occurrences: dict[str, int],
) -> list[dict[str, Any]]:
    events = []

    def emit(kind: str, evidence: Mapping[str, Any]) -> None:
        occurrence = occurrences.get(kind, 0) + 1
        occurrences[kind] = occurrence
        events.append(_event(kind, evidence, now, occurrence))

    before_overdue = previous.get("taskPressure", {}).get("overdue", 0)
    after_overdue = current.get("taskPressure", {}).get("overdue", 0)
    if after_overdue > before_overdue:
        emit("deadline_risk", {"overdue": after_overdue})

    before_drift = previous.get("scheduleDriftMinutes", 0)
    after_drift = current.get("scheduleDriftMinutes", 0)
    if abs(after_drift) >= 30 and after_drift != before_drift:
        emit("material_schedule_drift", {"minutes": after_drift})

    old_tasks = {str(task["id"]): task for task in previous.get("tasks", [])}
    for task in current.get("tasks", []):
        task_id = str(task["id"])
        old = old_tasks.get(task_id)
        blocked = bool(task.get("blocked") or task.get("blocker"))
        was_blocked = bool(old and (old.get("blocked") or old.get("blocker")))
        if blocked and not was_blocked:
            emit("blocker", {"taskId": task_id, "title": task.get("title", "")})
        if task.get("priority") == "high" and task != old:
            emit(
                "changed_high_priority",
                {key: task[key] for key in ("id", "title", "status", "dueDate") if key in task},
            )
    return events


def _compact_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep all pending work and a bounded tail of ack tombstones."""

    pending = [event for event in events if not event.get("acked")]
    acked = [event for event in events if event.get("acked")]
    return acked[-ACKED_HISTORY_LIMIT:] + pending


def run_monitor_check(
    profile_home: Path,
    assistant_context: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist a snapshot and enqueue deterministic candidate events."""

    checked_at = now or datetime.now(timezone.utc)
    with _locked(profile_home) as root:
        state_path = root / "state.json"
        state = _read_json(state_path, {"version": SCHEMA_VERSION})
        if not isinstance(assistant_context, Mapping):
            state["last_checked"] = checked_at.isoformat()
            state["last_status"] = "offline"
            atomic_json_write(state_path, state, mode=0o600, sort_keys=True)
            return {"status": "offline", "candidate_count": 0}

        current = _normalize_context(assistant_context)
        previous = state.get("snapshot")
        raw_occurrences = state.get("occurrences", {})
        occurrences = (
            {str(key): int(value) for key, value in raw_occurrences.items() if isinstance(value, int)}
            if isinstance(raw_occurrences, dict)
            else {}
        )
        candidates = (
            []
            if not isinstance(previous, dict)
            else _candidate_events(previous, current, checked_at, occurrences)
        )
        local_now = checked_at.astimezone(JERUSALEM)
        assessment_dates = state.get("scheduled_assessment_dates", [])
        if not isinstance(assessment_dates, list):
            assessment_dates = []
        local_date = local_now.date().isoformat()
        if local_now.hour >= 9 and local_date not in assessment_dates:
            occurrence = occurrences.get("scheduled_assessment", 0) + 1
            occurrences["scheduled_assessment"] = occurrence
            candidates.append(
                _event(
                    "scheduled_assessment",
                    {"localDate": local_date},
                    checked_at,
                    occurrence,
                )
            )
            assessment_dates = (assessment_dates + [local_date])[-14:]
        queue_path = root / "queue.json"
        queue = _read_json(queue_path, {"version": SCHEMA_VERSION, "events": []})
        events = queue.get("events") if isinstance(queue.get("events"), list) else []
        known_ids = {event.get("id") for event in events if isinstance(event, dict)}
        added = [candidate for candidate in candidates if candidate["id"] not in known_ids]
        if added:
            queue["events"] = _compact_events(events + added)
            atomic_json_write(queue_path, queue, mode=0o600, sort_keys=True)

        state.update(
            {
                "version": SCHEMA_VERSION,
                "last_checked": checked_at.isoformat(),
                "last_status": "available",
                "snapshot": current,
                "occurrences": occurrences,
                "scheduled_assessment_dates": assessment_dates,
            }
        )
        atomic_json_write(state_path, state, mode=0o600, sort_keys=True)
        return {"status": "checked", "candidate_count": len(added)}


def lease_candidate_event(
    profile_home: Path,
    consumer: str,
    *,
    now: datetime | None = None,
    lease_ttl: timedelta = LEASE_TTL,
) -> dict[str, Any] | None:
    leased_at = now or datetime.now(timezone.utc)
    with _locked(profile_home) as root:
        path = root / "queue.json"
        queue = _read_json(path, {"version": SCHEMA_VERSION, "events": []})
        for event in queue.get("events", []):
            if event.get("acked"):
                continue
            lease = event.get("lease")
            if isinstance(lease, dict):
                try:
                    if datetime.fromisoformat(lease["expires_at"]) > leased_at:
                        continue
                except (KeyError, TypeError, ValueError):
                    pass
            lease_id = uuid4().hex
            event["lease"] = {
                "id": lease_id,
                "consumer": consumer,
                "expires_at": (leased_at + lease_ttl).isoformat(),
            }
            atomic_json_write(path, queue, mode=0o600, sort_keys=True)
            result = dict(event)
            result["lease_id"] = lease_id
            return result
    return None


def ack_candidate_event(profile_home: Path, event_id: str, lease_id: str) -> bool:
    with _locked(profile_home) as root:
        path = root / "queue.json"
        queue = _read_json(path, {"version": SCHEMA_VERSION, "events": []})
        for event in queue.get("events", []):
            if event.get("id") != event_id:
                continue
            if event.get("acked"):
                return True
            if event.get("lease", {}).get("id") != lease_id:
                return False
            event["acked"] = True
            event["lease"] = None
            queue["events"] = _compact_events(queue.get("events", []))
            atomic_json_write(path, queue, mode=0o600, sort_keys=True)
            return True
    return False


def _desktop_notify(title: str, body: str) -> None:
    executable = shutil.which("notify-send")
    if not executable:
        return
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    try:
        subprocess.run(
            [executable, "--app-name=Hermes", title, body],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except Exception:
        return


def run_cli_monitor_check(
    profile_home: Path,
    assistant_context: Mapping[str, Any] | None,
    *,
    notifier=None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one CLI check and best-effort notify only for newly queued work."""

    result = run_monitor_check(profile_home, assistant_context, now=now)
    if result["candidate_count"]:
        notify = notifier or _desktop_notify
        try:
            notify(
                "Hermes personal assistant",
                "Personal assistant noticed a material change and will prepare it in Hermes.",
            )
        except Exception:
            pass
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Check FlowState for personal-assistant change candidates")
    parser.add_argument("--profile-home", required=True, type=Path)
    args = parser.parse_args()
    try:
        from tools.flowstate_tool import _request

        context = _request("GET", "/api/assistant/context")
        tasks = _request("GET", "/api/tasks?status=open&limit=25").get("tasks", [])
        context = {**context, "tasks": tasks}
    except Exception:
        context = None
    result = run_cli_monitor_check(args.profile_home, context)
    return 0 if result["status"] in {"checked", "offline"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
