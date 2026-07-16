"""Flow State local task API tools.

Registers Hermes-callable tools that talk to Flow State's localhost Local Task
API. Flow State remains the source of truth; Hermes is only a client.
"""

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tools.flowstate_receipts import (
    CanonicalReceiptError,
    canonical_json_sha256,
    validate_nested_canonical_receipt,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://127.0.0.1:5577"
_FLOW_STATE_API_URL: str = ""
_FLOW_STATE_API_TOKEN: str = ""
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_ONLY_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_LOCAL_MINUTE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d$")
_VALID_STATUS_FILTERS = {"todo", "open", "done"}
_VALID_DUE_FILTERS = {"today", "overdue", "open"}
_VALID_PRIORITIES = {"low", "medium", "high"}
_CANONICAL_TASK_CONTRACT = "task-v1"
_CANONICAL_TASK_SOURCE = "local-api"
_TASK_LIFECYCLE_CONTRACT = "task-lifecycle-v1"
_SUBTASK_BATCH_CONTRACT = "subtask-batch-v1"
_WORK_BLOCK_CONTRACT = "work-block-v1"
_LIFECYCLE_CREATE_STATUSES = {"planned", "in_progress", "backlog", "on_hold"}
_LIFECYCLE_STATUSES = _LIFECYCLE_CREATE_STATUSES | {"done"}
_CANONICAL_PATCH_FIELDS = {"title", "description", "priority", "dueDate", "progress"}
_CANONICAL_UPDATE_FIELDS = {
    "id",
    "operationId",
    "baseRevision",
    "patch",
    "preview",
    "previewDigest",
    "previewExpiresAt",
    "requestHash",
}
_RECURRENCE_COMMON_FIELDS = {"pattern", "interval", "endType", "endDate", "endCount"}
_RECURRENCE_PATTERN_FIELDS = {
    "daily": set(),
    "weekly": {"weekdays"},
    "monthly": {"monthDay", "monthWeekday"},
    "yearly": set(),
}
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SCOPE_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}$")
_READ_THROUGH_CACHE_VERSION = 1
_READ_THROUGH_CACHE_MAX_AGE_SECONDS = 300
_CAPABILITIES_SCHEMA_VERSION = "flowstate-hermes-capabilities-v1"
_CAPABILITIES_CACHE_SECONDS = 30.0

# Exact compatibility boundary for the currently registered Hermes tools.
# Dormant legacy instance/subtask mutation helpers are intentionally excluded.
_REGISTERED_ROUTE_REQUIREMENTS = (
    ("GET", "/api/health", "health-v1"),
    ("GET", "/api/assistant/context", "assistant-context-v1"),
    ("GET", "/api/tasks", "task-list-v1"),
    ("GET", "/api/tasks/search", "task-search-v1"),
    ("GET", "/api/tasks/inventory", "task-inventory-v1"),
    ("GET", "/api/tasks/:id", "task-read-v1"),
    ("POST", "/api/tasks/lifecycle", "task-lifecycle-v1"),
    ("PATCH", "/api/tasks/:id", "task-v1"),
    ("GET", "/api/timer/current", "timer-current-v1"),
    ("GET", "/api/timer/diagnostics", "timer-diagnostics-v1"),
    ("GET", "/api/tasks/:id/instances", "task-instances-v1"),
    ("POST", "/api/tasks/:id/work-blocks", "work-block-v1"),
    ("POST", "/api/tasks/:id/done-for-now", "task-v1"),
    ("POST", "/api/tasks/:id/merge", "task-v1"),
    ("GET", "/api/tasks/:id/subtasks", "subtask-list-v1"),
    ("POST", "/api/tasks/:id/subtasks/batch", "subtask-batch-v1"),
)

_FLOWSTATE_TOOL_REQUIREMENTS = {
    "flowstate_get_assistant_context": (("GET", "/api/assistant/context", "assistant-context-v1"),),
    "flowstate_health": (("GET", "/api/health", "health-v1"),),
    "flowstate_list_tasks": (
        ("GET", "/api/tasks", "task-list-v1"),
        ("GET", "/api/tasks/inventory", "task-inventory-v1"),
    ),
    "flowstate_search_tasks": (
        ("GET", "/api/tasks/search", "task-search-v1"),
        ("GET", "/api/tasks/inventory", "task-inventory-v1"),
    ),
    "flowstate_get_task": (("GET", "/api/tasks/:id", "task-read-v1"),),
    "flowstate_create_task": (("POST", "/api/tasks/lifecycle", "task-lifecycle-v1"),),
    "flowstate_update_task": (("PATCH", "/api/tasks/:id", "task-v1"),),
    "flowstate_delete_task": (("POST", "/api/tasks/lifecycle", "task-lifecycle-v1"),),
    "flowstate_restore_task": (("POST", "/api/tasks/lifecycle", "task-lifecycle-v1"),),
    "flowstate_set_task_status": (("POST", "/api/tasks/lifecycle", "task-lifecycle-v1"),),
    "flowstate_get_current_timer": (("GET", "/api/timer/current", "timer-current-v1"),),
    "flowstate_get_timer_diagnostics": (("GET", "/api/timer/diagnostics", "timer-diagnostics-v1"),),
    "flowstate_list_task_instances": (("GET", "/api/tasks/:id/instances", "task-instances-v1"),),
    "flowstate_create_work_block": (("POST", "/api/tasks/:id/work-blocks", "work-block-v1"),),
    "flowstate_move_work_block": (("POST", "/api/tasks/:id/work-blocks", "work-block-v1"),),
    "flowstate_resize_work_block": (("POST", "/api/tasks/:id/work-blocks", "work-block-v1"),),
    "flowstate_remove_work_block": (("POST", "/api/tasks/:id/work-blocks", "work-block-v1"),),
    "flowstate_done_for_now": (("POST", "/api/tasks/:id/done-for-now", "task-v1"),),
    "flowstate_merge_tasks": (("POST", "/api/tasks/:id/merge", "task-v1"),),
    "flowstate_list_subtasks": (("GET", "/api/tasks/:id/subtasks", "subtask-list-v1"),),
    "flowstate_subtask_batch": (("POST", "/api/tasks/:id/subtasks/batch", "subtask-batch-v1"),),
}

_capabilities_cache: Optional[tuple[str, float, Dict[str, Any]]] = None
_capabilities_cache_lock = threading.Lock()


