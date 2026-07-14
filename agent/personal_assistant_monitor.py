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
import sys
from typing import Any, Iterator, Mapping
import urllib.error
from uuid import uuid4
from zoneinfo import ZoneInfo

from utils import atomic_json_write


SCHEMA_VERSION = 2
EVENT_VERSION = 1
LEASE_TTL = timedelta(minutes=10)
ACKED_HISTORY_LIMIT = 128
MAX_DELIVERY_ATTEMPTS = 3
RETRY_BACKOFF = timedelta(seconds=30)
SETTLED_DISPOSITIONS = frozenset({"handled", "merged", "suppressed"})
TERMINAL_STATUSES = SETTLED_DISPOSITIONS | {"dead_letter"}
JERUSALEM = ZoneInfo("Asia/Jerusalem")


def _monitor_dir(profile_home: Path) -> Path:
    return Path(profile_home) / "state" / "personal-assistant-monitor"


def record_monitor_health(
    profile_home: Path,
    *,
    component: str,
    event: str,
    status: str,
    count: int = 0,
    now: datetime | None = None,
) -> None:
    """Append a privacy-safe producer/consumer heartbeat for the live watchdog."""
    def safe(value: Any) -> str:
        return "".join(
            char for char in str(value).lower() if char.isalnum() or char in "-_"
        )[:64]

    row = {
        "ts": (now or datetime.now(timezone.utc)).isoformat(),
        "component": "personal_assistant_monitor",
        "source": safe(component) or "unknown",
        "event": safe(event) or "heartbeat",
        "status": safe(status) or "unknown",
        "count": max(0, int(count)),
    }
    path = Path(profile_home) / "logs" / "personal-assistant-monitor.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    except OSError:
        # Monitoring must never make the task queue less reliable.
        return


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
        "scheduledTime", "duration", "progress", "blocked", "blocker", "projectId",
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


def _event_subject(kind: str, evidence: Mapping[str, Any]) -> str:
    task_id = str(evidence.get("taskId") or evidence.get("id") or "").strip()
    if task_id:
        subject = f"task:{task_id}"
    elif evidence.get("localDate"):
        subject = f"schedule:{evidence['localDate']}"
    elif kind == "deadline_risk":
        subject = "task-pressure:overdue"
    elif kind == "material_schedule_drift":
        subject = "schedule:drift"
    else:
        subject = f"global:{kind}"
    return subject[:200]


