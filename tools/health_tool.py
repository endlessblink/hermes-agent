"""Service-gated tools for durable food and exercise tracking."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, cast

import yaml

from agent.health_state import HealthStateStore
from agent.health_status_export import MAX_EXPORT_BYTES, validate_health_compact_envelope
from tools.registry import registry, tool_error


_SSH_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_SSH_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def _config() -> dict[str, Any]:
    from hermes_constants import get_hermes_home

    path = Path(get_hermes_home()) / "config.yaml"
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _health_config() -> dict[str, Any]:
    raw = _config().get("health") or {}
    return raw if isinstance(raw, dict) else {}


def _check_health_enabled() -> bool:
    try:
        return _health_config().get("enabled") is True
    except Exception:
        return False


def _active_profile() -> str:
    from hermes_cli.profiles import get_active_profile_name

    return get_active_profile_name() or "default"


def _remote_status_source() -> dict[str, str] | None:
    raw = _health_config().get("status_source")
    if not isinstance(raw, dict) or raw.get("transport") != "ssh":
        return None
    host = raw.get("host")
    user = raw.get("user")
    identity_raw = raw.get("identity_file")
    if not isinstance(host, str) or not _SSH_HOST_RE.fullmatch(host):
        return None
    if host.startswith("-") or ".." in host:
        return None
    if not isinstance(user, str) or not _SSH_USER_RE.fullmatch(user):
        return None
    if not isinstance(identity_raw, str):
        return None
    identity = Path(identity_raw)
    if not identity.is_absolute() or not identity.is_file():
        return None
    if os.name == "posix" and stat.S_IMODE(identity.stat().st_mode) & 0o077:
        return None
    return {"host": host, "user": user, "identity_file": str(identity.resolve())}


def _check_health_status_enabled() -> bool:
    try:
        if _active_profile() == "office-work":
            return _remote_status_source() is not None
        return _check_health_enabled()
    except Exception:
        return False


def _store() -> HealthStateStore:
    if not _check_health_enabled():
        raise ValueError("health tracking is not enabled")
    from hermes_constants import get_hermes_home

    config = _health_config()
    return HealthStateStore(
        Path(get_hermes_home()),
        daily_target_calories=config.get("daily_target_calories", 1900),
    )


def _result(payload: dict[str, Any]) -> str:
    return json.dumps({"result": payload}, ensure_ascii=False)


def _error(exc: Exception | str) -> str:
    return tool_error(str(exc))


def _calories(args: dict[str, Any]) -> dict[str, Any]:
    minimum = args.get("caloriesMin")
    return {"min": minimum, "max": args.get("caloriesMax", minimum)}


def _macro_values(args: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "proteinGrams",
        "carbohydrateGrams",
        "fatGrams",
        "fiberGrams",
        "sugarGrams",
    }
    return {field: args[field] for field in fields if field in args}


def _handle_log_event(args: dict, **kwargs) -> str:
    try:
        recorded = _store().record_event(
            request_id=cast(str, args.get("requestId")),
            kind=cast(str, args.get("kind")),
            label=cast(str, args.get("label")),
            calories=_calories(args),
            macros=_macro_values(args),
            status=cast(str | None, args.get("status")),
            occurred_at=cast(str | None, args.get("occurredAt")),
        )
        return _result(recorded)
    except Exception as exc:
        return _error(exc)


def _handle_correct_event(args: dict, **kwargs) -> str:
    try:
        changes: dict[str, Any] = {}
        if "label" in args:
            changes["label"] = args["label"]
        if "caloriesMin" in args or "caloriesMax" in args:
            changes["calories"] = _calories(args)
        macros = _macro_values(args)
        if macros:
            changes["macros"] = macros
        if "status" in args:
            changes["status"] = args["status"]
        if "occurredAt" in args:
            changes["occurred_at"] = args["occurredAt"]
        corrected = _store().correct_event(
            request_id=cast(str, args.get("requestId")),
            event_id=cast(str, args.get("eventId")),
            **changes,
        )
        return _result(corrected)
    except Exception as exc:
        return _error(exc)


def _handle_get_day(args: dict, **kwargs) -> str:
    try:
        return _result({"day": _store().get_day(args.get("date"))})
    except Exception as exc:
        return _error(exc)


def _handle_compact_status(args: dict, **kwargs) -> str:
    try:
        active_profile = _active_profile()
    except Exception:
        return _error("health status is unavailable")
    if active_profile == "office-work":
        try:
            source = _remote_status_source()
            if source is None:
                raise ValueError("remote health status is not configured")
            argv = [
                "ssh",
                "-T",
                "-F",
                "/dev/null",
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ClearAllForwardings=yes",
                "-o",
                "PermitLocalCommand=no",
                "-o",
                "ConnectTimeout=5",
                "-i",
                source["identity_file"],
                f"{source['user']}@{source['host']}",
            ]
            completed = subprocess.run(
                argv,
                capture_output=True,
                check=False,
                shell=False,
                stdin=subprocess.DEVNULL,
                timeout=8,
            )
            if completed.returncode != 0 or len(completed.stdout) > MAX_EXPORT_BYTES:
                raise ValueError("remote health export failed")
            payload = json.loads(completed.stdout.decode("utf-8", errors="strict"))
            status = validate_health_compact_envelope(payload)
            return _result(
                {
                    "status": status,
                    "generatedAt": payload["generatedAt"],
                    "timezone": payload["timezone"],
                }
            )
        except Exception:
            return _error("health status is unavailable")
    try:
        return _result({"status": _store().compact_status(args.get("date"))})
    except Exception as exc:
        return _error(exc)


_CALORIE_PROPERTIES = {
    "caloriesMin": {"type": "number", "minimum": 0},
    "caloriesMax": {"type": "number", "minimum": 0},
}
_MACRO_PROPERTIES = {
    "proteinGrams": {"type": "number", "minimum": 0},
    "carbohydrateGrams": {"type": "number", "minimum": 0},
    "fatGrams": {"type": "number", "minimum": 0},
    "fiberGrams": {"type": "number", "minimum": 0},
    "sugarGrams": {"type": "number", "minimum": 0},
}

LOG_EVENT_SCHEMA = {
    "name": "health_log_event",
    "description": "Record one food or exercise event in the durable health ledger.",
    "parameters": {
        "type": "object",
        "properties": {
            "requestId": {"type": "string", "description": "Stable unique ID for safe retries."},
            "kind": {"type": "string", "enum": ["food", "exercise"]},
            "label": {"type": "string"},
            **_CALORIE_PROPERTIES,
            **_MACRO_PROPERTIES,
            "status": {
                "type": "string",
                "enum": ["consumed", "planned", "completed", "cancelled"],
            },
            "occurredAt": {
                "type": "string",
                "description": "ISO-8601 timestamp with timezone; server time is used if omitted.",
            },
        },
        "required": ["requestId", "kind", "label", "caloriesMin"],
        "additionalProperties": False,
    },
}

CORRECT_EVENT_SCHEMA = {
    "name": "health_correct_event",
    "description": "Append an immutable correction or completion for an existing health event.",
    "parameters": {
        "type": "object",
        "properties": {
            "requestId": {"type": "string", "description": "Stable unique ID for safe retries."},
            "eventId": {"type": "string"},
            "label": {"type": "string"},
            **_CALORIE_PROPERTIES,
            **_MACRO_PROPERTIES,
            "status": {
                "type": "string",
                "enum": ["consumed", "planned", "completed", "cancelled"],
            },
            "occurredAt": {"type": "string"},
        },
        "required": ["requestId", "eventId"],
        "additionalProperties": False,
    },
}

GET_DAY_SCHEMA = {
    "name": "health_get_day",
    "description": "Read derived health totals and private event details for one Jerusalem date.",
    "parameters": {
        "type": "object",
        "properties": {"date": {"type": "string", "description": "YYYY-MM-DD; today if omitted."}},
        "additionalProperties": False,
    },
}

COMPACT_STATUS_SCHEMA = {
    "name": "health_compact_status",
    "description": (
        "Read an allowlisted daily status containing only totals and completion flags; "
        "never returns meal labels, macros, event IDs, or history."
    ),
    "parameters": {
        "type": "object",
        "properties": {"date": {"type": "string", "description": "YYYY-MM-DD; today if omitted."}},
        "additionalProperties": False,
    },
}


for _name, _schema, _handler in (
    ("health_log_event", LOG_EVENT_SCHEMA, _handle_log_event),
    ("health_correct_event", CORRECT_EVENT_SCHEMA, _handle_correct_event),
    ("health_get_day", GET_DAY_SCHEMA, _handle_get_day),
    ("health_compact_status", COMPACT_STATUS_SCHEMA, _handle_compact_status),
):
    registry.register(
        name=_name,
        toolset="health_status" if _name == "health_compact_status" else "health",
        schema=_schema,
        handler=_handler,
        check_fn=(
            _check_health_status_enabled
            if _name == "health_compact_status"
            else _check_health_enabled
        ),
    )