class _FlowStateApiError(RuntimeError):
    """Safe, typed Local Task API error with no request credentials attached."""

    def __init__(self, message: str, *, code: str, status: int, action: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.action = action


def _get_env_value(key: str) -> Optional[str]:
    try:
        from hermes_cli.config import get_env_value

        return get_env_value(key)
    except Exception:
        return os.getenv(key)


def _get_config() -> tuple[str, str]:
    base_url = (
        _FLOW_STATE_API_URL
        or _get_env_value("FLOW_STATE_API_URL")
        or _get_env_value("FLOWSTATE_API_URL")
        or _DEFAULT_BASE_URL
    )
    token = (
        _FLOW_STATE_API_TOKEN
        or _get_env_value("FLOW_STATE_API_TOKEN")
        or _get_env_value("FLOWSTATE_API_TOKEN")
        or ""
    )
    return base_url.rstrip("/"), token


def _headers(token: str = "") -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _flowstate_cache_root() -> Path:
    """Return the active profile's private FlowState read-through cache."""
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "cache" / "flowstate-read-through"


def _cacheable_read_path(path: str) -> bool:
    parsed_path = urllib.parse.urlsplit(path).path
    if parsed_path == "/api/tasks/inventory":
        return False
    return parsed_path == "/api/assistant/context" or (
        parsed_path == "/api/tasks"
        or parsed_path == "/api/tasks/search"
        or (
            parsed_path.startswith("/api/tasks/")
            and not parsed_path.endswith("/done-for-now")
            and not parsed_path.endswith("/merge")
            and not parsed_path.endswith("/delete")
            and not parsed_path.endswith("/batch")
        )
    )


def _cache_identity(base_url: str, token: str) -> str:
    # The digest separates profiles/users/API targets without persisting a
    # credential or exposing one in a filename or tool result.
    return hashlib.sha256(f"{base_url}\0{token}".encode("utf-8")).hexdigest()


def _cache_path(base_url: str, token: str, path: str) -> Path:
    identity = _cache_identity(base_url, token)
    request_key = hashlib.sha256(f"{identity}\0{path}".encode("utf-8")).hexdigest()
    return _flowstate_cache_root() / f"{request_key}.json"


def _write_read_snapshot(
    base_url: str,
    token: str,
    path: str,
    payload: Dict[str, Any],
) -> None:
    if not _cacheable_read_path(path):
        return
    root = _flowstate_cache_root()
    target = _cache_path(base_url, token, path)
    record = {
        "version": _READ_THROUGH_CACHE_VERSION,
        "identity": _cache_identity(base_url, token),
        "path": path,
        "cachedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload": payload,
    }
    temp_name: Optional[str] = None
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=root,
            prefix=f".{target.stem}-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, target)
    except (OSError, TypeError, ValueError):
        logger.warning("FlowState read-through snapshot write failed", exc_info=False)
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def _read_cached_snapshot(
    base_url: str,
    token: str,
    path: str,
) -> Optional[Dict[str, Any]]:
    if not _cacheable_read_path(path):
        return None
    try:
        record = json.loads(_cache_path(base_url, token, path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    expected_identity = _cache_identity(base_url, token)
    if not (
        isinstance(record, dict)
        and record.get("version") == _READ_THROUGH_CACHE_VERSION
        and record.get("identity") == expected_identity
        and record.get("path") == path
        and isinstance(record.get("cachedAt"), str)
        and isinstance(record.get("payload"), dict)
    ):
        return None
    try:
        cached_at = datetime.fromisoformat(record["cachedAt"].replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - cached_at).total_seconds()
    except (TypeError, ValueError):
        return None
    if age_seconds < 0 or age_seconds > _READ_THROUGH_CACHE_MAX_AGE_SECONDS:
        return None
    payload = dict(record["payload"])
    payload["_hermesReadThrough"] = {
        "source": "profile-cache",
        "stale": True,
        "cachedAt": record["cachedAt"],
        "path": path,
    }
    return payload


def _is_valid_date_or_due_filter(value: str) -> bool:
    return value in _VALID_DUE_FILTERS or bool(_DATE_ONLY_RE.match(value))


def _compact_http_error(exc: urllib.error.HTTPError) -> _FlowStateApiError:
    action = None
    try:
        raw = exc.read().decode("utf-8", errors="replace")
        payload = json.loads(raw) if raw else {}
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = error.get("message")
            code = error.get("code")
        else:
            message = error
            code = payload.get("code") if isinstance(payload, dict) else None
            if not code and error in {
                "signed_out", "reauth_required", "sidecar_auth_bridge_failed",
            }:
                code = error
        action = payload.get("action") if isinstance(payload, dict) else None
    except Exception:
        message = None
        code = None
    if not message:
        message = exc.reason or "HTTP error"
    if exc.code == 401:
        message = "Flow State Local Task API rejected the bearer token. Check FLOW_STATE_API_TOKEN."
        code = code or "unauthorized"
    if exc.code == 503 and code not in {
        "signed_out", "reauth_required", "sidecar_auth_bridge_failed",
    }:
        message = "Flow State Local Task API is temporarily unavailable."
        code = code or "flowstate_unavailable"
    return _FlowStateApiError(
        str(message),
        code=str(code or f"http_{exc.code}"),
        status=exc.code,
        action=str(action) if action else None,
    )


def _request(
    method: str,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    *,
    allow_stale_cache: bool = True,
) -> Dict[str, Any]:
    base_url, token = _get_config()
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers=_headers(token),
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise _compact_http_error(exc) from exc
    except urllib.error.URLError as exc:
        if method == "GET" and allow_stale_cache:
            cached = _read_cached_snapshot(base_url, token, path)
            if cached is not None:
                return cached
        raise RuntimeError(
            f"Flow State Local Task API is unavailable at {base_url}. "
            "Open Flow State and enable Local Task API, then check FLOW_STATE_API_URL."
        ) from exc
    except TimeoutError as exc:
        if method == "GET" and allow_stale_cache:
            cached = _read_cached_snapshot(base_url, token, path)
            if cached is not None:
                return cached
        raise RuntimeError(f"Flow State Local Task API timed out at {base_url}.") from exc

    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("Flow State Local Task API returned non-JSON data.") from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    if not isinstance(payload, dict):
        raise RuntimeError("Flow State Local Task API returned an unexpected response.")
    if method == "GET":
        _write_read_snapshot(base_url, token, path, payload)
    return payload


def _tool_result(payload: Dict[str, Any]) -> str:
    return json.dumps({"result": payload})


def _tool_error(message: str) -> str:
    from tools.registry import tool_error

    return tool_error(message)


def _typed_tool_error(exc: _FlowStateApiError) -> str:
    from tools.registry import tool_error

    extra = {"code": exc.code, "status": exc.status}
    if exc.action:
        extra["action"] = exc.action
    return tool_error(str(exc), **extra)


def _validated_inventory_receipt(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Fail closed before a model can treat malformed inventory as exact."""
    try:
        captured_at = str(payload["capturedAt"])
        captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        captured_age = datetime.now(timezone.utc) - captured.astimezone(timezone.utc)
        items = payload["items"]
        page = payload["page"]
        ids = [item["id"] for item in items]
        valid = (
            payload.get("source") == "flowstate"
            and isinstance(payload.get("scope"), str) and bool(payload["scope"].strip())
            and payload.get("scopeKind") in {"personal", "workspace"}
            and bool(_SCOPE_FINGERPRINT_RE.fullmatch(str(payload.get("scopeFingerprint") or "")))
            and isinstance(payload.get("appVersion"), str) and bool(payload["appVersion"])
            and captured.utcoffset() is not None
            and -60 <= captured_age.total_seconds() <= 300
            and payload.get("fresh") is True
            and isinstance(payload.get("complete"), bool)
            and isinstance(items, list)
            and all(isinstance(item, dict) for item in items)
            and all(_is_positive_int(item.get("canonicalRevision")) for item in items)
            and all(isinstance(task_id, str) and _UUID_RE.fullmatch(task_id) for task_id in ids)
            and len(ids) == len(set(ids))
            and isinstance(page, dict)
            and isinstance(page.get("hasMore"), bool)
        )
        if payload.get("complete") is True:
            valid = valid and (
                isinstance(payload.get("changeSequence"), int)
                and not isinstance(payload.get("changeSequence"), bool)
                and payload["changeSequence"] >= 0
                and isinstance(payload.get("total"), int)
                and not isinstance(payload.get("total"), bool)
                and payload["total"] == len(ids)
                and page.get("hasMore") is False
                and page.get("nextCursor") is None
            )
        else:
            valid = valid and "total" not in payload
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise _FlowStateApiError(
            "Flow State returned an invalid inventory receipt; no exact count is available.",
            code="invalid_inventory_receipt",
            status=502,
        )
    return payload


def _invalidate_flowstate_capabilities_cache() -> None:
    global _capabilities_cache
    with _capabilities_cache_lock:
        _capabilities_cache = None


def _load_flowstate_capabilities() -> Dict[str, Any]:
    """Fetch and strictly validate the sidecar's non-secret route manifest."""
    global _capabilities_cache
    base_url, token = _get_config()
    now = time.monotonic()
    with _capabilities_cache_lock:
        cached = _capabilities_cache
        if cached is not None and cached[0] == base_url and now - cached[1] < _CAPABILITIES_CACHE_SECONDS:
            return cached[2]

    req = urllib.request.Request(
        f"{base_url}/api/capabilities",
        headers=_headers(token),
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=1) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("Flow State returned an invalid capability manifest.") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schemaVersion") != _CAPABILITIES_SCHEMA_VERSION
        or not isinstance(payload.get("routes"), list)
    ):
        raise RuntimeError("Flow State returned an invalid capability manifest.")
    with _capabilities_cache_lock:
        _capabilities_cache = (base_url, now, payload)
    return payload


def _capability_index(payload: Dict[str, Any]) -> Dict[tuple[str, str], Dict[str, Any]]:
    index: Dict[tuple[str, str], Dict[str, Any]] = {}
    for route in payload.get("routes", []):
        if not isinstance(route, dict):
            continue
        method = route.get("method")
        path = route.get("path")
        contract_version = route.get("contractVersion")
        available = route.get("available")
        if (
            isinstance(method, str)
            and isinstance(path, str)
            and isinstance(contract_version, str)
            and isinstance(available, bool)
        ):
            index[(method, path)] = {
                "available": available,
                "contractVersion": contract_version,
            }
    return index


def _route_requirements_met(
    capabilities: Dict[tuple[str, str], Dict[str, Any]],
    requirements: tuple[tuple[str, str, str], ...],
) -> bool:
    for method, path, contract_version in requirements:
        route = capabilities.get((method, path))
        if (
            route is None
            or route.get("available") is not True
            or route.get("contractVersion") != contract_version
        ):
            return False
    return True


def _requirement_failure(
    capabilities: Dict[tuple[str, str], Dict[str, Any]],
    requirements: tuple[tuple[str, str, str], ...],
) -> Optional[Dict[str, str]]:
    for method, path, contract_version in requirements:
        route = capabilities.get((method, path))
        if route is None:
            return {"method": method, "path": path, "reason": "route_missing"}
        if route.get("available") is not True:
            return {"method": method, "path": path, "reason": "route_unavailable"}
        if route.get("contractVersion") != contract_version:
            return {"method": method, "path": path, "reason": "contract_mismatch"}
    return None


def _flowstate_compatibility_report() -> Dict[str, Any]:
    try:
        payload = _load_flowstate_capabilities()
        capabilities = _capability_index(payload)
    except Exception:
        return {
            "schemaVersion": _CAPABILITIES_SCHEMA_VERSION,
            "compatible": False,
            "routeCount": len(_REGISTERED_ROUTE_REQUIREMENTS),
            "blockedTools": [{"tool": "flowstate", "reason": "manifest_unavailable"}],
        }

    blocked = []
    for tool, requirements in sorted(_FLOWSTATE_TOOL_REQUIREMENTS.items()):
        if tool == "flowstate_health":
            continue
        failure = _requirement_failure(capabilities, requirements)
        if failure is not None:
            blocked.append({"tool": tool, **failure})
    return {
        "schemaVersion": _CAPABILITIES_SCHEMA_VERSION,
        "compatible": not blocked,
        "routeCount": len(_REGISTERED_ROUTE_REQUIREMENTS),
        "blockedTools": blocked,
    }


def _make_flowstate_route_check(tool_name: str):
    requirements = _FLOWSTATE_TOOL_REQUIREMENTS[tool_name]

    def _check() -> bool:
        try:
            return _route_requirements_met(
                _capability_index(_load_flowstate_capabilities()),
                requirements,
            )
        except Exception:
            return False

    _check.__name__ = f"check_{tool_name}_route"
    return _check


_FLOWSTATE_TOOL_CHECKS = {
    tool_name: _make_flowstate_route_check(tool_name)
    for tool_name in _FLOWSTATE_TOOL_REQUIREMENTS
    if tool_name != "flowstate_health"
}


def _check_flowstate_available() -> bool:
    base_url, token = _get_config()
    if base_url != _DEFAULT_BASE_URL:
        return True
    if token:
        return True
    try:
        req = urllib.request.Request(f"{base_url}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=1):
            return True
    except Exception:
        return False


def _handle_health(args: dict, **kw) -> str:
    try:
        health = _request("GET", "/api/health")
        return _tool_result({**health, "compatibility": _flowstate_compatibility_report()})
    except Exception as exc:
        logger.error("flowstate_health error: %s", exc)
        return _tool_error(str(exc))


def _handle_assistant_context(args: dict, **kw) -> str:
    try:
        return _tool_result(_request("GET", "/api/assistant/context"))
    except Exception as exc:
        logger.error("flowstate_get_assistant_context error: %s", exc)
        return _tool_error(str(exc))


_AUDIT_COVERAGE_OPTIONAL_FIELDS = (
    "snapshotAt",
    "auditMode",
    "representativeSample",
    "expectedItemIds",
    "expectedItemCount",
    "reviewedItems",
    "unreviewedItemIds",
    "screenshotRows",
    "knownTasks",
    "blockers",
    "notCovered",
)

_AUDIT_BLOCKED_INSTRUCTION = (
    "FlowState refused this summary draft: it claims more coverage than the "
    "receipt proves. Use safeSummary verbatim, or rewrite the summary strictly "
    "weaker than safeSummary and call flowstate_audit_coverage again. Never "
    "present the blocked draft to the user."
)


def _handle_audit_coverage(args: dict, **kw) -> str:
    """Notarize a review/audit summary against FlowState's coverage receipt.

    The endpoint re-reads claimed records server-side and refuses wording
    stronger than the evidence (422 broad_claim_blocked). Hermes must treat a
    blocked draft as a hard rewording requirement, and must surface typed
    blockers when the endpoint or connector is unavailable instead of
    presenting an unverified summary as covered.
    """
    body: Dict[str, Any] = {}
    for field in ("auditScope", "sourceSurface", "summaryDraft"):
        value = args.get(field)
        if not isinstance(value, str) or not value.strip():
            return _tool_error(f"{field} is required")
        body[field] = value
    for field in _AUDIT_COVERAGE_OPTIONAL_FIELDS:
        if field in args and args[field] is not None:
            body[field] = args[field]
    # Hermes has no server-owned live proof; never assert live verification.
    body["liveVerified"] = False

    base_url, token = _get_config()
    req = urllib.request.Request(
        f"{base_url}/api/audit/coverage",
        data=json.dumps(body).encode("utf-8"),
        headers=_headers(token),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 422:
            try:
                blocked = json.loads(exc.read().decode("utf-8", errors="replace"))
            except Exception:
                blocked = {}
            if isinstance(blocked, dict) and blocked.get("error") == "broad_claim_blocked":
                return _tool_result(
                    {
                        "accepted": False,
                        "blocked": "broad_claim_blocked",
                        "violations": blocked.get("violations") or [],
                        "claimLevel": blocked.get("claimLevel"),
                        "receipt": blocked.get("receipt"),
                        "safeSummary": blocked.get("safeSummary"),
                        "instruction": _AUDIT_BLOCKED_INSTRUCTION,
                    }
                )
        if exc.code == 404:
            return _typed_tool_error(
                _FlowStateApiError(
                    "The installed FlowState does not serve /api/audit/coverage yet, "
                    "so this review cannot be receipt-verified. State explicitly that "
                    "coverage is unverified declared-only evidence; do not claim full, "
                    "complete, or verified coverage.",
                    code="audit_endpoint_unavailable",
                    status=404,
                )
            )
        logger.error("flowstate_audit_coverage error: %s", exc)
        return _typed_tool_error(_compact_http_error(exc))
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.error("flowstate_audit_coverage error: %s", exc)
        return _tool_error(
            f"Flow State Local Task API is unavailable at {base_url}. The review "
            "summary cannot be receipt-verified; report this blocker instead of "
            "claiming any coverage level."
        )

    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return _tool_error("Flow State Local Task API returned non-JSON data.")
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        error = payload.get("error") if isinstance(payload, dict) else None
        return _tool_error(str(error or "Flow State returned an unexpected audit response."))
    return _tool_result({"accepted": True, **payload})


def _handle_list_tasks(args: dict, **kw) -> str:
    status = args.get("status")
    due = args.get("due")
    limit = args.get("limit")

    if status not in (None, "") and status not in _VALID_STATUS_FILTERS:
        return _tool_error("status must be todo|open|done")
    if due not in (None, "") and not _is_valid_date_or_due_filter(str(due)):
        return _tool_error("due must be today|overdue|open|YYYY-MM-DD")
    use_inventory = status in (None, "", "todo", "open") and due in (None, "")
    max_limit = 100 if use_inventory else 25
    if limit not in (None, ""):
        try:
            n = int(limit)
        except (TypeError, ValueError):
            return _tool_error(f"limit must be an integer from 1 to {max_limit}")
        if n < 1 or n > max_limit:
            return _tool_error(f"limit must be an integer from 1 to {max_limit}")
        limit = n

    try:
        if use_inventory:
            params = urllib.parse.urlencode({"limit": limit} if limit not in (None, "") else {})
            suffix = f"?{params}" if params else ""
            payload = _request(
                "GET", f"/api/tasks/inventory{suffix}", allow_stale_cache=False,
            )
            return _tool_result(_validated_inventory_receipt(payload))
        params = urllib.parse.urlencode(
            {
                key: value
                for key, value in {"status": status, "due": due, "limit": limit}.items()
                if value not in (None, "")
            }
        )
        suffix = f"?{params}" if params else ""
        return _tool_result(_request("GET", f"/api/tasks{suffix}"))
    except _FlowStateApiError as exc:
        logger.error("flowstate_list_tasks API error: status=%s code=%s", exc.status, exc.code)
        return _typed_tool_error(exc)
    except Exception as exc:
        logger.error("flowstate_list_tasks error: %s", exc)
        return _tool_error(str(exc))


def _handle_search_tasks(args: dict, **kw) -> str:
    query = str(args.get("query") or "").strip()
    limit = args.get("limit")
    params: Dict[str, Any] = {}
    if limit not in (None, ""):
        if isinstance(limit, bool):
            return _tool_error("limit must be an integer from 1 to 100")
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return _tool_error("limit must be an integer from 1 to 100")
        if limit < 1 or limit > 100:
            return _tool_error("limit must be an integer from 1 to 100")
        params["limit"] = limit

    browse_intent = (
        not query
        or query == "*"
        or (args.get("status") in {"open", "todo"} and len(query) <= 1)
    )
    unknown = set(args) - {"query", "limit", "status"}
    if unknown or ("status" in args and args.get("status") not in {"open", "todo"}):
        return _tool_error("search accepts only query and limit; use flowstate_list_tasks for filters")

    if browse_intent:
        path = f"/api/tasks/inventory?{urllib.parse.urlencode(params)}"
    else:
        if params.get("limit", 25) > 25:
            return _tool_error("exact title search limit must be an integer from 1 to 25")
        params = {"q": query, **params}
        path = f"/api/tasks/search?{urllib.parse.urlencode(params)}"

    try:
        payload = _request("GET", path, allow_stale_cache=not browse_intent)
        if browse_intent:
            payload = _validated_inventory_receipt(payload)
        return _tool_result(payload)
    except _FlowStateApiError as exc:
        logger.error("flowstate_search_tasks API error: status=%s code=%s", exc.status, exc.code)
        return _typed_tool_error(exc)
    except Exception as exc:
        logger.error("flowstate_search_tasks error: %s", exc)
        return _tool_error(str(exc))


def _handle_get_task(args: dict, **kw) -> str:
    task_id = str(args.get("taskId") or "").strip()
    if not task_id:
        return _tool_error("taskId is required")
    try:
        path = f"/api/tasks/{urllib.parse.quote(task_id, safe='')}"
        return _tool_result(_request("GET", path))
    except _FlowStateApiError as exc:
        logger.error(
            "flowstate_get_task API error: status=%s code=%s",
            exc.status,
            exc.code,
        )
        return _typed_tool_error(exc)
    except Exception as exc:
        logger.error("flowstate_get_task error: %s", exc)
        return _tool_error(str(exc))


def _valid_operation_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= 160
    )


def _valid_lifecycle_task_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.fullmatch(value))


def _valid_lifecycle_preview(
    response: Any,
    *,
    action: str,
    task_id: str,
    operation_id: str,
    base_revision: int,
    payload: Dict[str, Any],
) -> bool:
    if not (
        isinstance(response, dict)
        and response.get("ok") is True
        and response.get("result") == "preview"
        and response.get("contractVersion") == _TASK_LIFECYCLE_CONTRACT
        and response.get("operationId") == operation_id
        and response.get("action") == action
        and response.get("taskId") == task_id
        and response.get("baseRevision") == base_revision
        and isinstance(response.get("requestHash"), str)
        and bool(_SHA256_HEX_RE.fullmatch(response["requestHash"]))
        and isinstance(response.get("previewDigest"), str)
        and bool(_SHA256_HEX_RE.fullmatch(response["previewDigest"]))
        and _is_iso_timestamp(response.get("previewExpiresAt"))
    ):
        return False
    normalized = response.get("normalizedPayload")
    return (
        isinstance(normalized, dict)
        and set(normalized) == {
            "contractVersion",
            "source",
            "action",
            "taskId",
            "baseRevision",
            "workspaceId",
            "payload",
        }
        and normalized.get("contractVersion") == _TASK_LIFECYCLE_CONTRACT
        and normalized.get("source") == _CANONICAL_TASK_SOURCE
        and normalized.get("action") == action
        and normalized.get("taskId") == task_id
        and normalized.get("baseRevision") == base_revision
        and normalized.get("payload") == payload
        and (normalized.get("workspaceId") is None or isinstance(normalized.get("workspaceId"), str))
        and canonical_json_sha256(normalized) == response["requestHash"]
    )


def _valid_lifecycle_read_back(
    read_back: Any,
    *,
    action: str,
    task_id: str,
    payload: Dict[str, Any],
    base_revision: int,
) -> bool:
    if not (
        isinstance(read_back, dict)
        and read_back.get("id") == task_id
        and isinstance(read_back.get("isDeleted"), bool)
        and isinstance(read_back.get("tombstone"), bool)
        and read_back.get("canonicalRevision") == base_revision + 1
        and _is_iso_timestamp(read_back.get("canonicalUpdatedAt"))
    ):
        return False
    if action == "create":
        return (
            read_back.get("title") == payload["title"]
            and read_back.get("description") == payload["description"]
            and read_back.get("status") == payload["status"]
            and read_back.get("priority") == payload["priority"]
            and read_back.get("dueDate") == payload["dueDate"]
            and read_back.get("projectId") == payload["projectId"]
            and read_back.get("isDeleted") is False
            and read_back.get("tombstone") is False
        )
    if action == "soft_delete":
        return read_back.get("isDeleted") is True and read_back.get("tombstone") is True
    if action == "restore":
        return read_back.get("isDeleted") is False and read_back.get("tombstone") is False
    return (
        action == "set_status"
        and read_back.get("status") == payload["status"]
        and read_back.get("isDeleted") is False
    )


def _valid_lifecycle_commit(
    response: Any,
    *,
    action: str,
    task_id: str,
    operation_id: str,
    payload: Dict[str, Any],
    request_hash: str,
    base_revision: int,
) -> bool:
    if not (
        isinstance(response, dict)
        and response.get("ok") is True
        and response.get("status") == "committed"
        and response.get("result") == "committed"
        and isinstance(response.get("requestHash"), str)
        and bool(_SHA256_HEX_RE.fullmatch(response["requestHash"]))
        and response.get("requestHash") == request_hash
        and isinstance(response.get("receipt"), dict)
        and response["receipt"].get("status") == "committed"
        and response["receipt"].get("requestHash") == response["requestHash"]
    ):
        return False
    try:
        validate_nested_canonical_receipt(
            response["receipt"],
            expected={
                "contractVersion": _TASK_LIFECYCLE_CONTRACT,
                "operationId": operation_id,
                "source": _CANONICAL_TASK_SOURCE,
                "entityType": "task",
                "action": action,
                "entityId": task_id,
            },
            valid_read_back=lambda read_back: _valid_lifecycle_read_back(
                read_back,
                action=action,
                task_id=task_id,
                payload=payload,
                base_revision=base_revision,
            ),
        )
    except CanonicalReceiptError:
        return False
    return True


def _handle_lifecycle(
    args: dict,
    *,
    action: str,
    payload: Dict[str, Any],
    base_revision: int,
) -> str:
    task_id = args.get("taskId")
    if not _valid_lifecycle_task_id(task_id):
        return _tool_error("taskId is required and must be a stable UUID")
    operation_id = args.get("operationId")
    if not _valid_operation_id(operation_id):
        return _tool_error("operationId is required and must be at most 160 trimmed characters")
    preview = args.get("preview", True)
    if not isinstance(preview, bool):
        return _tool_error("preview must be a boolean")

    body: Dict[str, Any] = {
        "operationId": operation_id,
        "action": action,
        "taskId": task_id,
        "baseRevision": base_revision,
        "payload": payload,
        "preview": preview,
    }
    if not preview:
        digest = args.get("previewDigest")
        expiry = args.get("previewExpiresAt")
        request_hash = args.get("requestHash")
        if not isinstance(digest, str) or not _SHA256_HEX_RE.fullmatch(digest):
            return _tool_error("previewDigest is required when preview is false")
        if not _is_iso_timestamp(expiry):
            return _tool_error("previewExpiresAt is required when preview is false")
        if not isinstance(request_hash, str) or not _SHA256_HEX_RE.fullmatch(request_hash):
            return _tool_error("requestHash is required when preview is false")
        body["previewDigest"] = digest
        body["previewExpiresAt"] = expiry

    try:
        response = _request("POST", "/api/tasks/lifecycle", body)
        valid = (
            _valid_lifecycle_preview(
                response,
                action=action,
                task_id=task_id,
                operation_id=operation_id,
                base_revision=base_revision,
                payload=payload,
            )
            if preview
            else _valid_lifecycle_commit(
                response,
                action=action,
                task_id=task_id,
                operation_id=operation_id,
                payload=payload,
                request_hash=args["requestHash"],
                base_revision=base_revision,
            )
        )
        if not valid:
            proof = "preview" if preview else "receipt"
            return _tool_error(f"Canonical task lifecycle {proof} could not be verified")
        return _tool_result(response)
    except _FlowStateApiError as exc:
        logger.error("flowstate task lifecycle typed error: code=%s status=%s", exc.code, exc.status)
        return _typed_tool_error(exc)
    except RuntimeError as exc:
        logger.error("flowstate task lifecycle runtime error: %s", type(exc).__name__)
        message = str(exc)
        if message.startswith("Flow State Local Task API"):
            return _tool_error(message)
        return _tool_error("Flow State canonical task lifecycle failed")
    except Exception as exc:
        logger.error("flowstate task lifecycle failed: %s", type(exc).__name__)
        return _tool_error("Flow State canonical task lifecycle failed")


def _handle_create_task(args: dict, **kw) -> str:
    allowed = {
        "taskId", "operationId", "title", "description", "status", "priority",
        "dueDate", "projectId", "preview", "previewDigest", "previewExpiresAt", "requestHash",
    }
    unknown = sorted(set(args) - allowed)
    if unknown:
        return _tool_error(f"unsupported canonical create fields: {', '.join(unknown)}")
    title = str(args.get("title") or "").strip()
    if not title:
        return _tool_error("title is required")

    status = args.get("status", "planned")
    if status not in _LIFECYCLE_CREATE_STATUSES:
        return _tool_error("status must be planned|in_progress|backlog|on_hold")

    priority = args.get("priority")
    if priority in ("", None):
        priority = None
    if priority is not None and priority not in _VALID_PRIORITIES:
        return _tool_error("priority must be low|medium|high or null")

    due_date = args.get("dueDate")
    if due_date in ("", None):
        due_date = None
    elif not _DATE_ONLY_RE.match(str(due_date)):
        return _tool_error("dueDate must be YYYY-MM-DD")

    project_id = args.get("projectId")
    if project_id in ("", None):
        project_id = None

    payload = {
        "title": title,
        "description": str(args.get("description") or ""),
        "status": status,
        "priority": priority,
        "dueDate": due_date,
        "projectId": project_id,
    }
    return _handle_lifecycle(args, action="create", payload=payload, base_revision=0)


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None
    except ValueError:
        return False


def _valid_canonical_read_back(value: Any, task_id: str, revision: int) -> bool:
    return (
        isinstance(value, dict)
        and value.get("id") == task_id
        and value.get("canonicalRevision") == revision
    )


def _valid_canonical_preview(payload: Any, task_id: str, operation_id: str, revision: int) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("result") == "preview"
        and payload.get("contractVersion") == _CANONICAL_TASK_CONTRACT
        and payload.get("operationId") == operation_id
        and payload.get("baseRevision") == revision
        and isinstance(payload.get("previewDigest"), str)
        and bool(_SHA256_HEX_RE.fullmatch(payload["previewDigest"]))
        and isinstance(payload.get("requestHash"), str)
        and bool(_SHA256_HEX_RE.fullmatch(payload["requestHash"]))
        and _is_iso_timestamp(payload.get("previewExpiresAt"))
        and isinstance(payload.get("normalizedPayload"), dict)
        and _valid_canonical_read_back(payload.get("readBack"), task_id, revision)
    )


def _valid_canonical_commit(
    payload: Any,
    task_id: str,
    operation_id: str,
    request_hash: str,
) -> bool:
    if not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("result") != "committed":
        return False
    if payload.get("requestHash") != request_hash:
        return False
    receipt = payload.get("receipt")
    if not isinstance(receipt, dict):
        return False
    try:
        validate_nested_canonical_receipt(
            receipt,
            expected={
                "contractVersion": _CANONICAL_TASK_CONTRACT,
                "operationId": operation_id,
                "source": _CANONICAL_TASK_SOURCE,
                "entityType": "task",
                "action": "patch",
                "entityId": task_id,
                "requestHash": request_hash,
            },
        )
    except CanonicalReceiptError:
        return False
    return True


def _validate_canonical_patch(patch: Any) -> Optional[str]:
    if not isinstance(patch, dict) or not patch:
        return "patch must contain at least one field"
    unknown = sorted(set(patch) - _CANONICAL_PATCH_FIELDS)
    if unknown:
        return f"unsupported patch fields: {', '.join(unknown)}"
    if "title" in patch and (not isinstance(patch["title"], str) or not patch["title"].strip()):
        return "patch.title must be a non-empty string"
    if "description" in patch and not isinstance(patch["description"], str):
        return "patch.description must be a string"
    if "priority" in patch and patch["priority"] is not None and patch["priority"] not in _VALID_PRIORITIES:
        return "patch.priority must be low|medium|high or null"
    if "dueDate" in patch and patch["dueDate"] is not None:
        if not isinstance(patch["dueDate"], str) or not _DATE_ONLY_RE.fullmatch(patch["dueDate"]):
            return "patch.dueDate must be YYYY-MM-DD or null"
    if "progress" in patch:
        progress = patch["progress"]
        if not isinstance(progress, int) or isinstance(progress, bool) or not 0 <= progress <= 100:
            return "patch.progress must be an integer from 0 to 100"
    return None


def _handle_update_task(args: dict, **kw) -> str:
    unknown = sorted(set(args) - _CANONICAL_UPDATE_FIELDS)
    if unknown:
        return _tool_error(f"unsupported canonical update fields: {', '.join(unknown)}")

    task_id = args.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        return _tool_error("id is required")
    task_id = task_id.strip()

    operation_id = args.get("operationId")
    if (
        not isinstance(operation_id, str)
        or not operation_id.strip()
        or operation_id != operation_id.strip()
        or len(operation_id) > 160
    ):
        return _tool_error("operationId is required and must be at most 160 trimmed characters")

    base_revision = args.get("baseRevision")
    if not _is_positive_int(base_revision):
        return _tool_error("baseRevision is required and must be a positive integer")

    patch = args.get("patch")
    patch_error = _validate_canonical_patch(patch)
    if patch_error:
        return _tool_error(patch_error)

    preview = args.get("preview", True)
    if not isinstance(preview, bool):
        return _tool_error("preview must be a boolean")

    body: Dict[str, Any] = {
        "operationId": operation_id,
        "baseRevision": base_revision,
        "patch": patch,
        "preview": preview,
    }
    if not preview:
        digest = args.get("previewDigest")
        expiry = args.get("previewExpiresAt")
        request_hash = args.get("requestHash")
        if not isinstance(digest, str) or not _SHA256_HEX_RE.fullmatch(digest):
            return _tool_error("previewDigest is required when preview is false")
        if not _is_iso_timestamp(expiry):
            return _tool_error("previewExpiresAt is required when preview is false")
        if not isinstance(request_hash, str) or not _SHA256_HEX_RE.fullmatch(request_hash):
            return _tool_error("requestHash is required when preview is false")
        body["previewDigest"] = digest
        body["previewExpiresAt"] = expiry
        body["requestHash"] = request_hash

    try:
        payload = _request("PATCH", f"/api/tasks/{urllib.parse.quote(task_id, safe='')}", body)
        if preview:
            if not _valid_canonical_preview(payload, task_id, operation_id, base_revision):
                return _tool_error("Canonical task preview could not be verified")
        elif not _valid_canonical_commit(payload, task_id, operation_id, args["requestHash"]):
            return _tool_error("Canonical task receipt could not be verified")
        return _tool_result(payload)
    except _FlowStateApiError as exc:
        logger.error("flowstate_update_task typed error: code=%s status=%s", exc.code, exc.status)
        return _typed_tool_error(exc)
    except RuntimeError as exc:
        logger.error("flowstate_update_task runtime error: %s", type(exc).__name__)
        message = str(exc)
        if message.startswith("Flow State Local Task API"):
            return _tool_error(message)
        return _tool_error("Flow State canonical task update failed")
    except Exception as exc:
        logger.error("flowstate_update_task error: %s", type(exc).__name__)
        return _tool_error("Flow State canonical task update failed")


def _handle_delete_task(args: dict, **kw) -> str:
    return _handle_revision_lifecycle(args, action="soft_delete", payload={})


def _handle_restore_task(args: dict, **kw) -> str:
    return _handle_revision_lifecycle(args, action="restore", payload={})


def _handle_set_task_status(args: dict, **kw) -> str:
    status = args.get("status")
    if status not in _LIFECYCLE_STATUSES:
        return _tool_error("status must be planned|in_progress|done|backlog|on_hold")
    return _handle_revision_lifecycle(args, action="set_status", payload={"status": status})


def _handle_revision_lifecycle(args: dict, *, action: str, payload: Dict[str, Any]) -> str:
    allowed = {
        "taskId", "operationId", "baseRevision", "preview", "previewDigest", "previewExpiresAt", "requestHash",
    }
    if action == "set_status":
        allowed.add("status")
    unknown = sorted(set(args) - allowed)
    if unknown:
        return _tool_error(f"unsupported canonical lifecycle fields: {', '.join(unknown)}")
    base_revision = args.get("baseRevision")
    if not _is_positive_int(base_revision):
        return _tool_error("baseRevision is required and must be a positive integer")
    return _handle_lifecycle(
        args,
        action=action,
        payload=payload,
        base_revision=base_revision,
    )


def _handle_current_timer(args: dict, **kw) -> str:
    try:
        return _tool_result(_request("GET", "/api/timer/current"))
    except Exception as exc:
        logger.error("flowstate_get_current_timer error: %s", exc)
        return _tool_error(str(exc))


def _handle_timer_diagnostics(args: dict, **kw) -> str:
    try:
        return _tool_result(_request("GET", "/api/timer/diagnostics"))
    except Exception as exc:
        logger.error("flowstate_get_timer_diagnostics error: %s", exc)
        return _tool_error(str(exc))


def _handle_list_task_instances(args: dict, **kw) -> str:
    task_id = str(args.get("id") or "").strip()
    if not task_id:
        return _tool_error("id is required")

    try:
        return _tool_result(_request("GET", f"/api/tasks/{urllib.parse.quote(task_id, safe='')}/instances"))
    except Exception as exc:
        logger.error("flowstate_list_task_instances error: %s", exc)
        return _tool_error(str(exc))


def _handle_schedule_task_instance(args: dict, **kw) -> str:
    task_id = str(args.get("id") or "").strip()
    if not task_id:
        return _tool_error("id is required")

    scheduled_date = str(args.get("scheduledDate") or "").strip()
    if not _DATE_ONLY_RE.match(scheduled_date):
        return _tool_error("scheduledDate must be YYYY-MM-DD")

    scheduled_time = str(args.get("scheduledTime") or "").strip()
    if not _TIME_ONLY_RE.match(scheduled_time):
        return _tool_error("scheduledTime must be HH:mm")

    try:
        duration = int(args.get("duration"))
    except (TypeError, ValueError):
        return _tool_error("duration must be an integer from 1 to 1440")
    if duration < 1 or duration > 1440:
        return _tool_error("duration must be an integer from 1 to 1440")

    body = {
        "scheduledDate": scheduled_date,
        "scheduledTime": scheduled_time,
        "duration": duration,
        "preview": False if args.get("preview") is False else True,
    }

    try:
        return _tool_result(_request("POST", f"/api/tasks/{urllib.parse.quote(task_id, safe='')}/instances", body))
    except Exception as exc:
        logger.error("flowstate_schedule_task_instance error: %s", exc)
        return _tool_error(str(exc))


def _valid_local_date(value: Any) -> bool:
    if not isinstance(value, str) or not _DATE_ONLY_RE.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _valid_local_minute(value: Any) -> bool:
    if not isinstance(value, str) or not _LOCAL_MINUTE_RE.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M")
        return True
    except ValueError:
        return False


def _valid_timezone_name(value: Any) -> bool:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 100:
        return False
    try:
        ZoneInfo(value)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def _normalize_work_block_command(args: dict, action: str) -> Optional[dict]:
    work_block_id = args.get("workBlockId")
    if not _valid_lifecycle_task_id(work_block_id):
        return None
    work_block_id = work_block_id.lower()
    finish_by = args.get("finishBy")
    if finish_by is not None and not _valid_local_minute(finish_by):
        return None
    if action in {"create", "move"}:
        scheduled_date = args.get("scheduledDate")
        scheduled_time = args.get("scheduledTime")
        timezone_name = args.get("timezone")
        if (
            not _valid_local_date(scheduled_date)
            or not isinstance(scheduled_time, str)
            or not _TIME_ONLY_RE.fullmatch(scheduled_time)
            or not _valid_timezone_name(timezone_name)
        ):
            return None
    if action in {"create", "resize"}:
        duration = args.get("duration")
        if not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= 1440:
            return None
    if action == "create":
        command: Dict[str, Any] = {
            "action": "create",
            "workBlock": {
                "id": work_block_id,
                "scheduledDate": args["scheduledDate"],
                "scheduledTime": args["scheduledTime"],
                "duration": args["duration"],
                "timezone": args["timezone"],
            },
        }
    elif action == "move":
        command = {
            "action": "move",
            "workBlockId": work_block_id,
            "scheduledDate": args["scheduledDate"],
            "scheduledTime": args["scheduledTime"],
            "timezone": args["timezone"],
        }
    elif action == "resize":
        command = {"action": "resize", "workBlockId": work_block_id, "duration": args["duration"]}
    else:
        command = {"action": "remove", "workBlockId": work_block_id}
    if finish_by is not None:
        command["finishBy"] = finish_by
    return command


def _work_block_matches_command(
    work_block: Any,
    *,
    action: str,
    task_id: str,
    work_block_id: str,
    work_block_revision: int,
    command: dict,
) -> bool:
    if not (
        isinstance(work_block, dict)
        and work_block.get("id") == work_block_id
        and work_block.get("taskId") == task_id
        and work_block.get("canonicalRevision") == (1 if action == "create" else work_block_revision + 1)
    ):
        return False
    expected = command["workBlock"] if action == "create" else command
    fields = (
        ("scheduledDate", "scheduledTime", "duration", "timezone")
        if action == "create"
        else ("scheduledDate", "scheduledTime", "timezone")
        if action == "move"
        else ("duration",)
    )
    return all(work_block.get(field) == expected.get(field) for field in fields)


def _valid_work_block_instances(value: Any, task_id: str) -> bool:
    if not isinstance(value, list):
        return False
    ids: list[str] = []
    for instance in value:
        if not isinstance(instance, dict) or not isinstance(instance.get("id"), str) or not instance["id"]:
            return False
        if instance.get("taskId") not in (None, task_id):
            return False
        # The existing instances column also contains legacy recurrence
        # occurrences. Preserve those opaque rows, but validate every row that
        # claims canonical work-block revision semantics in full. The command
        # target itself is checked separately by _work_block_matches_command.
        if "canonicalRevision" in instance and not (
            instance.get("taskId") == task_id
            and _valid_local_date(instance.get("scheduledDate"))
            and isinstance(instance.get("scheduledTime"), str)
            and bool(_TIME_ONLY_RE.fullmatch(instance["scheduledTime"]))
            and isinstance(instance.get("duration"), int)
            and not isinstance(instance.get("duration"), bool)
            and 1 <= instance["duration"] <= 1440
            and _valid_timezone_name(instance.get("timezone"))
            and isinstance(instance.get("canonicalRevision"), int)
            and not isinstance(instance.get("canonicalRevision"), bool)
            and instance["canonicalRevision"] > 0
        ):
            return False
        ids.append(instance["id"])
    return len(ids) == len(set(ids))


def _valid_work_block_read_back(
    read_back: Any,
    *,
    action: str,
    task_id: str,
    work_block_id: str,
    work_block_revision: int,
    command: dict,
    expected_task_revision: int,
) -> bool:
    if not (
        isinstance(read_back, dict)
        and read_back.get("id") == task_id
        and read_back.get("canonicalRevision") == expected_task_revision
        and "workspaceId" in read_back
        and (read_back["workspaceId"] is None or isinstance(read_back["workspaceId"], str))
        and "workBlock" in read_back
        and "removedWorkBlockId" in read_back
        and _valid_work_block_instances(read_back.get("instances"), task_id)
    ):
        return False
    matching = [
        instance for instance in read_back["instances"]
        if isinstance(instance, dict) and instance.get("id") == work_block_id
    ]
    if action == "remove":
        return (
            not matching
            and read_back.get("workBlock") is None
            and read_back.get("removedWorkBlockId") == work_block_id
        )
    work_block = read_back.get("workBlock")
    if work_block is None and len(matching) == 1:
        work_block = matching[0]
    return (
        read_back.get("removedWorkBlockId") is None
        and len(matching) == 1
        and matching[0] == work_block
        and _work_block_matches_command(
            work_block,
            action=action,
            task_id=task_id,
            work_block_id=work_block_id,
            work_block_revision=work_block_revision,
            command=command,
        )
    )


def _valid_work_block_preview_evidence(
    response: Any,
    *,
    action: str,
    task_id: str,
    operation_id: str,
    base_revision: int,
    work_block_revision: int,
    command: dict,
) -> bool:
    work_block_id = command.get("workBlockId") or command.get("workBlock", {}).get("id")
    if not (
        isinstance(response, dict)
        and response.get("ok") is True
        and response.get("status") == "preview"
        and response.get("result") == "preview"
        and isinstance(response.get("requestHash"), str)
        and bool(_SHA256_HEX_RE.fullmatch(response["requestHash"]))
        and isinstance(response.get("previewDigest"), str)
        and bool(_SHA256_HEX_RE.fullmatch(response["previewDigest"]))
        and _is_iso_timestamp(response.get("previewExpiresAt"))
    ):
        return False
    normalized = response.get("normalizedPayload")
    if not (
        isinstance(normalized, dict)
        and set(normalized) == {
            "contractVersion", "source", "action", "taskId", "baseRevision",
            "workBlockRevision", "workspaceId", "command",
        }
        and normalized.get("contractVersion") == _WORK_BLOCK_CONTRACT
        and normalized.get("source") == _CANONICAL_TASK_SOURCE
        and normalized.get("action") == action
        and normalized.get("taskId") == task_id
        and normalized.get("baseRevision") == base_revision
        and normalized.get("workBlockRevision") == work_block_revision
        and normalized.get("command") == command
        and (normalized.get("workspaceId") is None or isinstance(normalized.get("workspaceId"), str))
        and canonical_json_sha256(normalized) == response["requestHash"]
    ):
        return False
    evidence = response.get("preview")
    if not (
        isinstance(evidence, dict)
        and evidence.get("action") == action
        and evidence.get("workBlockId") == work_block_id
        and isinstance(evidence.get("interval"), dict)
        and set(evidence["interval"]) == {"before", "after"}
        and isinstance(evidence.get("duration"), dict)
        and set(evidence["duration"]) == {"beforeMinutes", "afterMinutes"}
        and isinstance(evidence.get("timezone"), str)
        and isinstance(evidence.get("overlapWarnings"), list)
        and isinstance(evidence.get("taskEffect"), dict)
        and evidence["taskEffect"].get("taskId") == task_id
        and isinstance(evidence["taskEffect"].get("dueDate"), dict)
        and evidence["taskEffect"]["dueDate"].get("before") == evidence["taskEffect"]["dueDate"].get("after")
    ):
        return False
    for interval in evidence["interval"].values():
        if interval is not None and (
            not isinstance(interval, dict)
            or set(interval) != {"localStart", "localEnd"}
            or not _valid_local_minute(interval.get("localStart"))
            or not _valid_local_minute(interval.get("localEnd"))
            or datetime.strptime(interval["localEnd"], "%Y-%m-%dT%H:%M")
            <= datetime.strptime(interval["localStart"], "%Y-%m-%dT%H:%M")
        ):
            return False
    due_date = evidence["taskEffect"]["dueDate"].get("before")
    if due_date is not None and not _valid_local_date(due_date):
        return False
    before_duration = evidence["duration"].get("beforeMinutes")
    after_duration = evidence["duration"].get("afterMinutes")
    if any(
        value is not None
        and (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 1 <= value <= 1440
        )
        for value in (before_duration, after_duration)
    ):
        return False
    if action == "create":
        start = datetime.strptime(
            f"{command['workBlock']['scheduledDate']}T{command['workBlock']['scheduledTime']}",
            "%Y-%m-%dT%H:%M",
        )
        after = evidence["interval"].get("after")
        if (
            evidence["interval"].get("before") is not None
            or not isinstance(after, dict)
            or after.get("localStart") != start.strftime("%Y-%m-%dT%H:%M")
            or after.get("localEnd") != (start + timedelta(minutes=command["workBlock"]["duration"])).strftime("%Y-%m-%dT%H:%M")
            or evidence.get("timezone") != command["workBlock"]["timezone"]
            or evidence["duration"].get("afterMinutes") != command["workBlock"]["duration"]
            or before_duration is not None
        ):
            return False
    elif action == "move":
        if (
            evidence.get("timezone") != command["timezone"]
            or not isinstance(evidence["interval"].get("before"), dict)
            or not isinstance(evidence["interval"].get("after"), dict)
            or evidence["interval"]["after"].get("localStart")
            != f"{command['scheduledDate']}T{command['scheduledTime']}"
            or before_duration != after_duration
        ):
            return False
    elif action == "resize":
        if (
            not isinstance(evidence["interval"].get("before"), dict)
            or not isinstance(evidence["interval"].get("after"), dict)
            or evidence["interval"]["before"].get("localStart")
            != evidence["interval"]["after"].get("localStart")
            or after_duration != command["duration"]
        ):
            return False
    elif action == "remove" and (
        not isinstance(evidence["interval"].get("before"), dict)
        or evidence["interval"].get("after") is not None
        or after_duration is not None
    ):
        return False
    for warning in evidence["overlapWarnings"]:
        if not (
            isinstance(warning, dict)
            and set(warning) == {"taskId", "workBlockId", "localStart", "timezone"}
            and _valid_lifecycle_task_id(warning.get("taskId"))
            and _valid_lifecycle_task_id(warning.get("workBlockId"))
            and _valid_local_minute(warning.get("localStart"))
            and _valid_timezone_name(warning.get("timezone"))
        ):
            return False
    finish_by = command.get("finishBy")
    boundary = evidence.get("finishByBoundary")
    if (finish_by is None and boundary is not None) or (
        finish_by is not None
        and not (
            isinstance(boundary, dict)
            and boundary.get("finishBy") == finish_by
            and boundary.get("satisfied") is True
        )
    ):
        return False
    preview_read_back = response.get("readBack")
    return (
        isinstance(preview_read_back, dict)
        and preview_read_back.get("id") == task_id
        and preview_read_back.get("canonicalRevision") == base_revision
        and _valid_work_block_instances(preview_read_back.get("instances"), task_id)
        and (
            not any(
                isinstance(instance, dict) and instance.get("id") == work_block_id
                for instance in preview_read_back["instances"]
            )
            if action == "remove"
            else any(
                _work_block_matches_command(
                    instance,
                    action=action,
                    task_id=task_id,
                    work_block_id=work_block_id,
                    work_block_revision=work_block_revision,
                    command=command,
                )
                for instance in preview_read_back["instances"]
            )
        )
    )


def _valid_work_block_commit(
    response: Any,
    *,
    action: str,
    task_id: str,
    operation_id: str,
    base_revision: int,
    work_block_revision: int,
    command: dict,
    request_hash: str,
) -> bool:
    work_block_id = command.get("workBlockId") or command.get("workBlock", {}).get("id")
    if not (
        isinstance(response, dict)
        and response.get("ok") is True
        and response.get("status") == "committed"
        and response.get("result") == "committed"
        and response.get("requestHash") == request_hash
        and isinstance(response.get("receipt"), dict)
        and response["receipt"].get("status") == "committed"
        and response["receipt"].get("requestHash") == request_hash
        and response["receipt"].get("workBlockId") == work_block_id
        and response["receipt"].get("canonicalRevision") == base_revision + 1
    ):
        return False
    try:
        validate_nested_canonical_receipt(
            response["receipt"],
            expected={
                "contractVersion": _WORK_BLOCK_CONTRACT,
                "operationId": operation_id,
                "source": _CANONICAL_TASK_SOURCE,
                "entityType": "task",
                "action": f"work_block_{action}",
                "entityId": task_id,
            },
            valid_read_back=lambda read_back: _valid_work_block_read_back(
                read_back,
                action=action,
                task_id=task_id,
                work_block_id=work_block_id,
                work_block_revision=work_block_revision,
                command=command,
                expected_task_revision=base_revision + 1,
            ),
        )
    except CanonicalReceiptError:
        return False
    return True


def _handle_work_block(args: dict, *, action: str) -> str:
    action_fields = {
        "create": {"scheduledDate", "scheduledTime", "duration", "timezone", "finishBy"},
        "move": {"scheduledDate", "scheduledTime", "timezone", "finishBy"},
        "resize": {"duration", "finishBy"},
        "remove": set(),
    }[action]
    common = {
        "taskId", "workBlockId", "operationId", "baseRevision", "workBlockRevision",
        "preview", "previewDigest", "previewExpiresAt", "requestHash",
    }
    unknown = set(args) - common - action_fields
    if unknown:
        return _tool_error(f"unsupported work-block fields: {', '.join(sorted(unknown))}")
    task_id = args.get("taskId")
    if not _valid_lifecycle_task_id(task_id):
        return _tool_error("taskId is required and must be a stable UUID")
    task_id = task_id.lower()
    operation_id = args.get("operationId")
    if not _valid_operation_id(operation_id):
        return _tool_error("operationId is required and must be at most 160 trimmed characters")
    base_revision = args.get("baseRevision")
    work_block_revision = args.get("workBlockRevision")
    if not isinstance(base_revision, int) or isinstance(base_revision, bool) or base_revision < 1:
        return _tool_error("baseRevision is required and must be a positive integer")
    if (
        not isinstance(work_block_revision, int)
        or isinstance(work_block_revision, bool)
        or (action == "create" and work_block_revision != 0)
        or (action != "create" and work_block_revision < 1)
    ):
        return _tool_error("workBlockRevision must be 0 for create and positive for existing blocks")
    command = _normalize_work_block_command(args, action)
    if command is None:
        return _tool_error("work-block command does not match the canonical interval contract")
    preview = args.get("preview", True)
    if not isinstance(preview, bool):
        return _tool_error("preview must be a boolean")
    body: Dict[str, Any] = {
        "operationId": operation_id,
        "baseRevision": base_revision,
        "workBlockRevision": work_block_revision,
        "command": command,
        "preview": preview,
    }
    if not preview:
        digest = args.get("previewDigest")
        expiry = args.get("previewExpiresAt")
        request_hash = args.get("requestHash")
        if not isinstance(digest, str) or not _SHA256_HEX_RE.fullmatch(digest):
            return _tool_error("previewDigest is required when preview is false")
        if not _is_iso_timestamp(expiry):
            return _tool_error("previewExpiresAt is required when preview is false")
        if not isinstance(request_hash, str) or not _SHA256_HEX_RE.fullmatch(request_hash):
            return _tool_error("requestHash is required when preview is false")
        body["previewDigest"] = digest
        body["previewExpiresAt"] = expiry
    try:
        response = _request(
            "POST", f"/api/tasks/{urllib.parse.quote(task_id, safe='')}/work-blocks", body
        )
        valid = (
            _valid_work_block_preview_evidence(
                response,
                action=action,
                task_id=task_id,
                operation_id=operation_id,
                base_revision=base_revision,
                work_block_revision=work_block_revision,
                command=command,
            )
            if preview
            else _valid_work_block_commit(
                response,
                action=action,
                task_id=task_id,
                operation_id=operation_id,
                base_revision=base_revision,
                work_block_revision=work_block_revision,
                command=command,
                request_hash=args["requestHash"],
            )
        )
        if not valid:
            return _tool_error(
                f"Canonical work-block {'preview' if preview else 'receipt'} could not be verified"
            )
        return _tool_result(response)
    except _FlowStateApiError as exc:
        logger.error("flowstate work-block API error: status=%s code=%s", exc.status, exc.code)
        return _typed_tool_error(exc)
    except Exception as exc:
        logger.error("flowstate work-block error: %s", type(exc).__name__)
        return _tool_error("Flow State canonical work-block command failed")


def _handle_create_work_block(args: dict, **kw) -> str:
    return _handle_work_block(args, action="create")


def _handle_move_work_block(args: dict, **kw) -> str:
    return _handle_work_block(args, action="move")


def _handle_resize_work_block(args: dict, **kw) -> str:
    return _handle_work_block(args, action="resize")


def _handle_remove_work_block(args: dict, **kw) -> str:
    return _handle_work_block(args, action="remove")


def _handle_done_for_now(args: dict, **kw) -> str:
    task_id = str(args.get("taskId") or "").strip()
    if not task_id:
        return _tool_error("taskId is required")

    next_due_date = args.get("nextDueDate")
    if next_due_date in ("", None):
        next_due_date = None
    elif not _DATE_ONLY_RE.match(str(next_due_date)):
        return _tool_error("nextDueDate must be YYYY-MM-DD")

    preview = False if args.get("preview") is False else True
    request_id = str(args.get("requestId") or "").strip()
    preview_version = str(args.get("previewVersion") or "").strip()
    request_hash = str(args.get("requestHash") or "").strip()
    if not request_id:
        return _tool_error("requestId is required")
    if not preview and not preview_version:
        return _tool_error("previewVersion is required when preview is false")
    if not preview and not _SHA256_HEX_RE.fullmatch(request_hash):
        return _tool_error("requestHash is required when preview is false")

    body: Dict[str, Any] = {"preview": preview}
    if next_due_date is not None:
        body["nextDueDate"] = next_due_date
    if request_id:
        body["requestId"] = request_id
    if preview_version:
        body["previewVersion"] = preview_version
    if request_hash:
        body["requestHash"] = request_hash

    try:
        path = f"/api/tasks/{urllib.parse.quote(task_id, safe='')}/done-for-now"
        return _tool_result(_request("POST", path, body))
    except _FlowStateApiError as exc:
        logger.error(
            "flowstate_done_for_now API error: status=%s code=%s",
            exc.status,
            exc.code,
        )
        return _typed_tool_error(exc)
    except Exception as exc:
        logger.error("flowstate_done_for_now error: %s", exc)
        return _tool_error(str(exc))


def _is_canonical_recurrence_rule(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    pattern = value.get("pattern")
    allowed_pattern_fields = _RECURRENCE_PATTERN_FIELDS.get(pattern)
    if allowed_pattern_fields is None:
        return False
    if set(value) - (_RECURRENCE_COMMON_FIELDS | allowed_pattern_fields):
        return False
    interval = value.get("interval")
    if not isinstance(interval, int) or isinstance(interval, bool) or not 1 <= interval <= 365:
        return False
    end_type = value.get("endType")
    if end_type not in {"never", "after_count", "on_date"}:
        return False
    if end_type == "never" and ("endDate" in value or "endCount" in value):
        return False
    if end_type == "after_count":
        end_count = value.get("endCount")
        if not isinstance(end_count, int) or isinstance(end_count, bool) or end_count < 1 or "endDate" in value:
            return False
    if end_type == "on_date":
        end_date = value.get("endDate")
        if not isinstance(end_date, str) or not _DATE_ONLY_RE.fullmatch(end_date) or "endCount" in value:
            return False
    if pattern == "weekly":
        weekdays = value.get("weekdays")
        if (
            not isinstance(weekdays, list)
            or not weekdays
            or len(weekdays) != len(set(weekdays))
            or any(not isinstance(day, int) or isinstance(day, bool) or not 0 <= day <= 6 for day in weekdays)
        ):
            return False
    if pattern == "monthly":
        month_day = value.get("monthDay")
        month_weekday = value.get("monthWeekday")
        if (month_day is None) == (month_weekday is None):
            return False
        if month_day is not None and (
            not isinstance(month_day, int) or isinstance(month_day, bool) or not 1 <= month_day <= 31
        ):
            return False
        if month_weekday is not None and (
            not isinstance(month_weekday, dict)
            or set(month_weekday) != {"nth", "day"}
            or not isinstance(month_weekday.get("nth"), int)
            or isinstance(month_weekday.get("nth"), bool)
            or month_weekday["nth"] not in {-1, 1, 2, 3, 4, 5}
            or not isinstance(month_weekday.get("day"), int)
            or isinstance(month_weekday.get("day"), bool)
            or not 0 <= month_weekday["day"] <= 6
        ):
            return False
    return True


def _handle_merge_tasks(args: dict, **kw) -> str:
    survivor_task_id = str(args.get("survivorTaskId") or "").strip()
    if not survivor_task_id:
        return _tool_error("survivorTaskId is required")
    duplicate_task_id = str(args.get("duplicateTaskId") or "").strip()
    if not duplicate_task_id:
        return _tool_error("duplicateTaskId is required")
    if survivor_task_id == duplicate_task_id:
        return _tool_error("survivorTaskId and duplicateTaskId must be different")

    preview = False if args.get("preview") is False else True
    request_id = str(args.get("requestId") or "").strip()
    preview_version = str(args.get("previewVersion") or "").strip()
    request_hash = str(args.get("requestHash") or "").strip()
    if not request_id:
        return _tool_error("requestId is required")
    if not preview and not preview_version:
        return _tool_error("previewVersion is required when preview is false")
    if not preview and not _SHA256_HEX_RE.fullmatch(request_hash):
        return _tool_error("requestHash is required when preview is false")
    recurrence_resolution = args.get("recurrenceResolution")
    if recurrence_resolution is not None and not _is_canonical_recurrence_rule(recurrence_resolution):
        return _tool_error("recurrenceResolution must be a canonical recurrence rule")

    body: Dict[str, Any] = {
        "duplicateTaskId": duplicate_task_id,
        "preview": preview,
    }
    if request_id:
        body["requestId"] = request_id
    if preview_version:
        body["previewVersion"] = preview_version
    if request_hash:
        body["requestHash"] = request_hash
    if recurrence_resolution is not None:
        body["recurrenceResolution"] = recurrence_resolution

    try:
        path = f"/api/tasks/{urllib.parse.quote(survivor_task_id, safe='')}/merge"
        return _tool_result(_request("POST", path, body))
    except _FlowStateApiError as exc:
        logger.error(
            "flowstate_merge_tasks API error: status=%s code=%s",
            exc.status,
            exc.code,
        )
        return _typed_tool_error(exc)
    except Exception as exc:
        logger.error("flowstate_merge_tasks error: %s", exc)
        return _tool_error(str(exc))


def _subtask_path(task_id: str, subtask_id: Optional[str] = None) -> str:
    path = f"/api/tasks/{urllib.parse.quote(task_id, safe='')}/subtasks"
    if subtask_id is not None:
        path += f"/{urllib.parse.quote(subtask_id, safe='')}"
    return path


def _preview_metadata(args: dict) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "preview": False if args.get("preview") is False else True,
    }
    request_id = str(args.get("requestId") or "").strip()
    if request_id:
        metadata["requestId"] = request_id
    return metadata


def _validate_apply_request(body: Dict[str, Any]) -> Optional[str]:
    if body["preview"] is False and not body.get("requestId"):
        return "requestId is required when preview is false"
    return None


def _handle_list_subtasks(args: dict, **kw) -> str:
    task_id = str(args.get("taskId") or "").strip()
    if not task_id:
        return _tool_error("taskId is required")
    try:
        return _tool_result(_request("GET", _subtask_path(task_id)))
    except Exception as exc:
        logger.error("flowstate_list_subtasks error: %s", exc)
        return _tool_error(str(exc))


def _handle_create_subtask(args: dict, **kw) -> str:
    task_id = str(args.get("taskId") or "").strip()
    if not task_id:
        return _tool_error("taskId is required")
    title = str(args.get("title") or "").strip()
    if not title:
        return _tool_error("title is required")

    body: Dict[str, Any] = {"title": title, **_preview_metadata(args)}
    if "order" in args and args.get("order") is not None:
        try:
            order = int(args["order"])
        except (TypeError, ValueError):
            return _tool_error("order must be a non-negative integer")
        if order < 0:
            return _tool_error("order must be a non-negative integer")
        body["order"] = order
    error = _validate_apply_request(body)
    if error:
        return _tool_error(error)

    try:
        return _tool_result(_request("POST", _subtask_path(task_id), body))
    except Exception as exc:
        logger.error("flowstate_create_subtask error: %s", exc)
        return _tool_error(str(exc))


def _handle_update_subtask(args: dict, **kw) -> str:
    task_id = str(args.get("taskId") or "").strip()
    if not task_id:
        return _tool_error("taskId is required")
    subtask_id = str(args.get("subtaskId") or "").strip()
    if not subtask_id:
        return _tool_error("subtaskId is required")

    body: Dict[str, Any] = _preview_metadata(args)
    if "title" in args:
        title = str(args.get("title") or "").strip()
        if not title:
            return _tool_error("title cannot be empty")
        body["title"] = title
    if "completed" in args:
        if not isinstance(args.get("completed"), bool):
            return _tool_error("completed must be a boolean")
        body["completed"] = args["completed"]
    if "order" in args:
        try:
            order = int(args["order"])
        except (TypeError, ValueError):
            return _tool_error("order must be a non-negative integer")
        if order < 0:
            return _tool_error("order must be a non-negative integer")
        body["order"] = order
    if not any(field in body for field in ("title", "completed", "order")):
        return _tool_error("provide at least one field to update")
    error = _validate_apply_request(body)
    if error:
        return _tool_error(error)

    try:
        return _tool_result(_request("PATCH", _subtask_path(task_id, subtask_id), body))
    except Exception as exc:
        logger.error("flowstate_update_subtask error: %s", exc)
        return _tool_error(str(exc))


def _handle_delete_subtask(args: dict, **kw) -> str:
    task_id = str(args.get("taskId") or "").strip()
    if not task_id:
        return _tool_error("taskId is required")
    subtask_id = str(args.get("subtaskId") or "").strip()
    if not subtask_id:
        return _tool_error("subtaskId is required")
    body = _preview_metadata(args)
    error = _validate_apply_request(body)
    if error:
        return _tool_error(error)

    try:
        # A POST action keeps preview payloads portable; a DELETE body is not
        # handled consistently by all localhost proxies and HTTP clients.
        return _tool_result(_request("POST", f"{_subtask_path(task_id, subtask_id)}/delete", body))
    except Exception as exc:
        logger.error("flowstate_delete_subtask error: %s", exc)
        return _tool_error(str(exc))


def _normalize_subtask_batch_operations(value: Any) -> Optional[list[dict]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 50:
        return None
    subtask_fields = {
        "id", "title", "description", "isCompleted", "completedPomodoros",
        "doneEnough", "estimateMinutes",
    }
    patch_fields = subtask_fields - {"id"}
    normalized_operations: list[dict] = []
    for operation in value:
        if not isinstance(operation, dict) or operation.get("action") not in {
            "create", "update", "delete",
        }:
            return None
        action = operation["action"]
        allowed = (
            {"action", "subtask", "order"}
            if action == "create"
            else {"action", "subtaskId", "patch", "order"}
            if action == "update"
            else {"action", "subtaskId"}
        )
        if not set(operation).issubset(allowed):
            return None
        order = operation.get("order")
        if order is not None and (
            not isinstance(order, int) or isinstance(order, bool) or order < 0
            or order > 100000
        ):
            return None
        if action == "create":
            subtask = operation.get("subtask")
            if (
                not isinstance(subtask, dict)
                or not set(subtask).issubset(subtask_fields)
                or not _valid_lifecycle_task_id(subtask.get("id"))
                or not isinstance(subtask.get("title"), str)
                or not subtask["title"].strip()
                or not isinstance(subtask.get("doneEnough"), str)
                or not subtask["doneEnough"].strip()
            ):
                return None
        elif action == "update":
            patch = operation.get("patch")
            if (
                not isinstance(operation.get("subtaskId"), str)
                or not operation["subtaskId"].strip()
                or not isinstance(patch, dict)
                or (not patch and order is None)
                or not set(patch).issubset(patch_fields)
            ):
                return None
        elif (
            not isinstance(operation.get("subtaskId"), str)
            or not operation["subtaskId"].strip()
        ):
            return None
        candidate = operation.get("subtask") if action == "create" else operation.get("patch")
        if isinstance(candidate, dict):
            if "title" in candidate and (
                not isinstance(candidate["title"], str)
                or not candidate["title"].strip()
                or len(candidate["title"].strip()) > 500
            ):
                return None
            if "description" in candidate and (
                not isinstance(candidate["description"], str)
                or len(candidate["description"]) > 10000
            ):
                return None
            if "doneEnough" in candidate and (
                not isinstance(candidate["doneEnough"], str)
                or not candidate["doneEnough"].strip()
                or candidate["doneEnough"].strip() != candidate["doneEnough"]
                or len(candidate["doneEnough"]) > 1000
            ):
                return None
            if "isCompleted" in candidate and not isinstance(candidate["isCompleted"], bool):
                return None
            if "completedPomodoros" in candidate and (
                not isinstance(candidate["completedPomodoros"], int)
                or isinstance(candidate["completedPomodoros"], bool)
                or not 0 <= candidate["completedPomodoros"] <= 100000
            ):
                return None
            if "estimateMinutes" in candidate and (
                candidate["estimateMinutes"] is not None
                and (
                    not isinstance(candidate["estimateMinutes"], int)
                    or isinstance(candidate["estimateMinutes"], bool)
                    or not 1 <= candidate["estimateMinutes"] <= 10080
                )
            ):
                return None
        if action == "create":
            subtask = operation["subtask"]
            normalized: Dict[str, Any] = {
                "action": "create",
                "subtask": {
                    "id": subtask["id"].lower(),
                    "title": subtask["title"].strip(),
                    "description": subtask.get("description", ""),
                    "isCompleted": subtask.get("isCompleted", False),
                    "completedPomodoros": subtask.get("completedPomodoros", 0),
                    "doneEnough": subtask.get("doneEnough"),
                    "estimateMinutes": subtask.get("estimateMinutes"),
                },
            }
        elif action == "update":
            normalized_patch = dict(operation["patch"])
            if "title" in normalized_patch:
                normalized_patch["title"] = normalized_patch["title"].strip()
            target_id = operation["subtaskId"].strip()
            if _UUID_RE.fullmatch(target_id):
                target_id = target_id.lower()
            normalized = {
                "action": "update",
                "subtaskId": target_id,
                "patch": normalized_patch,
            }
        else:
            target_id = operation["subtaskId"].strip()
            if _UUID_RE.fullmatch(target_id):
                target_id = target_id.lower()
            normalized = {"action": "delete", "subtaskId": target_id}
        if order is not None:
            normalized["order"] = order
        normalized_operations.append(normalized)
    return normalized_operations


def _valid_subtask_batch_preview(
    response: Any,
    *,
    task_id: str,
    operation_id: str,
    base_revision: int,
    operations: list[dict],
) -> bool:
    if not (
        isinstance(response, dict)
        and response.get("ok") is True
        and response.get("result") == "preview"
        and response.get("contractVersion") == _SUBTASK_BATCH_CONTRACT
        and response.get("operationId") == operation_id
        and response.get("taskId") == task_id
        and response.get("baseRevision") == base_revision
        and isinstance(response.get("requestHash"), str)
        and bool(_SHA256_HEX_RE.fullmatch(response["requestHash"]))
        and isinstance(response.get("previewDigest"), str)
        and bool(_SHA256_HEX_RE.fullmatch(response["previewDigest"]))
        and _is_iso_timestamp(response.get("previewExpiresAt"))
        and isinstance(response.get("readBack"), dict)
    ):
        return False
    normalized = response.get("normalizedPayload")
    return (
        isinstance(normalized, dict)
        and set(normalized) == {
            "contractVersion", "source", "action", "taskId", "baseRevision", "workspaceId",
            "operations",
        }
        and normalized.get("contractVersion") == _SUBTASK_BATCH_CONTRACT
        and normalized.get("source") == _CANONICAL_TASK_SOURCE
        and normalized.get("action") == "subtask_batch"
        and normalized.get("taskId") == task_id
        and normalized.get("baseRevision") == base_revision
        and normalized.get("operations") == operations
        and (normalized.get("workspaceId") is None or isinstance(normalized.get("workspaceId"), str))
        and canonical_json_sha256(normalized) == response["requestHash"]
        and response["readBack"].get("id") == task_id
        and response["readBack"].get("workspaceId") == normalized.get("workspaceId")
        and response["readBack"].get("canonicalRevision") == base_revision
        and isinstance(response["readBack"].get("subtasks"), list)
        and _subtask_read_back_matches_operations(
            response["readBack"], task_id=task_id, operations=operations
        )
    )


def _subtask_read_back_matches_operations(
    read_back: Any,
    *,
    task_id: str,
    operations: list[dict],
) -> bool:
    if not (
        isinstance(read_back, dict)
        and read_back.get("id") == task_id
        and isinstance(read_back.get("subtasks"), list)
    ):
        return False
    by_id = {
        subtask.get("id"): (index, subtask)
        for index, subtask in enumerate(read_back["subtasks"])
        if isinstance(subtask, dict) and isinstance(subtask.get("id"), str)
    }
    effects: Dict[str, Optional[dict]] = {}
    for operation in operations:
        action = operation["action"]
        target_id = (
            operation["subtask"]["id"] if action == "create" else operation.get("subtaskId")
        )
        if action == "delete":
            effects[target_id] = None
        elif action == "create":
            effects[target_id] = dict(operation["subtask"])
        else:
            effects[target_id] = {**(effects.get(target_id) or {}), **operation["patch"]}
    for target_id, expected in effects.items():
        target_entry = by_id.get(target_id)
        if expected is None:
            if target_entry is not None:
                return False
            continue
        if target_entry is None:
            return False
        _, target = target_entry
        if any(target.get(key) != value for key, value in expected.items()):
            return False
    last_operation = operations[-1]
    if "order" in last_operation:
        target_id = (
            last_operation["subtask"]["id"]
            if last_operation["action"] == "create"
            else last_operation["subtaskId"]
        )
        expected_index = min(last_operation["order"], max(0, len(read_back["subtasks"]) - 1))
        if target_id not in by_id or by_id[target_id][0] != expected_index:
            return False
    return True


def _normalize_ordered_subtask_ids(value: Any) -> Optional[list[str]]:
    if not isinstance(value, list) or len(value) > 10000:
        return None
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or item != item.strip():
            return None
        normalized.append(item)
    return normalized if len(set(normalized)) == len(normalized) else None


def _valid_subtask_batch_commit(
    response: Any,
    *,
    task_id: str,
    operation_id: str,
    base_revision: int,
    operations: list[dict],
    approved_subtask_ids: list[str],
    request_hash: str,
) -> bool:
    if not (
        isinstance(response, dict)
        and response.get("ok") is True
        and response.get("status") == "committed"
        and response.get("result") == "committed"
        and response.get("requestHash") == request_hash
        and isinstance(response.get("receipt"), dict)
        and response["receipt"].get("status") == "committed"
        and response["receipt"].get("requestHash") == request_hash
        and isinstance(response["receipt"].get("replayed"), bool)
        and response["receipt"].get("canonicalRevision") == base_revision + 1
        and isinstance(response["receipt"].get("readBack"), dict)
        and isinstance(response["receipt"]["readBack"].get("subtasks"), list)
        and [
            subtask.get("id")
            for subtask in response["receipt"]["readBack"]["subtasks"]
            if isinstance(subtask, dict)
        ] == approved_subtask_ids
    ):
        return False
    try:
        validate_nested_canonical_receipt(
            response["receipt"],
            expected={
                "contractVersion": _SUBTASK_BATCH_CONTRACT,
                "operationId": operation_id,
                "source": _CANONICAL_TASK_SOURCE,
                "entityType": "task",
                "action": "subtask_batch",
                "entityId": task_id,
            },
            valid_read_back=lambda read_back: _subtask_read_back_matches_operations(
                read_back,
                task_id=task_id,
                operations=operations,
            ),
        )
    except CanonicalReceiptError:
        return False
    return True


def _handle_subtask_batch(args: dict, **kw) -> str:
    allowed = {
        "taskId", "operationId", "baseRevision", "operations", "preview",
        "previewDigest", "previewExpiresAt", "requestHash", "approvedSubtaskIds",
    }
    unknown = set(args) - allowed
    if unknown:
        return _tool_error(f"unsupported subtask batch fields: {', '.join(sorted(unknown))}")
    task_id = args.get("taskId")
    if not _valid_lifecycle_task_id(task_id):
        return _tool_error("taskId is required and must be a stable UUID")
    task_id = task_id.lower()
    operation_id = args.get("operationId")
    if not _valid_operation_id(operation_id):
        return _tool_error("operationId is required and must be at most 160 trimmed characters")
    base_revision = args.get("baseRevision")
    if not isinstance(base_revision, int) or isinstance(base_revision, bool) or base_revision < 1:
        return _tool_error("baseRevision is required and must be a positive integer")
    operations = _normalize_subtask_batch_operations(args.get("operations"))
    if operations is None:
        return _tool_error("operations do not match the canonical subtask batch contract")
    preview = args.get("preview", True)
    if not isinstance(preview, bool):
        return _tool_error("preview must be a boolean")
    body: Dict[str, Any] = {
        "operationId": operation_id,
        "baseRevision": base_revision,
        "operations": operations,
        "preview": preview,
    }
    if not preview:
        digest = args.get("previewDigest")
        expiry = args.get("previewExpiresAt")
        request_hash = args.get("requestHash")
        approved_subtask_ids = _normalize_ordered_subtask_ids(args.get("approvedSubtaskIds"))
        if not isinstance(digest, str) or not _SHA256_HEX_RE.fullmatch(digest):
            return _tool_error("previewDigest is required when preview is false")
        if not _is_iso_timestamp(expiry):
            return _tool_error("previewExpiresAt is required when preview is false")
        if not isinstance(request_hash, str) or not _SHA256_HEX_RE.fullmatch(request_hash):
            return _tool_error("requestHash is required when preview is false")
        if approved_subtask_ids is None:
            return _tool_error("approvedSubtaskIds is required when preview is false")
        body["previewDigest"] = digest
        body["previewExpiresAt"] = expiry
        body["approvedSubtaskIds"] = approved_subtask_ids
    try:
        response = _request("POST", f"{_subtask_path(task_id)}/batch", body)
        valid = (
            _valid_subtask_batch_preview(
                response,
                task_id=task_id,
                operation_id=operation_id,
                base_revision=base_revision,
                operations=operations,
            )
            if preview
            else _valid_subtask_batch_commit(
                response,
                task_id=task_id,
                operation_id=operation_id,
                base_revision=base_revision,
                operations=operations,
                approved_subtask_ids=approved_subtask_ids,
                request_hash=args["requestHash"],
            )
        )
        if not valid:
            return _tool_error(
                f"Canonical subtask batch {'preview' if preview else 'receipt'} could not be verified"
            )
        return _tool_result(response)
    except _FlowStateApiError as exc:
        logger.error("flowstate_subtask_batch API error: status=%s code=%s", exc.status, exc.code)
        return _typed_tool_error(exc)
    except Exception as exc:
        logger.error("flowstate_subtask_batch error: %s", type(exc).__name__)
        return _tool_error("Flow State canonical subtask batch failed")


FLOWSTATE_ASSISTANT_CONTEXT_SCHEMA = {
    "name": "flowstate_get_assistant_context",
    "description": (
        "Read FlowState's local personal-assistant context summary. This is a "
        "bearer-protected, user-scoped, read-only endpoint for task pressure, "
        "focus patterns, project signals, and assistant memory aggregates."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

FLOWSTATE_HEALTH_SCHEMA = {
    "name": "flowstate_health",
    "description": (
        "Check whether the local Flow State API sidecar is reachable. Use this "
        "when a user asks to work with FlowState and connector availability is uncertain."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

FLOWSTATE_AUDIT_COVERAGE_SCHEMA = {
    "name": "flowstate_audit_coverage",
    "description": (
        "MANDATORY before presenting any review, audit, or coverage summary about "
        "FlowState tasks (including screenshot-based reviews). Sends the exact "
        "reviewed/unreviewed item IDs and your draft summary to FlowState, which "
        "re-reads the claimed records server-side and returns a durable "
        "audit-coverage-v2 receipt plus the strongest wording the evidence "
        "justifies. If the draft over-claims, the result has accepted=false and a "
        "safeSummary you MUST use verbatim (or strictly weaker). Never state "
        "'reviewed everything', 'all tasks', or 'fully verified' without an "
        "accepted receipt whose claimLevel is verified."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "auditScope": {
                "type": "string",
                "description": "What universe was audited, e.g. 'open tasks in personal scope'.",
            },
            "sourceSurface": {
                "type": "string",
                "description": "Where the audited data came from, e.g. 'local-api /api/tasks/inventory' or 'screenshot + /api/tasks/search'.",
            },
            "summaryDraft": {
                "type": "string",
                "description": "The exact summary wording you intend to present to the user.",
            },
            "auditMode": {"type": "string", "enum": ["item", "capability"]},
            "representativeSample": {
                "type": "boolean",
                "description": "True when only a sample of a wider scope was reviewed.",
            },
            "expectedItemIds": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The full expected ID universe when known (e.g. from a complete inventory receipt).",
            },
            "expectedItemCount": {"type": "integer"},
            "reviewedItems": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "itemId": {"type": "string"},
                        "evidenceClass": {
                            "type": "string",
                            "enum": [
                                "exact-record-read",
                                "canonical-receipt",
                                "screenshot-row-reconciled",
                                "title-only-match",
                                "capability-class",
                            ],
                        },
                    },
                    "required": ["itemId", "evidenceClass"],
                },
                "description": "Exactly which items you reviewed and what kind of evidence you have for each.",
            },
            "unreviewedItemIds": {"type": "array", "items": {"type": "string"}},
            "screenshotRows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "visibleText": {"type": "string"},
                        "claimedTaskId": {"type": "string"},
                        "reviewed": {"type": "boolean"},
                    },
                    "required": ["visibleText"],
                },
                "description": "Visible screenshot rows (Hebrew/multiline safe); FlowState reconciles them to exact records.",
            },
            "blockers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Active connector/auth blockers that must survive into the final wording.",
            },
            "notCovered": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["auditScope", "sourceSurface", "summaryDraft"],
    },
}

