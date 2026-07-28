"""Calendar-first safety contract for Personal Assistant planning reads."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


CALENDAR_PREFLIGHT_TTL = timedelta(minutes=15)
_CALENDAR_GATED_TOOLS: frozenset[str] = frozenset()
_PLANNING_TASK_READ_TOOLS = frozenset(
    {"flowstate_list_tasks", "flowstate_search_tasks", "flowstate_get_task"}
)
_calendar_first_turn: ContextVar[
    tuple[bool, bool, frozenset[str], bool, tuple[tuple[str, str, str, str, int | None], ...]]
] = ContextVar(
    "personal_assistant_calendar_first_turn", default=(False, False, frozenset(), False, ())
)
_calendar_first_task_details: ContextVar[tuple[tuple[str, str], ...]] = ContextVar(
    "personal_assistant_calendar_first_task_details", default=()
)
_calendar_first_candidate_records: ContextVar[tuple[tuple[str, str], ...]] = ContextVar(
    "personal_assistant_calendar_first_candidate_records", default=()
)
_same_day_grounding_required: ContextVar[bool] = ContextVar(
    "personal_assistant_same_day_grounding_required", default=False
)
_task_fact_correction_required: ContextVar[bool] = ContextVar(
    "personal_assistant_task_fact_correction_required", default=False
)
_TASK_FACT_CORRECTION_TOOLS = frozenset({"flowstate_get_task", "flowstate_update_task"})
_CLOSED_FAILURE_CHOICES = (
    {"id": "retry", "label": "Retry calendar check"},
    {"id": "repair-calendar", "label": "Repair Calendar connection"},
    {"id": "cancel", "label": "Cancel planning"},
)


def _native_gws_config_dir() -> Path:
    configured = os.getenv("GOOGLE_WORKSPACE_CLI_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    try:
        import pwd

        get_uid = getattr(os, "getuid")
        system_home = Path(pwd.getpwuid(get_uid()).pw_dir)
    except (AttributeError, ImportError, KeyError, OSError):
        system_home = Path.home()
    return system_home / ".config" / "gws"


def calendar_receipt_is_fresh_complete(
    receipt: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> bool:
    if not isinstance(receipt, Mapping):
        return False
    if receipt.get("status") != "complete" or receipt.get("complete") is not True:
        return False
    try:
        expires_at = datetime.fromisoformat(str(receipt.get("expiresAt") or ""))
        current = now or datetime.now(timezone.utc)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return expires_at > current
    except (TypeError, ValueError):
        return False


def calendar_receipt_covers(
    receipt: Mapping[str, Any] | None,
    *,
    start_date: str,
    end_date: str,
    timezone_name: str = "Asia/Jerusalem",
) -> bool:
    if not isinstance(receipt, Mapping) or receipt.get("timezone") != timezone_name:
        return False
    covered_range = receipt.get("range")
    return bool(
        isinstance(covered_range, Mapping)
        and covered_range.get("startDate") == start_date
        and covered_range.get("endDate") == end_date
    )


def calendar_preflight_gate(
    tool_name: str,
    receipt: Mapping[str, Any] | None,
    *,
    tool_args: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return a typed closed-interaction error when a planning read is premature."""

    normalized_tool_name = str(tool_name or "").strip().lower()
    calendar_required, calendar_complete, _, _, _ = _calendar_first_turn.get()
    grounding_message = same_day_grounding_gate_message(normalized_tool_name)
    if calendar_required and grounding_message:
        return {
            "error": grounding_message,
            "error_type": "same_day_grounding_required",
            "requiredTool": "personal_assistant_interview_start",
        }
    if (
        calendar_required
        and not calendar_complete
        and normalized_tool_name in _PLANNING_TASK_READ_TOOLS
        and not (
            _task_fact_correction_required.get()
            and normalized_tool_name in _TASK_FACT_CORRECTION_TOOLS
        )
    ):
        return {
            "error": "This planning turn must check Calendar before reading tasks.",
            "error_type": "calendar_preflight_required_this_turn",
            "requiredTool": "personal_assistant_calendar_preflight",
        }
    if normalized_tool_name not in _CALENDAR_GATED_TOOLS:
        return None
    if calendar_receipt_is_fresh_complete(receipt, now=now):
        planning_date = str((tool_args or {}).get("planningDate") or "").strip()
        if not planning_date:
            return None
        try:
            required_end = (date.fromisoformat(planning_date) + timedelta(days=1)).isoformat()
        except ValueError:
            required_end = ""
        if required_end and calendar_receipt_covers(
            receipt,
            start_date=planning_date,
            end_date=required_end,
        ):
            return None
        return {
            "error": "The Calendar check does not cover this planning date and timezone.",
            "error_type": "calendar_preflight_scope_mismatch",
            "requiredTool": "personal_assistant_calendar_preflight",
            "requiredRange": {
                "startDate": planning_date,
                "endDate": required_end,
                "timezone": "Asia/Jerusalem",
            },
            "requiredInteraction": {
                "type": "single-choice",
                "question": "Calendar must be checked for this date. What should Hermes do?",
                "choices": list(_CLOSED_FAILURE_CHOICES),
                "allowCustomAnswer": False,
            },
        }
    return {
        "error": "A complete fresh Calendar check is required before planning data can be read.",
        "error_type": "calendar_preflight_required",
        "requiredTool": "personal_assistant_calendar_preflight",
        "requiredInteraction": {
            "type": "single-choice",
            "question": "Calendar could not be fully checked. What should Hermes do?",
            "choices": list(_CLOSED_FAILURE_CHOICES),
            "allowCustomAnswer": False,
        },
    }