def _event(
    kind: str,
    evidence: Mapping[str, Any],
    now: datetime,
    occurrence: int,
    subject: str | None = None,
) -> dict[str, Any]:
    subject = (str(subject).strip()[:200] if subject else _event_subject(kind, evidence))
    canonical = json.dumps(
        {
            "version": EVENT_VERSION,
            "kind": kind,
            "subject": subject,
            "evidence": evidence,
            "occurrence": occurrence,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    event_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return {
        "id": event_id,
        "version": EVENT_VERSION,
        "kind": kind,
        "subject": subject,
        "occurrence": occurrence,
        "evidence": dict(evidence),
        "created_at": now.isoformat(),
        "lifecycle_version": SCHEMA_VERSION,
        "status": "pending",
        "attempts": 0,
        "available_at": now.isoformat(),
        "disposition": None,
        "last_error": None,
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

    def emit(kind: str, evidence: Mapping[str, Any], subject: str | None = None) -> None:
        subject = (str(subject).strip()[:200] if subject else _event_subject(kind, evidence))
        occurrence_key = f"{kind}:{subject}"
        prior = occurrences.get(occurrence_key)
        if prior is None and kind in {
            "deadline_risk",
            "material_schedule_drift",
            "scheduled_assessment",
        }:
            prior = occurrences.get(kind, 0)
        occurrence = int(prior or 0) + 1
        occurrences[occurrence_key] = occurrence
        events.append(_event(kind, evidence, now, occurrence, subject))

    before_overdue = previous.get("taskPressure", {}).get("overdue", 0)
    after_overdue = current.get("taskPressure", {}).get("overdue", 0)
    if after_overdue > before_overdue:
        emit("deadline_risk", {"overdue": after_overdue})

    before_drift = previous.get("scheduleDriftMinutes", 0)
    after_drift = current.get("scheduleDriftMinutes", 0)
    crossed_drift_threshold = abs(before_drift) < 30 <= abs(after_drift)
    materially_changed_drift = abs(after_drift) >= 30 and abs(after_drift - before_drift) >= 15
    if crossed_drift_threshold or materially_changed_drift:
        emit("material_schedule_drift", {"minutes": after_drift})

    old_tasks = {str(task["id"]): task for task in previous.get("tasks", [])}

    project_metadata_was_known = not old_tasks or any(
        "projectId" in task for task in old_tasks.values()
    )

    old_uncategorized = {
        task_id
        for task_id, task in old_tasks.items()
        if "projectId" in task and task.get("projectId") in (None, "")
    }
    current_uncategorized = [
        task
        for task in current.get("tasks", [])
        if "projectId" in task and task.get("projectId") in (None, "")
    ]
    if project_metadata_was_known and len(current_uncategorized) > len(old_uncategorized):
        added = [
            {
                "taskId": str(task["id"]),
                "title": str(task.get("title") or "")[:200],
            }
            for task in current_uncategorized
            if str(task["id"]) not in old_uncategorized
        ]
        emit(
            "uncategorized_tasks",
            {
                "count": len(current_uncategorized),
                "added": added,
                "action": "suggest_preview",
            },
            subject="flowstate:uncategorized",
        )

    for task in current.get("tasks", []):
        task_id = str(task["id"])
        old = old_tasks.get(task_id)
        blocked = bool(task.get("blocked") or task.get("blocker"))
        was_blocked = bool(old and (old.get("blocked") or old.get("blocker")))
        if blocked and not was_blocked:
            emit(
                "blocker",
                {"taskId": task_id, "title": task.get("title", "")},
            )
        if old and old.get("priority") != "high" and task.get("priority") == "high":
            emit(
                "changed_high_priority",
                {
                    "taskId": task_id,
                    **{
                        key: task[key]
                        for key in ("title", "status", "dueDate")
                        if key in task
                    },
                },
            )
    return events


def _compact_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep all pending work and a bounded tail of ack tombstones."""

    pending = [event for event in events if event.get("status") not in TERMINAL_STATUSES]
    acked = [event for event in events if event.get("status") in TERMINAL_STATUSES]
    return acked[-ACKED_HISTORY_LIMIT:] + pending


def _normalize_event(raw: Any) -> tuple[dict[str, Any] | None, bool]:
    """Upgrade a persisted v1 queue entry without changing its stable identity."""

    if not isinstance(raw, dict) or not raw.get("id"):
        return None, True
    event = dict(raw)
    lifecycle_fields = {
        "status",
        "attempts",
        "available_at",
        "disposition",
        "last_error",
        "lease",
    }
    changed = event.get("lifecycle_version") != SCHEMA_VERSION or not lifecycle_fields.issubset(event)
    if not event.get("subject"):
        evidence = event.get("evidence") if isinstance(event.get("evidence"), Mapping) else {}
        event["subject"] = _event_subject(str(event.get("kind") or "context_change"), evidence)
        changed = True
    if event.get("acked"):
        event.setdefault("status", "handled")
        event.setdefault("disposition", "handled")
    else:
        event.setdefault("status", "leased" if isinstance(event.get("lease"), dict) else "pending")
        event.setdefault("disposition", None)
    event.setdefault("attempts", 1 if isinstance(event.get("lease"), dict) else 0)
    event.setdefault("available_at", event.get("created_at"))
    event.setdefault("last_error", None)
    event["version"] = EVENT_VERSION
    event["lifecycle_version"] = SCHEMA_VERSION
    event["acked"] = event.get("status") in TERMINAL_STATUSES
    return event, changed


def _load_queue(path: Path) -> tuple[dict[str, Any], bool]:
    raw = _read_json(path, {"version": SCHEMA_VERSION, "events": []})
    changed = raw.get("version") != SCHEMA_VERSION
    events = []
    for value in raw.get("events", []) if isinstance(raw.get("events"), list) else []:
        event, migrated = _normalize_event(value)
        changed = changed or migrated
        if event is not None:
            events.append(event)
    return {"version": SCHEMA_VERSION, "events": events}, changed


def run_monitor_check(
    profile_home: Path,
    assistant_context: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
    connector_failure: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a snapshot and enqueue deterministic candidate events."""

    checked_at = now or datetime.now(timezone.utc)
    with _locked(profile_home) as root:
        state_path = root / "state.json"
        state = _read_json(state_path, {"version": SCHEMA_VERSION})
        if not isinstance(assistant_context, Mapping):
            state["last_checked"] = checked_at.isoformat()
            state["last_status"] = "offline"
            state["connector_error"] = _sanitize_connector_failure(connector_failure)
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
        queue, queue_changed = _load_queue(queue_path)
        events = queue["events"]
        known_ids = {event.get("id") for event in events if isinstance(event, dict)}
        added = [candidate for candidate in candidates if candidate["id"] not in known_ids]
        if added or queue_changed:
            queue["events"] = _compact_events(events + added)
            atomic_json_write(queue_path, queue, mode=0o600, sort_keys=True)

        state.update(
            {
                "version": SCHEMA_VERSION,
                "last_checked": checked_at.isoformat(),
                "last_status": "available",
                "connector_error": None,
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
    expired_dead_letters = 0
    with _locked(profile_home) as root:
        path = root / "queue.json"
        queue, migrated = _load_queue(path)
        queue_changed = migrated
        for event in queue.get("events", []):
            if event.get("status") in TERMINAL_STATUSES:
                continue
            lease = event.get("lease")
            if isinstance(lease, dict):
                try:
                    if datetime.fromisoformat(lease["expires_at"]) > leased_at:
                        continue
                except (KeyError, TypeError, ValueError):
                    pass
                if int(event.get("attempts") or 0) >= MAX_DELIVERY_ATTEMPTS:
                    event["status"] = "dead_letter"
                    event["disposition"] = "dead_letter"
                    event["acked"] = True
                    event["lease"] = None
                    event["last_error"] = _safe_error(
                        {"category": "delivery_failed", "code": "lease_expired"},
                        leased_at,
                    )
                    event["settled_at"] = leased_at.isoformat()
                    expired_dead_letters += 1
                    queue_changed = True
                    continue
            available_at = event.get("available_at")
            if event.get("status") == "retry_wait" and available_at:
                try:
                    if datetime.fromisoformat(str(available_at)) > leased_at:
                        continue
                except ValueError:
                    pass
            lease_id = uuid4().hex
            event["attempts"] = int(event.get("attempts") or 0) + 1
            event["status"] = "leased"
            event["lease"] = {
                "id": lease_id,
                "consumer": consumer,
                "expires_at": (leased_at + lease_ttl).isoformat(),
            }
            atomic_json_write(path, queue, mode=0o600, sort_keys=True)
            if expired_dead_letters:
                record_monitor_health(
                    profile_home,
                    component="consumer",
                    event="dead_letter",
                    status="failed",
                    count=expired_dead_letters,
                    now=leased_at,
                )
            result = dict(event)
            result["lease_id"] = lease_id
            return result
        if queue_changed:
            queue["events"] = _compact_events(queue.get("events", []))
            atomic_json_write(path, queue, mode=0o600, sort_keys=True)
    if expired_dead_letters:
        record_monitor_health(
            profile_home,
            component="consumer",
            event="dead_letter",
            status="failed",
            count=expired_dead_letters,
            now=leased_at,
        )
    return None


def ack_candidate_event(profile_home: Path, event_id: str, lease_id: str) -> bool:
    return settle_candidate_event(profile_home, event_id, lease_id, "handled")


def settle_candidate_event(
    profile_home: Path,
    event_id: str,
    lease_id: str,
    disposition: str,
) -> bool:
    if disposition not in SETTLED_DISPOSITIONS:
        raise ValueError("disposition must be handled|merged|suppressed")
    with _locked(profile_home) as root:
        path = root / "queue.json"
        queue, _ = _load_queue(path)
        for event in queue.get("events", []):
            if event.get("id") != event_id:
                continue
            if event.get("status") in TERMINAL_STATUSES:
                return bool(
                    event.get("disposition") == disposition
                    and (
                        event.get("settled_lease_id") == lease_id
                        or (
                            disposition == "handled"
                            and event.get("settled_lease_id") is None
                        )
                    )
                )
            if event.get("status") != "leased":
                return False
            if event.get("lease", {}).get("id") != lease_id:
                return False
            event["status"] = disposition
            event["disposition"] = disposition
            event["acked"] = True
            event["lease"] = None
            event["settled_lease_id"] = lease_id
            event["settled_at"] = datetime.now(timezone.utc).isoformat()
            queue["events"] = _compact_events(queue.get("events", []))
            atomic_json_write(path, queue, mode=0o600, sort_keys=True)
            return True
    return False


def _safe_error(error: Any, now: datetime) -> dict[str, str]:
    """Retain actionable classification without persisting exception text."""

    if isinstance(error, Mapping):
        raw_category = str(error.get("category") or "delivery_failed")
        raw_code = str(error.get("code") or "unknown")
    else:
        raw_category = "delivery_failed"
        raw_code = type(error).__name__.lower() if isinstance(error, BaseException) else "unknown"

    def safe_slug(value: str, fallback: str) -> str:
        result = "".join(char for char in value.lower() if char.isalnum() or char in "-_")[:64]
        return result or fallback

    return {
        "category": safe_slug(raw_category, "delivery_failed"),
        "code": safe_slug(raw_code, "unknown"),
        "recorded_at": now.isoformat(),
    }


def retry_candidate_event(
    profile_home: Path,
    event_id: str,
    lease_id: str,
    error: Any,
    *,
    now: datetime | None = None,
    backoff: timedelta | None = None,
    max_attempts: int = MAX_DELIVERY_ATTEMPTS,
) -> bool:
    """Release a failed delivery for bounded retry or move it to dead letter."""

    failed_at = now or datetime.now(timezone.utc)
    max_attempts = max(1, int(max_attempts))
    with _locked(profile_home) as root:
        path = root / "queue.json"
        queue, _ = _load_queue(path)
        for event in queue.get("events", []):
            if event.get("id") != event_id:
                continue
            if event.get("status") != "leased":
                return False
            if event.get("lease", {}).get("id") != lease_id:
                return False
            attempts = int(event.get("attempts") or 0)
            event["last_error"] = _safe_error(error, failed_at)
            event["lease"] = None
            if attempts >= max_attempts:
                event["status"] = "dead_letter"
                event["disposition"] = "dead_letter"
                event["acked"] = True
                event["settled_at"] = failed_at.isoformat()
                event["settled_lease_id"] = lease_id
                record_monitor_health(
                    profile_home,
                    component="consumer",
                    event="dead_letter",
                    status="failed",
                    count=1,
                    now=failed_at,
                )
            else:
                delay = backoff
                if delay is None:
                    delay = RETRY_BACKOFF * (2 ** max(0, attempts - 1))
                if delay < timedelta(0):
                    raise ValueError("backoff must not be negative")
                event["status"] = "retry_wait"
                event["disposition"] = None
                event["acked"] = False
                event["available_at"] = (failed_at + delay).isoformat()
            queue["events"] = _compact_events(queue.get("events", []))
            atomic_json_write(path, queue, mode=0o600, sort_keys=True)
            return True
    return False


def defer_candidate_event(
    profile_home: Path,
    event_id: str,
    lease_id: str,
    *,
    now: datetime | None = None,
    delay: timedelta = timedelta(seconds=5),
) -> bool:
    """Release an event observed during a busy turn without spending a retry."""
    deferred_at = now or datetime.now(timezone.utc)
    if delay < timedelta(0):
        raise ValueError("delay must not be negative")
    with _locked(profile_home) as root:
        path = root / "queue.json"
        queue, _ = _load_queue(path)
        for event in queue.get("events", []):
            if event.get("id") != event_id:
                continue
            if event.get("status") != "leased":
                return False
            if event.get("lease", {}).get("id") != lease_id:
                return False
            event["attempts"] = max(0, int(event.get("attempts") or 0) - 1)
            event["status"] = "retry_wait"
            event["disposition"] = None
            event["available_at"] = (deferred_at + delay).isoformat()
            event["last_error"] = None
            event["lease"] = None
            event["acked"] = False
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
    connector_failure: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one CLI check and best-effort notify only for newly queued work."""

    result = run_monitor_check(
        profile_home,
        assistant_context,
        now=now,
        connector_failure=connector_failure,
    )
    failure = _sanitize_connector_failure(connector_failure)
    record_monitor_health(
        profile_home,
        component="producer",
        event="connector_failure" if result["status"] == "offline" else "producer_check",
        status=failure["category"] if result["status"] == "offline" else "available",
        count=int(result.get("candidate_count") or 0),
        now=now,
    )
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


class ConnectorResponseError(ValueError):
    pass


def _sanitize_connector_failure(value: Mapping[str, Any] | None) -> dict[str, str]:
    raw = value if isinstance(value, Mapping) else {}

    def slug(key: str, fallback: str) -> str:
        text = str(raw.get(key) or fallback).lower()
        clean = "".join(char for char in text if char.isalnum() or char in "-_")[:64]
        return clean or fallback

    category = slug("category", "unavailable")
    code = slug("code", category)
    safe_details = {
        "authentication": "FlowState rejected monitor authentication.",
        "not_signed_in": "FlowState is running without a signed-in user.",
        "signed_out": "FlowState is signed out.",
        "timeout": "FlowState did not respond before the monitor timeout.",
        "connection_refused": "The FlowState Local Task API refused the connection.",
        "invalid_response": "FlowState returned a response the monitor could not validate.",
        "unavailable": "The FlowState Local Task API is unavailable.",
    }
    return {
        "category": category,
        "code": code,
        "detail": safe_details.get(category, "The FlowState connector check failed."),
    }


def classify_connector_failure(exc: BaseException) -> dict[str, str]:
    status = getattr(exc, "status", None)
    code = str(getattr(exc, "code", "") or "").lower()
    if status == 401 or code in {"unauthorized", "authentication", "invalid_token"}:
        return _sanitize_connector_failure(
            {"category": "authentication", "code": code or "unauthorized"}
        )
    if code == "signed_out":
        return _sanitize_connector_failure(
            {"category": "signed_out", "code": "signed_out"}
        )
    if status == 503 or code == "not_signed_in":
        return _sanitize_connector_failure(
            {"category": "not_signed_in", "code": code or "not_signed_in"}
        )
    if isinstance(exc, (TimeoutError,)):
        return _sanitize_connector_failure({"category": "timeout", "code": "timeout"})
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return _sanitize_connector_failure({"category": "timeout", "code": "timeout"})
        if isinstance(reason, ConnectionRefusedError):
            return _sanitize_connector_failure(
                {"category": "connection_refused", "code": "connection_refused"}
            )
        return _sanitize_connector_failure({"category": "unavailable", "code": "url_error"})
    if isinstance(exc, (json.JSONDecodeError, ConnectorResponseError)):
        code = "invalid_json" if isinstance(exc, json.JSONDecodeError) else "invalid_shape"
        return _sanitize_connector_failure({"category": "invalid_response", "code": code})
    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, BaseException) and cause is not exc:
        return classify_connector_failure(cause)
    message = str(exc).lower()
    if "timed out" in message:
        return _sanitize_connector_failure({"category": "timeout", "code": "timeout"})
    if "non-json" in message or "unexpected response" in message:
        return _sanitize_connector_failure(
            {"category": "invalid_response", "code": "invalid_shape"}
        )
    return _sanitize_connector_failure({"category": "unavailable", "code": "connector_error"})


def _is_temporary_connector_failure(exc: BaseException) -> bool:
    """Separate expected connector downtime from monitor implementation bugs."""

    if getattr(exc, "status", None) in {401, 503}:
        return True
    if str(getattr(exc, "code", "") or "").lower() in {
        "unauthorized",
        "authentication",
        "invalid_token",
        "not_signed_in",
        "signed_out",
    }:
        return True
    if isinstance(
        exc,
        (TimeoutError, urllib.error.URLError, json.JSONDecodeError, ConnectorResponseError),
    ):
        return True
    cause = getattr(exc, "__cause__", None)
    return (
        isinstance(cause, BaseException)
        and cause is not exc
        and _is_temporary_connector_failure(cause)
    )


def fetch_flowstate_context(request=None) -> dict[str, Any]:
    if request is None:
        from tools.flowstate_tool import _request

        request = _request
    context = request("GET", "/api/assistant/context", allow_stale_cache=False)
    task_payload = request(
        "GET", "/api/tasks?status=open&limit=25", allow_stale_cache=False
    )
    if not isinstance(context, Mapping) or not isinstance(task_payload, Mapping):
        raise ConnectorResponseError("invalid FlowState response shape")
    tasks = task_payload.get("tasks")
    if not isinstance(tasks, list):
        raise ConnectorResponseError("invalid FlowState task response shape")
    return {**dict(context), "tasks": tasks}


def main(argv: list[str] | None = None, *, fetch_context=None) -> int:
    parser = argparse.ArgumentParser(description="Check FlowState for personal-assistant change candidates")
    parser.add_argument("--profile-home", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        context = (fetch_context or fetch_flowstate_context)()
        if not isinstance(context, Mapping):
            raise ConnectorResponseError("invalid FlowState response shape")
    except Exception as exc:
        failure = classify_connector_failure(exc)
        run_cli_monitor_check(
            args.profile_home,
            None,
            connector_failure=failure,
        )
        print(json.dumps({"status": "offline", "connector_error": failure}), file=sys.stderr)
        return 75 if _is_temporary_connector_failure(exc) else 1
    result = run_cli_monitor_check(args.profile_home, context)
    return 0 if result["status"] == "checked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