FLOWSTATE_LIST_TASKS_SCHEMA = {
    "name": "flowstate_list_tasks",
    "description": (
        "List Flow State tasks. With no due filter and open/todo status, this returns the complete "
        "live open-task inventory with explicit completeness, freshness, total, and receipt metadata. "
        "Flow State is the user's personal task app; "
        "it is not a project name. Use returned task ids for later updates. "
        "Do not answer FlowState list/check requests with Markdown, JSON, or "
        "a hermes-ui/task-triage artifact instead of this tool."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "description": "Optional: todo, open, or done."},
            "due": {"type": "string", "description": "Optional: today, overdue, open, or YYYY-MM-DD."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25, "description": "Optional page size, 1-25."},
        },
        "required": [],
    },
}


FLOWSTATE_SEARCH_TASKS_SCHEMA = {
    "name": "flowstate_search_tasks",
    "description": (
        "Search the signed-in user's Flow State tasks by title through the read-only Local Task API. "
        "If no exact title text is known, omit query to browse open tasks safely instead of guessing. "
        "Title search is not a wildcard, list-all, counting, or pagination substitute. "
        "Use this before proposing a mutation when the exact task id is not already known. The response "
        "preserves FlowState's exact task identifiers so similar titles are never treated as duplicates."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional exact title search text; omit to browse open tasks.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 25,
                "description": "Optional result cap, 1-25.",
            },
        },
        "required": [],
    },
}