def same_day_grounding_gate_message(tool_name: str) -> str | None:
    """Require durable planning input instead of a transient clarify prompt."""

    normalized = str(tool_name or "").strip().lower()
    if not _same_day_grounding_required.get() or normalized in {
        "personal_assistant_get_state",
        "personal_assistant_calendar_preflight",
        "personal_assistant_interview_start",
    } or (
        _task_fact_correction_required.get()
        and normalized in _TASK_FACT_CORRECTION_TOOLS
    ):
        return None
    return (
        "Today's planning context is not ready. First call personal_assistant_calendar_preflight for the exact "
        "requested date, then call personal_assistant_interview_start with mode=daily-grounding and include the "
        "calendar receipt in sourceSnapshot. Render one durable question; do not use clarify or inspect task sources first."
    )


def begin_calendar_first_planning_turn(
    *,
    required: bool,
    same_day_grounding_required: bool = False,
    task_fact_correction_required: bool = False,
) -> None:
    """Start a fresh per-request calendar-first contract in this execution context."""

    _calendar_first_turn.set((bool(required), False, frozenset(), False, ()))
    _calendar_first_task_details.set(())
    _calendar_first_candidate_records.set(())
    _same_day_grounding_required.set(bool(required and same_day_grounding_required))
    _task_fact_correction_required.set(bool(required and task_fact_correction_required))


def calendar_first_planning_turn_active() -> bool:
    """Return whether the current execution context is a planning turn."""

    required, _, _, _, _ = _calendar_first_turn.get()
    return required


def complete_calendar_first_planning_turn(*, complete: bool) -> None:
    """Unlock planning task reads only after this request's preflight completed."""

    required, _, task_ids, inventory_complete, task_records = _calendar_first_turn.get()
    _calendar_first_turn.set(
        (required, bool(complete), task_ids, inventory_complete, task_records)
    )


def record_calendar_first_task_inventory(payload: Mapping[str, Any] | None) -> None:
    """Record only a complete, fresh full inventory read from this planning turn."""

    required, calendar_complete, existing_ids, already_complete, existing_records = (
        _calendar_first_turn.get()
    )
    if already_complete or not isinstance(payload, Mapping) or payload.get("complete") is not True:
        return
    items = payload.get("items")
    if not isinstance(items, list):
        return
    task_ids = frozenset(
        str(item.get("id") or "").strip()
        for item in items
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    )
    total = payload.get("total")
    complete = isinstance(total, int) and not isinstance(total, bool) and total == len(task_ids)
    def inventory_duration(item: Mapping[str, Any]) -> int | None:
        duration = item.get("estimatedDuration")
        if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
            return duration
        due_date = str(item.get("dueDate") or "").strip()
        for instance in item.get("instances") or []:
            if not isinstance(instance, Mapping):
                continue
            candidate = instance.get("duration")
            if (
                str(instance.get("scheduledDate") or "").strip() == due_date
                and isinstance(candidate, int)
                and not isinstance(candidate, bool)
                and candidate > 0
            ):
                return candidate
        return None

    task_records = tuple(
        (
            str(item.get("id") or "").strip(),
            str(item.get("title") or "").strip(),
            str(item.get("dueDate") or "").strip(),
            str(item.get("priority") or "").strip().lower(),
            inventory_duration(item),
        )
        for item in items
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    )
    _calendar_first_turn.set(
        (
            required,
            calendar_complete,
            task_ids if complete else existing_ids,
            complete,
            task_records if complete else existing_records,
        )
    )
    if complete:
        record_calendar_first_candidate_inventory("flowstate", payload)