FLOWSTATE_GET_TASK_SCHEMA = {
    "name": "flowstate_get_task",
    "description": (
        "Read one exact Flow State task by its stable id through the signed-in Local Task API. "
        "Returns supported metadata, recurrence identity, subtasks, work blocks, and Canvas placement "
        "without authentication material. Use this read-back before and after exact mutations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "Exact Flow State task id."},
        },
        "required": ["taskId"],
    },
}


FLOWSTATE_CREATE_TASK_SCHEMA = {
    "name": "flowstate_create_task",
    "description": (
        "Preview creation of one exact personal Flow State task using a caller-generated stable taskId "
        "and operationId. Defaults to preview; apply only after approval of the exact preview digest "
        "and expiry. Omit projectId unless the user explicitly provided a known Flow State project id. "
        "When the user asks to create, save, add, or schedule a task in FlowState, call this tool; do not "
        "substitute Markdown, JSON, or a hermes-ui/task-triage artifact. Verified apply returns a canonical "
        "receipt and exact read-back; it never falls back to the legacy create endpoint."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "Stable UUID generated once and reused for preview/apply/retry."},
            "operationId": {"type": "string", "description": "Stable idempotency key for preview/apply/retry."},
            "title": {"type": "string", "description": "Task title."},
            "description": {"type": "string", "description": "Optional task description."},
            "status": {"type": "string", "enum": ["planned", "in_progress", "backlog", "on_hold"]},
            "priority": {"type": ["string", "null"], "enum": ["low", "medium", "high", None]},
            "dueDate": {"type": "string", "description": "Optional YYYY-MM-DD due date."},
            "projectId": {"type": "string", "description": "Optional known Flow State project id."},
            "preview": {"type": "boolean", "description": "Defaults true; false applies the approved preview."},
            "previewDigest": {"type": "string", "description": "Server-issued digest required for apply."},
            "previewExpiresAt": {"type": "string", "description": "Server-issued expiry required for apply."},
            "requestHash": {"type": "string", "description": "Server-issued request hash required for apply receipt verification."},
        },
        "required": ["taskId", "operationId", "title"],
        "additionalProperties": False,
    },
}