def calendar_first_task_inventory() -> tuple[frozenset[str], bool]:
    _, _, task_ids, complete, _ = _calendar_first_turn.get()
    return task_ids, complete


def complete_inventory_repeat_gate(tool_name: str) -> dict[str, Any] | None:
    """Prevent a planning turn from reloading an already-complete full inventory."""

    if tool_name != "flowstate_list_tasks":
        return None
    task_ids, complete = calendar_first_task_inventory()
    if not complete:
        return None
    return {
        "error": (
            "The complete FlowState inventory is already available in this planning turn. "
            "Use that result and call flowstate_get_task only for shortlisted tasks; do not list it again."
        ),
        "error_type": "complete_inventory_already_available",
        "complete": True,
        "total": len(task_ids),
    }


def calendar_first_task_records() -> dict[str, dict[str, Any]]:
    """Return immutable-turn task identity fields used to verify displayed recommendations."""

    _, _, _, complete, records = _calendar_first_turn.get()
    if not complete:
        return {}
    return {
        task_id: {
            "title": title,
            "dueDate": due_date,
            "priority": priority,
            "estimatedDuration": estimated_duration,
        }
        for task_id, title, due_date, priority, estimated_duration in records
    }


def _candidate_duration(source_id: str, item: Mapping[str, Any]) -> int | None:
    duration = item.get("estimatedDuration")
    if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
        return duration
    if source_id != "flowstate":
        return None
    due_date = str(item.get("dueDate") or "").strip()
    for instance in item.get("instances") or []:
        if not isinstance(instance, Mapping):
            continue
        candidate = instance.get("duration")
        if (
            str(instance.get("scheduledDate") or "").strip() == due_date
            and isinstance(candidate, int)
            and not isinstance(candidate, bool)
            and candidate > 0
        ):
            return candidate
    return None