FLOWSTATE_UPDATE_TASK_SCHEMA = {
    "name": "flowstate_update_task",
    "description": (
        "Preview an exact Flow State task patch before mutation. Apply only after the user "
        "approves that preview by resending the exact operation, base revision, patch, "
        "preview digest, expiry, and request hash with preview=false. This generic patch is not a substitute "
        "for task completion; recurring tasks use Done for now through flowstate_done_for_now."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Exact Flow State task id."},
            "operationId": {"type": "string", "description": "Stable idempotency key for preview and apply."},
            "baseRevision": {"type": "integer", "minimum": 1, "description": "Canonical revision returned by Flow State task reads."},
            "patch": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {"type": ["string", "null"]},
                    "dueDate": {"type": ["string", "null"]},
                    "progress": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "additionalProperties": False,
                "minProperties": 1,
            },
            "preview": {"type": "boolean", "description": "Defaults true. False applies an approved preview."},
            "previewDigest": {"type": "string", "description": "Server-issued digest required for apply."},
            "previewExpiresAt": {"type": "string", "description": "Server-issued expiry required for apply."},
            "requestHash": {"type": "string", "description": "Server-issued request hash required for apply."},
        },
        "required": ["id", "operationId", "baseRevision", "patch"],
        "additionalProperties": False,
    },
}