def _normalized_candidate_record(
    source_id: str, item: Mapping[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    item_id = str(
        item.get("id")
        or item.get("taskId")
        or item.get("canonicalId")
        or ""
    ).strip()
    title = str(item.get("title") or item.get("name") or "").strip()
    if not item_id or not title:
        return None
    return (
        item_id,
        {
            "id": item_id,
            "sourceId": source_id,
            "title": title,
            "status": str(item.get("status") or "").strip().lower() or None,
            "priority": str(item.get("priority") or "").strip().lower() or None,
            "dueDate": str(
                item.get("dueDate") or item.get("due") or item.get("scheduledDate") or ""
            ).strip()
            or None,
            "estimatedDuration": _candidate_duration(source_id, item),
        },
    )


def _notion_property_text(prop: Mapping[str, Any]) -> str:
    prop_type = str(prop.get("type") or "").strip()
    values = prop.get(prop_type)
    if not isinstance(values, list):
        return ""
    return "".join(
        str(
            value.get("plain_text")
            or ((value.get("text") or {}).get("content") if isinstance(value.get("text"), Mapping) else "")
            or ""
        )
        for value in values
        if isinstance(value, Mapping)
    ).strip()


def _normalized_notion_candidate(
    source_id: str, page: Mapping[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    page_id = str(page.get("id") or "").strip()
    properties = page.get("properties")
    if not page_id or not isinstance(properties, Mapping):
        return None

    title = ""
    due_date = None
    priority = None
    status = None
    completed = False
    for name, raw_prop in properties.items():
        if not isinstance(raw_prop, Mapping):
            continue
        prop_type = str(raw_prop.get("type") or "").strip()
        folded_name = str(name or "").strip().casefold()
        if prop_type == "title" and not title:
            title = _notion_property_text(raw_prop)
        elif prop_type == "date" and due_date is None:
            date_value = raw_prop.get("date")
            if isinstance(date_value, Mapping):
                due_date = str(date_value.get("start") or "").strip() or None
        elif prop_type == "checkbox":
            checked = raw_prop.get("checkbox") is True
            if folded_name in {"done", "completed", "הושלם", "בוצע"}:
                completed = checked
        elif prop_type in {"select", "status"}:
            selection = raw_prop.get(prop_type)
            selection_name = (
                str(selection.get("name") or "").strip()
                if isinstance(selection, Mapping)
                else ""
            )
            if any(token in folded_name for token in ("priority", "דחיפות", "עדיפות")):
                normalized_priority = selection_name.casefold()
                if normalized_priority in {"high", "גבוה", "גבוהה"}:
                    priority = "high"
                elif normalized_priority in {"medium", "בינוני", "בינונית"}:
                    priority = "medium"
                elif normalized_priority in {"low", "נמוך", "נמוכה"}:
                    priority = "low"
            elif any(token in folded_name for token in ("status", "סטטוס")):
                status = selection_name.casefold() or None

    if not title:
        return None
    if completed:
        status = "done"
    elif status is None:
        status = "open"
    return (
        page_id,
        {
            "id": page_id,
            "sourceId": source_id,
            "title": title,
            "status": status,
            "priority": priority,
            "dueDate": due_date,
            "estimatedDuration": None,
        },
    )


def record_calendar_first_candidate_inventory(
    source_id: str, payload: Mapping[str, Any] | None
) -> None:
    """Record normalized planning candidates for a complete inventory source read."""

    safe_source_id = str(source_id or "").strip()
    if not safe_source_id or not isinstance(payload, Mapping):
        return
    notion_query = payload.get("query")
    notion_results = (
        notion_query.get("results")
        if isinstance(notion_query, Mapping)
        and payload.get("ok") is True
        and notion_query.get("has_more") is False
        else None
    )
    notion_payload = isinstance(notion_results, list)
    if not notion_payload and payload.get("complete") is not True:
        return
    items = notion_results if notion_payload else payload.get("items")
    if not isinstance(items, list):
        return

    normalized: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        record = (
            _normalized_notion_candidate(safe_source_id, item)
            if notion_payload
            else _normalized_candidate_record(safe_source_id, item)
        )
        if record is None:
            continue
        item_id, candidate = record
        normalized[item_id] = candidate
    total = len(items) if notion_payload else payload.get("total")
    if (
        isinstance(total, int)
        and not isinstance(total, bool)
        and total != len(normalized)
    ):
        return

    captured = dict(_calendar_first_candidate_records.get())
    captured[safe_source_id] = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    _calendar_first_candidate_records.set(tuple(captured.items()))


def calendar_first_candidate_records() -> dict[str, dict[str, dict[str, Any]]]:
    """Return normalized per-source planning candidates read in this turn."""

    result: dict[str, dict[str, dict[str, Any]]] = {}
    for source_id, serialized in _calendar_first_candidate_records.get():
        try:
            records = json.loads(serialized)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(records, dict):
            result[source_id] = {
                item_id: record
                for item_id, record in records.items()
                if isinstance(item_id, str) and isinstance(record, dict)
            }
    return result


def record_calendar_first_task_detail(payload: Mapping[str, Any] | None) -> None:
    """Record a fresh full task read made during this planning turn."""

    if not isinstance(payload, Mapping):
        return
    task = payload.get("task")
    if not isinstance(task, Mapping):
        return
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        return
    serialized = json.dumps(dict(task), ensure_ascii=False, sort_keys=True)
    details = dict(_calendar_first_task_details.get())
    details[task_id] = serialized
    _calendar_first_task_details.set(tuple(details.items()))


def calendar_first_task_details() -> dict[str, dict[str, Any]]:
    """Return full task records read explicitly during this planning turn."""

    result: dict[str, dict[str, Any]] = {}
    for task_id, serialized in _calendar_first_task_details.get():
        try:
            task = json.loads(serialized)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(task, dict):
            result[task_id] = task
    return result


def _default_gws_runner(parts: list[str], params: dict) -> dict[str, Any]:
    from hermes_constants import get_hermes_home

    binary = os.getenv("HERMES_GWS_BIN") or shutil.which("gws")
    if not binary:
        raise RuntimeError("gws is not installed")
    token_path = Path(get_hermes_home()) / "google_token.json"
    env = os.environ.copy()
    if token_path.is_file():
        env["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"] = str(token_path)
    else:
        config_dir = _native_gws_config_dir()
        native_credentials = (config_dir / "credentials.enc", config_dir / "credentials.json")
        if not any(path.is_file() for path in native_credentials):
            raise RuntimeError("Google Workspace is not authenticated")
        env.pop("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE", None)
        env["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] = str(config_dir)
    completed = subprocess.run(
        [binary, *parts, "--params", json.dumps(params)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "Calendar request failed").strip()
        raise RuntimeError(detail[:500])
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Calendar returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Calendar returned an invalid response")
    return payload


def _paginate(
    run_gws: Callable[[list[str], dict], dict[str, Any]],
    parts: list[str],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        page_params = dict(params)
        if page_token:
            page_params["pageToken"] = page_token
        payload = run_gws(parts, page_params)
        page_items = payload.get("items") or []
        if not isinstance(page_items, list):
            raise RuntimeError("Calendar page items were invalid")
        items.extend(item for item in page_items if isinstance(item, dict))
        page_token = str(payload.get("nextPageToken") or "").strip() or None
        if not page_token:
            return items


def build_calendar_preflight_receipt(
    *,
    start_date: str,
    end_date: str,
    timezone_name: str = "Asia/Jerusalem",
    run_gws: Callable[[list[str], dict], dict[str, Any]] = _default_gws_runner,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read every visible calendar and return a complete/partial/unavailable receipt."""

    zone = ZoneInfo(timezone_name)
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end == start:
        end = start + timedelta(days=1)
        end_date = end.isoformat()
    elif end < start:
        raise ValueError("endDate must be after startDate")
    time_min = datetime.combine(start, time.min, tzinfo=zone).isoformat()
    time_max = datetime.combine(end, time.min, tzinfo=zone).isoformat()
    captured = now or datetime.now(timezone.utc)
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    base: dict[str, Any] = {
        "status": "unavailable",
        "complete": False,
        "capturedAt": captured.isoformat(),
        "expiresAt": (captured + CALENDAR_PREFLIGHT_TTL).isoformat(),
        "timezone": timezone_name,
        "range": {"startDate": start_date, "endDate": end_date, "timeMin": time_min, "timeMax": time_max},
        "coverage": {"calendarCount": 0, "eventCount": 0},
        "calendars": [],
        "events": [],
        "errors": [],
    }
    try:
        calendars = _paginate(
            run_gws,
            ["calendar", "calendarList", "list"],
            {"maxResults": 250},
        )
    except Exception as exc:
        base["errors"] = [{"scope": "calendar-list", "message": str(exc)[:500]}]
        base["requiredInteraction"] = {
            "type": "single-choice",
            "question": "Calendar could not be checked. What should Hermes do?",
            "choices": list(_CLOSED_FAILURE_CHOICES),
            "allowCustomAnswer": False,
        }
        return base

    readable = [calendar for calendar in calendars if calendar.get("id")]
    base["coverage"]["calendarCount"] = len(readable)
    for calendar in readable:
        calendar_id = str(calendar["id"])
        base["calendars"].append(
            {"id": calendar_id, "summary": str(calendar.get("summary") or "")[:300]}
        )
        try:
            events = _paginate(
                run_gws,
                ["calendar", "events", "list"],
                {
                    "calendarId": calendar_id,
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "timeZone": timezone_name,
                    "singleEvents": True,
                    "orderBy": "startTime",
                    "maxResults": 2500,
                },
            )
            for event in events:
                base["events"].append({**event, "calendarId": calendar_id})
        except Exception as exc:
            base["errors"].append(
                {"scope": "calendar-events", "calendarId": calendar_id, "message": str(exc)[:500]}
            )
    base["coverage"]["eventCount"] = len(base["events"])
    base["status"] = "complete" if not base["errors"] else "partial"
    base["complete"] = not base["errors"]
    if base["errors"]:
        base["requiredInteraction"] = {
            "type": "single-choice",
            "question": "Calendar could not be fully checked. What should Hermes do?",
            "choices": list(_CLOSED_FAILURE_CHOICES),
            "allowCustomAnswer": False,
        }
    return base


__all__ = [
    "begin_calendar_first_planning_turn",
    "build_calendar_preflight_receipt",
    "calendar_first_candidate_records",
    "calendar_preflight_gate",
    "calendar_receipt_covers",
    "calendar_receipt_is_fresh_complete",
    "calendar_first_task_inventory",
    "calendar_first_planning_turn_active",
    "complete_inventory_repeat_gate",
    "complete_calendar_first_planning_turn",
    "record_calendar_first_candidate_inventory",
    "record_calendar_first_task_inventory",
    "same_day_grounding_gate_message",
]