FLOWSTATE_DELETE_TASK_SCHEMA = {
    "name": "flowstate_delete_task",
    "description": (
        "Preview a canonical soft-delete for one exact Flow State task and canonical revision. Defaults "
        "to preview; apply only with the exact operationId, digest, and expiry returned by that preview. "
        "A typed conflict halts the mutation and is never converted into a fallback delete or patch."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "Exact stable Flow State task UUID."},
            "operationId": {"type": "string", "description": "Stable idempotency key."},
            "baseRevision": {"type": "integer", "minimum": 1},
            "preview": {"type": "boolean", "description": "Defaults true; false applies the approved preview."},
            "previewDigest": {"type": "string", "description": "Server-issued digest required for apply."},
            "previewExpiresAt": {"type": "string", "description": "Server-issued expiry required for apply."},
            "requestHash": {"type": "string", "description": "Server-issued request hash required for apply receipt verification."},
        },
        "required": ["taskId", "operationId", "baseRevision"],
        "additionalProperties": False,
    },
}

FLOWSTATE_RESTORE_TASK_SCHEMA = {
    "name": "flowstate_restore_task",
    "description": (
        "Preview restoration of one exact soft-deleted Flow State task and canonical revision. Defaults "
        "to preview and applies only the exact approved operation/digest/expiry. Missing tombstone evidence, "
        "scope errors, or stale revisions halt with a typed conflict and no fallback write."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "Exact stable Flow State task UUID."},
            "operationId": {"type": "string", "description": "Stable idempotency key."},
            "baseRevision": {"type": "integer", "minimum": 1},
            "preview": {"type": "boolean", "description": "Defaults true; false applies the approved preview."},
            "previewDigest": {"type": "string", "description": "Server-issued digest required for apply."},
            "previewExpiresAt": {"type": "string", "description": "Server-issued expiry required for apply."},
            "requestHash": {"type": "string", "description": "Server-issued request hash required for apply receipt verification."},
        },
        "required": ["taskId", "operationId", "baseRevision"],
        "additionalProperties": False,
    },
}

FLOWSTATE_SET_TASK_STATUS_SCHEMA = {
    "name": "flowstate_set_task_status",
    "description": (
        "Preview one exact canonical status transition for a non-recurring Flow State task. Defaults to "
        "preview and applies only the exact approved operation/digest/expiry. Recurring completion must use "
        "flowstate_done_for_now; if Flow State returns that typed requirement, stop and use the domain command "
        "after approval, with no fallback status patch or second write."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "Exact stable Flow State task UUID."},
            "operationId": {"type": "string", "description": "Stable idempotency key."},
            "baseRevision": {"type": "integer", "minimum": 1},
            "status": {"type": "string", "enum": ["planned", "in_progress", "done", "backlog", "on_hold"]},
            "preview": {"type": "boolean", "description": "Defaults true; false applies the approved preview."},
            "previewDigest": {"type": "string", "description": "Server-issued digest required for apply."},
            "previewExpiresAt": {"type": "string", "description": "Server-issued expiry required for apply."},
            "requestHash": {"type": "string", "description": "Server-issued request hash required for apply receipt verification."},
        },
        "required": ["taskId", "operationId", "baseRevision", "status"],
        "additionalProperties": False,
    },
}

FLOWSTATE_CURRENT_TIMER_SCHEMA = {
    "name": "flowstate_get_current_timer",
    "description": "Get the current Flow State timer session, if one is active.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}


FLOWSTATE_TIMER_DIAGNOSTICS_SCHEMA = {
    "name": "flowstate_get_timer_diagnostics",
    "description": (
        "Read-only Flow State timer synchronization diagnostics. Use this to verify whether the "
        "running UI snapshot or signed-in database is authoritative, whether a device leader is "
        "active, and whether local and remote active-timer state agree. This never starts, pauses, "
        "resumes, replaces, or stops a timer."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


FLOWSTATE_LIST_TASK_INSTANCES_SCHEMA = {
    "name": "flowstate_list_task_instances",
    "description": (
        "Read calendar/time-block instances for one exact FlowState task id. "
        "This is read-only and never returns the full task body."
    ),
    "parameters": {
        "type": "object",
        "properties": {"id": {"type": "string", "description": "Exact Flow State task id."}},
        "required": ["id"],
    },
}

FLOWSTATE_SCHEDULE_TASK_INSTANCE_SCHEMA = {
    "name": "flowstate_schedule_task_instance",
    "description": (
        "Preview or apply a FlowState task time block using POST /api/tasks/:id/instances. "
        "Defaults to preview=true and is non-mutating unless preview is explicitly false after "
        "the user approves the exact task id, date, time, and duration. This tool never changes "
        "task status, title, priority, or due date, and never deletes tasks."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Exact Flow State task id."},
            "scheduledDate": {"type": "string", "description": "YYYY-MM-DD scheduled date."},
            "scheduledTime": {"type": "string", "description": "HH:mm 24-hour scheduled time."},
            "duration": {"type": "integer", "description": "Duration in minutes, 1-1440."},
            "preview": {
                "type": "boolean",
                "description": "Omit or set true for a non-mutating preview; set false only after explicit approval.",
            },
        },
        "required": ["id", "scheduledDate", "scheduledTime", "duration"],
    },
}


_WORK_BLOCK_COMMON_PROPERTIES = {
    "taskId": {"type": "string", "description": "Exact stable parent task UUID."},
    "workBlockId": {"type": "string", "description": "Stable work-block UUID."},
    "operationId": {
        "type": "string",
        "description": "Stable idempotency key reused for preview, apply, and retry.",
    },
    "baseRevision": {
        "type": "integer", "minimum": 1,
        "description": "Canonical parent task revision from the latest read.",
    },
    "workBlockRevision": {
        "type": "integer", "minimum": 0,
        "description": "Use 0 for create; otherwise the current canonical work-block revision.",
    },
    "preview": {
        "type": "boolean",
        "description": "Defaults true; false applies only the exact approved preview.",
    },
    "previewDigest": {
        "type": "string", "description": "Exact digest returned by preview; required for apply.",
    },
    "previewExpiresAt": {
        "type": "string", "description": "Exact expiry returned by preview; required for apply.",
    },
    "requestHash": {
        "type": "string", "description": "Exact request hash returned by preview; required for apply proof.",
    },
}
_WORK_BLOCK_INTERVAL_PROPERTIES = {
    "scheduledDate": {"type": "string", "description": "Exact local date as YYYY-MM-DD."},
    "scheduledTime": {"type": "string", "description": "Exact local time as HH:mm."},
    "timezone": {"type": "string", "description": "Recognized IANA timezone for the local interval."},
}
_WORK_BLOCK_FINISH_BY_PROPERTY = {
    "finishBy": {
        "type": "string",
        "description": "Optional latest local end minute as YYYY-MM-DDTHH:mm in the block timezone.",
    },
}
_WORK_BLOCK_REQUIRED = [
    "taskId", "workBlockId", "operationId", "baseRevision", "workBlockRevision",
]

FLOWSTATE_CREATE_WORK_BLOCK_SCHEMA = {
    "name": "flowstate_create_work_block",
    "description": (
        "Preview creation of one stable FlowState work block with exact local interval, timezone, "
        "duration, overlap warnings, due-date effect, and optional finish-by evidence. Apply only the "
        "approved digest/expiry and accept success only from a verified canonical read-back."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            **_WORK_BLOCK_COMMON_PROPERTIES,
            **_WORK_BLOCK_INTERVAL_PROPERTIES,
            "duration": {"type": "integer", "minimum": 1, "maximum": 1440},
            **_WORK_BLOCK_FINISH_BY_PROPERTY,
        },
        "required": _WORK_BLOCK_REQUIRED + ["scheduledDate", "scheduledTime", "duration", "timezone"],
        "additionalProperties": False,
    },
}

FLOWSTATE_MOVE_WORK_BLOCK_SCHEMA = {
    "name": "flowstate_move_work_block",
    "description": (
        "Preview moving one exact work block under both parent-task and work-block revisions. "
        "The preview must prove before/after local time, timezone, overlaps, task effect, and finish-by."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            **_WORK_BLOCK_COMMON_PROPERTIES,
            **_WORK_BLOCK_INTERVAL_PROPERTIES,
            **_WORK_BLOCK_FINISH_BY_PROPERTY,
        },
        "required": _WORK_BLOCK_REQUIRED + ["scheduledDate", "scheduledTime", "timezone"],
        "additionalProperties": False,
    },
}

FLOWSTATE_RESIZE_WORK_BLOCK_SCHEMA = {
    "name": "flowstate_resize_work_block",
    "description": (
        "Preview resizing one exact work block under both parent-task and work-block revisions. "
        "Apply only the exact approved duration, finish-by evidence, digest, and expiry."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            **_WORK_BLOCK_COMMON_PROPERTIES,
            "duration": {"type": "integer", "minimum": 1, "maximum": 1440},
            **_WORK_BLOCK_FINISH_BY_PROPERTY,
        },
        "required": _WORK_BLOCK_REQUIRED + ["duration"],
        "additionalProperties": False,
    },
}

FLOWSTATE_REMOVE_WORK_BLOCK_SCHEMA = {
    "name": "flowstate_remove_work_block",
    "description": (
        "Preview removal of one exact work block without deleting or completing its parent task. "
        "Requires both canonical revisions and verifies the removed ID and authoritative task read-back."
    ),
    "parameters": {
        "type": "object",
        "properties": {**_WORK_BLOCK_COMMON_PROPERTIES},
        "required": _WORK_BLOCK_REQUIRED,
        "additionalProperties": False,
    },
}


FLOWSTATE_DONE_FOR_NOW_SCHEMA = {
    "name": "flowstate_done_for_now",
    "description": (
        "Preview or apply FlowState's real Done for now operation for one exact recurring task. "
        "Defaults to preview and never treats a generic status, progress, or due-date update as "
        "recurring completion. Apply only after explicit user approval of the preview; preview=false "
        "requires the previewVersion and requestHash returned by FlowState. Every call requires a stable requestId. The response includes "
        "FlowState's typed receipt and read-back verification without authentication material."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "Exact recurring Flow State task id."},
            "nextDueDate": {
                "type": "string",
                "description": "Optional YYYY-MM-DD override, only when FlowState's recurrence rules allow it.",
            },
            "preview": {
                "type": "boolean",
                "description": "Defaults true; set false only after the user approves the exact preview.",
            },
            "requestId": {
                "type": "string",
                "description": "Stable idempotency key required for preview and apply.",
            },
            "previewVersion": {
                "type": "string",
                "description": "State-bound version returned by preview. Required when preview is false.",
            },
            "requestHash": {
                "type": "string",
                "description": "Exact request hash returned by preview. Required when preview is false.",
            },
        },
        "required": ["taskId", "requestId"],
    },
}


FLOWSTATE_MERGE_TASKS_SCHEMA = {
    "name": "flowstate_merge_tasks",
    "description": (
        "Preview or apply FlowState's safe merge for two exact task ids. Defaults to preview and "
        "returns FlowState's retained fields, transfers, conflicts, archival behavior, receipt, and "
        "read-back unchanged. Apply only after explicit approval of that preview and provide its "
        "requestId, previewVersion, and requestHash. Title similarity is never approval, and this tool does not "
        "implement or guess merge semantics outside FlowState. If FlowState returns a recurrence "
        "conflict without a supplied resolution, stop all further Flow State mutations and ask for "
        "the exact intended cadence; never fall back to separate task updates or deletion."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "survivorTaskId": {"type": "string", "description": "Exact task id that will survive."},
            "duplicateTaskId": {"type": "string", "description": "Exact task id to merge and archive."},
            "recurrenceResolution": {
                "type": "object",
                "description": (
                    "Optional exact canonical cadence selected by the user for the surviving root task. "
                    "Use only after exact-reading both tasks and confirming there is no established series history."
                ),
                "properties": {
                    "pattern": {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly"]},
                    "interval": {"type": "integer", "minimum": 1, "maximum": 365},
                    "weekdays": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0, "maximum": 6},
                        "minItems": 1,
                        "uniqueItems": True,
                    },
                    "monthDay": {"type": "integer", "minimum": 1, "maximum": 31},
                    "monthWeekday": {
                        "type": "object",
                        "properties": {
                            "nth": {"type": "integer", "enum": [-1, 1, 2, 3, 4, 5]},
                            "day": {"type": "integer", "minimum": 0, "maximum": 6},
                        },
                        "required": ["nth", "day"],
                        "additionalProperties": False,
                    },
                    "endType": {"type": "string", "enum": ["never", "after_count", "on_date"]},
                    "endDate": {"type": "string", "description": "YYYY-MM-DD; required only for on_date."},
                    "endCount": {"type": "integer", "minimum": 1},
                },
                "required": ["pattern", "interval", "endType"],
                "additionalProperties": False,
            },
            "preview": {
                "type": "boolean",
                "description": "Defaults true; set false only after explicit approval of this exact merge.",
            },
            "requestId": {
                "type": "string",
                "description": "Stable idempotency key required for preview and apply.",
            },
            "previewVersion": {
                "type": "string",
                "description": "State-bound version returned by preview. Required when preview is false.",
            },
            "requestHash": {
                "type": "string",
                "description": "Exact request hash returned by preview. Required when preview is false.",
            },
        },
        "required": ["survivorTaskId", "duplicateTaskId", "requestId"],
    },
}


_SUBTASK_MUTATION_PROPERTIES = {
    "preview": {
        "type": "boolean",
        "description": "Defaults true; set false only after the user approves the exact change.",
    },
    "requestId": {
        "type": "string",
        "description": "Stable idempotency key. Required when preview is false and echoed in the apply receipt.",
    },
}

FLOWSTATE_LIST_SUBTASKS_SCHEMA = {
    "name": "flowstate_list_subtasks",
    "description": "List the ordered subtasks stored by FlowState for one exact parent task id.",
    "parameters": {
        "type": "object",
        "properties": {"taskId": {"type": "string", "description": "Exact parent task id."}},
        "required": ["taskId"],
    },
}

FLOWSTATE_CREATE_SUBTASK_SCHEMA = {
    "name": "flowstate_create_subtask",
    "description": (
        "Preview or create an ordered FlowState subtask. Defaults to non-mutating preview. "
        "Apply responses include a stable receipt from FlowState."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "Exact parent task id."},
            "title": {"type": "string", "description": "Subtask title."},
            "order": {"type": "integer", "description": "Optional zero-based order."},
            **_SUBTASK_MUTATION_PROPERTIES,
        },
        "required": ["taskId", "title"],
    },
}

FLOWSTATE_UPDATE_SUBTASK_SCHEMA = {
    "name": "flowstate_update_subtask",
    "description": (
        "Preview or update a FlowState subtask's title, completion, or order. "
        "Defaults to non-mutating preview."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "Exact parent task id."},
            "subtaskId": {"type": "string", "description": "Exact subtask id."},
            "title": {"type": "string", "description": "Optional new title."},
            "completed": {"type": "boolean", "description": "Optional completion state."},
            "order": {"type": "integer", "description": "Optional zero-based order."},
            **_SUBTASK_MUTATION_PROPERTIES,
        },
        "required": ["taskId", "subtaskId"],
    },
}

FLOWSTATE_DELETE_SUBTASK_SCHEMA = {
    "name": "flowstate_delete_subtask",
    "description": "Preview or delete one exact FlowState subtask. Defaults to non-mutating preview.",
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "Exact parent task id."},
            "subtaskId": {"type": "string", "description": "Exact subtask id."},
            **_SUBTASK_MUTATION_PROPERTIES,
        },
        "required": ["taskId", "subtaskId"],
    },
}

FLOWSTATE_SUBTASK_BATCH_SCHEMA = {
    "name": "flowstate_subtask_batch",
    "description": (
        "Preview an ordered task breakdown or atomically apply the exact approved preview. "
        "Uses one durable operationId, the parent's canonical baseRevision, stable client UUIDs "
        "for creates, and one verified receipt for the complete outcome. Never fall back to "
        "individual subtask writes when this command fails. A doneEnough criterion records an "
        "intentional stopping point; it does not imply that the parent task is complete."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "Exact parent task id."},
            "operationId": {
                "type": "string",
                "description": "Stable idempotency key reused for preview, apply, and retry.",
            },
            "baseRevision": {
                "type": "integer",
                "minimum": 1,
                "description": "Canonical parent revision returned by the latest FlowState read.",
            },
            "operations": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["create", "update", "delete"]},
                        "subtaskId": {"type": "string", "description": "Target id for update/delete."},
                        "subtask": {
                            "type": "object",
                            "description": "Create payload. id must be a stable client-generated UUID.",
                            "properties": {
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "isCompleted": {"type": "boolean"},
                                "completedPomodoros": {
                                    "type": "integer", "minimum": 0, "maximum": 100000,
                                },
                                "doneEnough": {
                                    "type": "string",
                                    "minLength": 1,
                                    "description": "Concrete evidence that this step is intentionally done enough.",
                                },
                                "estimateMinutes": {
                                    "anyOf": [
                                        {"type": "integer", "minimum": 1, "maximum": 10080},
                                        {"type": "null"},
                                    ],
                                },
                            },
                            "required": ["id", "title", "doneEnough"],
                            "additionalProperties": False,
                        },
                        "patch": {
                            "type": "object",
                            "description": "Fields to change for an update operation.",
                            "properties": {
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "isCompleted": {"type": "boolean"},
                                "completedPomodoros": {
                                    "type": "integer", "minimum": 0, "maximum": 100000,
                                },
                                "doneEnough": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                                "estimateMinutes": {
                                    "anyOf": [
                                        {"type": "integer", "minimum": 1, "maximum": 10080},
                                        {"type": "null"},
                                    ],
                                },
                            },
                            "additionalProperties": False,
                        },
                        "order": {"type": "integer", "minimum": 0, "maximum": 100000},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
            "preview": {
                "type": "boolean",
                "description": "Defaults true; set false only after approval of this exact preview.",
            },
            "previewDigest": {
                "type": "string",
                "description": "Exact digest returned by preview; required for apply.",
            },
            "previewExpiresAt": {
                "type": "string",
                "description": "Exact expiry returned by preview; required for apply.",
            },
            "requestHash": {
                "type": "string",
                "description": "Exact request hash returned by preview; required to verify apply.",
            },
            "approvedSubtaskIds": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
                "description": (
                    "Exact ordered subtask IDs from preview.readBack.subtasks; required for apply."
                ),
            },
        },
        "required": ["taskId", "operationId", "baseRevision", "operations"],
        "additionalProperties": False,
    },
}


from tools.registry import registry

for _name, _schema, _handler in [
    ("flowstate_get_assistant_context", FLOWSTATE_ASSISTANT_CONTEXT_SCHEMA, _handle_assistant_context),
    ("flowstate_health", FLOWSTATE_HEALTH_SCHEMA, _handle_health),
    ("flowstate_audit_coverage", FLOWSTATE_AUDIT_COVERAGE_SCHEMA, _handle_audit_coverage),
    ("flowstate_list_tasks", FLOWSTATE_LIST_TASKS_SCHEMA, _handle_list_tasks),
    ("flowstate_search_tasks", FLOWSTATE_SEARCH_TASKS_SCHEMA, _handle_search_tasks),
    ("flowstate_get_task", FLOWSTATE_GET_TASK_SCHEMA, _handle_get_task),
    ("flowstate_create_task", FLOWSTATE_CREATE_TASK_SCHEMA, _handle_create_task),
    ("flowstate_update_task", FLOWSTATE_UPDATE_TASK_SCHEMA, _handle_update_task),
    ("flowstate_delete_task", FLOWSTATE_DELETE_TASK_SCHEMA, _handle_delete_task),
    ("flowstate_restore_task", FLOWSTATE_RESTORE_TASK_SCHEMA, _handle_restore_task),
    ("flowstate_set_task_status", FLOWSTATE_SET_TASK_STATUS_SCHEMA, _handle_set_task_status),
    ("flowstate_get_current_timer", FLOWSTATE_CURRENT_TIMER_SCHEMA, _handle_current_timer),
    ("flowstate_get_timer_diagnostics", FLOWSTATE_TIMER_DIAGNOSTICS_SCHEMA, _handle_timer_diagnostics),
    ("flowstate_list_task_instances", FLOWSTATE_LIST_TASK_INSTANCES_SCHEMA, _handle_list_task_instances),
    ("flowstate_create_work_block", FLOWSTATE_CREATE_WORK_BLOCK_SCHEMA, _handle_create_work_block),
    ("flowstate_move_work_block", FLOWSTATE_MOVE_WORK_BLOCK_SCHEMA, _handle_move_work_block),
    ("flowstate_resize_work_block", FLOWSTATE_RESIZE_WORK_BLOCK_SCHEMA, _handle_resize_work_block),
    ("flowstate_remove_work_block", FLOWSTATE_REMOVE_WORK_BLOCK_SCHEMA, _handle_remove_work_block),
    ("flowstate_done_for_now", FLOWSTATE_DONE_FOR_NOW_SCHEMA, _handle_done_for_now),
    ("flowstate_merge_tasks", FLOWSTATE_MERGE_TASKS_SCHEMA, _handle_merge_tasks),
    ("flowstate_list_subtasks", FLOWSTATE_LIST_SUBTASKS_SCHEMA, _handle_list_subtasks),
    ("flowstate_subtask_batch", FLOWSTATE_SUBTASK_BATCH_SCHEMA, _handle_subtask_batch),
]:
    registry.register(
        name=_name,
        toolset="flowstate",
        schema=_schema,
        handler=_handler,
        check_fn=(
            _check_flowstate_available
            if _name == "flowstate_health"
            # Tools without a route-requirement entry (e.g. audit_coverage,
            # which degrades gracefully server-side) fall back to the plain
            # availability check instead of being capability-gated.
            else _FLOWSTATE_TOOL_CHECKS.get(_name, _check_flowstate_available)
        ),
        emoji="📋",
    )
