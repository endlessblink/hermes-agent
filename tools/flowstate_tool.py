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
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tools.flowstate_receipts import (
    CanonicalReceiptError,
    canonical_json_hash,
    validate_canonical_receipt,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://127.0.0.1:5577"
_FLOW_STATE_API_URL: str = ""
_FLOW_STATE_API_TOKEN: str = ""
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_ONLY_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_VALID_STATUS_FILTERS = {"todo", "open", "done"}
_VALID_DUE_FILTERS = {"today", "overdue", "open"}
_VALID_PRIORITIES = {"low", "medium", "high"}
_CANONICAL_TASK_CONTRACT = "task-v1"
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
_CANONICAL_COMPLETE_FIELDS = {
    "taskId",
    "operationId",
    "baseRevision",
    "preview",
    "previewDigest",
    "previewExpiresAt",
    "requestHash",
}
_CANONICAL_LIFECYCLE_APPROVAL_FIELDS = {
    "operationId",
    "baseRevision",
    "preview",
    "previewDigest",
    "previewExpiresAt",
    "requestHash",
}
_CANONICAL_CREATE_FIELDS = _CANONICAL_LIFECYCLE_APPROVAL_FIELDS | {
    "taskId",
    "title",
    "description",
    "priority",
    "dueDate",
    "projectId",
}
_CANONICAL_EXISTING_LIFECYCLE_FIELDS = _CANONICAL_LIFECYCLE_APPROVAL_FIELDS | {"id"}
_WORK_BLOCK_COMMAND_FIELDS = {
    "operationId",
    "timeZone",
    "finishBy",
    "operations",
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
            and payload.get("fresh") is True
            and payload.get("complete") is True
            and isinstance(items, list)
            and all(isinstance(item, dict) for item in items)
            and all(isinstance(task_id, str) and _UUID_RE.fullmatch(task_id) for task_id in ids)
            and all(
                isinstance(item.get("canonicalRevision"), int)
                and not isinstance(item.get("canonicalRevision"), bool)
                and item["canonicalRevision"] > 0
                for item in items
            )
            and len(ids) == len(set(ids))
            and isinstance(page, dict)
            and isinstance(payload.get("changeSequence"), int)
            and not isinstance(payload.get("changeSequence"), bool)
            and payload["changeSequence"] >= 0
            and isinstance(payload.get("total"), int)
            and not isinstance(payload.get("total"), bool)
            and payload["total"] == len(ids)
            and page.get("hasMore") is False
            and page.get("nextCursor") is None
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise _FlowStateApiError(
            "Flow State returned an invalid inventory receipt; no exact count is available.",
            code="invalid_inventory_receipt",
            status=502,
        )
    return payload


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
        return _tool_result(_request("GET", "/api/health"))
    except Exception as exc:
        logger.error("flowstate_health error: %s", exc)
        return _tool_error(str(exc))


def _handle_assistant_context(args: dict, **kw) -> str:
    try:
        return _tool_result(_request("GET", "/api/assistant/context"))
    except Exception as exc:
        logger.error("flowstate_get_assistant_context error: %s", exc)
        return _tool_error(str(exc))


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


def _canonical_lifecycle_state(action: str, read_back: Any, task_id: str) -> bool:
    if (
        not isinstance(read_back, dict)
        or read_back.get("id") != task_id
        or not isinstance(read_back.get("tombstonePresent"), bool)
    ):
        return False
    if action == "delete":
        return (
            read_back.get("isDeleted") is True
            and read_back.get("tombstonePresent") is True
            and _is_iso_timestamp(read_back.get("deletedAt"))
        )
    if not (
        read_back.get("isDeleted") is False
        and read_back.get("tombstonePresent") is False
        and read_back.get("deletedAt") is None
    ):
        return False
    return action != "reopen" or (
        read_back.get("status") == "todo" and read_back.get("completedAt") is None
    )


def _valid_lifecycle_preview(
    payload: Any,
    *,
    action: str,
    task_id: Optional[str],
    operation_id: str,
    revision: int,
    expected_create_payload: Optional[dict] = None,
) -> bool:
    if (
        not isinstance(payload, dict)
        or payload.get("ok") is not True
        or payload.get("result") != "preview"
        or payload.get("contractVersion") != _CANONICAL_TASK_CONTRACT
        or payload.get("operationId") != operation_id
        or payload.get("action") != action
        or payload.get("baseRevision") != revision
        or not isinstance(payload.get("previewDigest"), str)
        or not _SHA256_HEX_RE.fullmatch(payload["previewDigest"])
        or not isinstance(payload.get("requestHash"), str)
        or not _SHA256_HEX_RE.fullmatch(payload["requestHash"])
        or not _is_iso_timestamp(payload.get("previewExpiresAt"))
        or not isinstance(payload.get("normalizedPayload"), dict)
    ):
        return False
    issued_task_id = payload.get("taskId")
    if not isinstance(issued_task_id, str) or not issued_task_id:
        return False
    if action == "create":
        if task_id is not None and task_id != issued_task_id:
            return False
        if payload["normalizedPayload"].get("taskId") != issued_task_id:
            return False
        if expected_create_payload is None or any(
            payload["normalizedPayload"].get(key) != value
            for key, value in expected_create_payload.items()
        ):
            return False
    elif issued_task_id != task_id:
        return False
    return (
        _valid_canonical_read_back(payload.get("readBack"), issued_task_id, revision)
        and _canonical_lifecycle_state(action, payload["readBack"], issued_task_id)
    )


def _validate_lifecycle_operation(args: dict, *, action: str) -> tuple[Optional[dict], Optional[str]]:
    allowed = _CANONICAL_CREATE_FIELDS if action == "create" else _CANONICAL_EXISTING_LIFECYCLE_FIELDS
    unknown = sorted(set(args) - allowed)
    if unknown:
        return None, f"unsupported canonical lifecycle fields: {', '.join(unknown)}"
    operation_id = args.get("operationId")
    if (
        not isinstance(operation_id, str)
        or not operation_id.strip()
        or operation_id != operation_id.strip()
        or len(operation_id) > 160
    ):
        return None, "operationId is required and must be at most 160 trimmed characters"
    revision = args.get("baseRevision")
    if action == "create":
        if revision != 0 or isinstance(revision, bool):
            return None, "baseRevision is required and must be 0 for task creation"
    elif not _is_positive_int(revision):
        return None, "baseRevision is required and must be a positive integer"
    preview = args.get("preview", True)
    if not isinstance(preview, bool):
        return None, "preview must be a boolean"
    task_key = "taskId" if action == "create" else "id"
    task_id = args.get(task_key)
    if action != "create" or not preview:
        if not isinstance(task_id, str) or not task_id.strip():
            return None, f"{task_key} is required"
        task_id = task_id.strip()
    else:
        task_id = None
    body: Dict[str, Any] = {
        "operationId": operation_id,
        "baseRevision": revision,
        "preview": preview,
    }
    if not preview:
        digest = args.get("previewDigest")
        expiry = args.get("previewExpiresAt")
        request_hash = args.get("requestHash")
        if not isinstance(digest, str) or not _SHA256_HEX_RE.fullmatch(digest):
            return None, "previewDigest is required when preview is false"
        if not _is_iso_timestamp(expiry):
            return None, "previewExpiresAt is required when preview is false"
        if not isinstance(request_hash, str) or not _SHA256_HEX_RE.fullmatch(request_hash):
            return None, "requestHash is required when preview is false"
        body.update({
            "previewDigest": digest,
            "previewExpiresAt": expiry,
            "requestHash": request_hash,
        })
    return {"body": body, "taskId": task_id, "preview": preview}, None


def _handle_task_lifecycle(action: str, args: dict) -> str:
    validated, error = _validate_lifecycle_operation(args, action=action)
    if error:
        return _tool_error(error)
    assert validated is not None
    body = validated["body"]
    task_id = validated["taskId"]
    preview = validated["preview"]
    if action == "create":
        title = str(args.get("title") or "").strip()
        if not title:
            return _tool_error("title is required")
        priority = args.get("priority")
        if priority in ("", None):
            priority = None
        if priority is not None and priority not in _VALID_PRIORITIES:
            return _tool_error("priority must be low|medium|high or null")
        due_date = args.get("dueDate")
        if due_date in ("", None):
            due_date = None
        elif not _DATE_ONLY_RE.fullmatch(str(due_date)):
            return _tool_error("dueDate must be YYYY-MM-DD")
        project_id = args.get("projectId")
        if project_id in ("", None):
            project_id = None
        body["payload"] = {
            "title": title,
            "description": str(args.get("description") or ""),
            "priority": priority,
            "dueDate": due_date,
            "projectId": project_id,
        }
        if not preview:
            body["taskId"] = task_id
        path = "/api/tasks"
    else:
        path = f"/api/tasks/{urllib.parse.quote(task_id, safe='')}/{action}"

    try:
        payload = _request("POST", path, body)
        if preview:
            if not _valid_lifecycle_preview(
                payload,
                action=action,
                task_id=task_id,
                operation_id=body["operationId"],
                revision=body["baseRevision"],
                expected_create_payload=body.get("payload") if action == "create" else None,
            ):
                return _tool_error("Canonical task lifecycle preview could not be verified")
        else:
            expected_action = "update" if action == "reopen" else action
            if (
                not isinstance(payload, dict)
                or payload.get("operationId") != body["operationId"]
                or payload.get("action") != action
                or payload.get("taskId") != task_id
            ):
                return _tool_error("Canonical task lifecycle receipt could not be verified")
            try:
                validate_canonical_receipt(
                    payload,
                    expected_operation_id=body["operationId"],
                    expected_request_hash=body["requestHash"],
                    expected_action=action,
                    expected_entity_id=task_id,
                    expected_affected_actions={task_id: expected_action},
                    read_back_validator=lambda read_back, receipt: (
                        read_back.get("canonicalRevision") == receipt.get("canonicalRevision")
                        and read_back.get("canonicalUpdatedAt") == receipt.get("canonicalUpdatedAt")
                        and _canonical_lifecycle_state(action, read_back, task_id)
                    ),
                )
            except CanonicalReceiptError:
                return _tool_error("Canonical task lifecycle receipt could not be verified")
        return _tool_result(payload)
    except _FlowStateApiError as exc:
        logger.error("flowstate_%s_task typed error: code=%s status=%s", action, exc.code, exc.status)
        if action == "reopen" and exc.code == "recurring_task":
            exc = _FlowStateApiError(
                str(exc),
                code=exc.code,
                status=exc.status,
                action="stop_mutations_and_report_recurrence_history",
            )
        return _typed_tool_error(exc)
    except RuntimeError as exc:
        logger.error("flowstate_%s_task runtime error: %s", action, type(exc).__name__)
        message = str(exc)
        if message.startswith("Flow State Local Task API"):
            return _tool_error(message)
        return _tool_error("Flow State canonical task lifecycle failed")
    except Exception as exc:
        logger.error("flowstate_%s_task error: %s", action, type(exc).__name__)
        return _tool_error("Flow State canonical task lifecycle failed")


def _handle_create_task(args: dict, **kw) -> str:
    return _handle_task_lifecycle("create", args)


def _handle_delete_task(args: dict, **kw) -> str:
    return _handle_task_lifecycle("delete", args)


def _handle_restore_task(args: dict, **kw) -> str:
    return _handle_task_lifecycle("restore", args)


def _handle_reopen_task(args: dict, **kw) -> str:
    return _handle_task_lifecycle("reopen", args)


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


def _same_iso_instant(left: Any, right: Any) -> bool:
    if not _is_iso_timestamp(left) or not _is_iso_timestamp(right):
        return False
    left_value = datetime.fromisoformat(left.replace("Z", "+00:00"))
    right_value = datetime.fromisoformat(right.replace("Z", "+00:00"))
    return left_value == right_value


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
        else:
            try:
                validate_canonical_receipt(
                    payload,
                    expected_operation_id=operation_id,
                    expected_request_hash=body["requestHash"],
                    expected_action="patch",
                    expected_entity_id=task_id,
                    expected_affected_actions={task_id: "update"},
                    read_back_validator=lambda read_back, receipt: (
                        read_back.get("id") == task_id
                        and read_back.get("canonicalRevision")
                        == receipt.get("canonicalRevision")
                    ),
                )
            except CanonicalReceiptError:
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


def _handle_complete_task(args: dict, **kw) -> str:
    unknown = sorted(set(args) - _CANONICAL_COMPLETE_FIELDS)
    if unknown:
        return _tool_error(
            f"unsupported canonical completion fields: {', '.join(unknown)}"
        )

    task_id = args.get("taskId")
    if not isinstance(task_id, str) or not task_id.strip():
        return _tool_error("taskId is required")
    task_id = task_id.strip()

    operation_id = args.get("operationId")
    if (
        not isinstance(operation_id, str)
        or not operation_id.strip()
        or operation_id != operation_id.strip()
        or len(operation_id) > 160
    ):
        return _tool_error(
            "operationId is required and must be at most 160 trimmed characters"
        )

    base_revision = args.get("baseRevision")
    if not _is_positive_int(base_revision):
        return _tool_error("baseRevision is required and must be a positive integer")

    preview = args.get("preview", True)
    if not isinstance(preview, bool):
        return _tool_error("preview must be a boolean")

    body: Dict[str, Any] = {
        "operationId": operation_id,
        "baseRevision": base_revision,
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
        body.update({
            "previewDigest": digest,
            "previewExpiresAt": expiry,
            "requestHash": request_hash,
        })

    try:
        path = f"/api/tasks/{urllib.parse.quote(task_id, safe='')}/complete"
        payload = _request("POST", path, body)
        if preview:
            valid_preview = (
                _valid_canonical_preview(
                    payload, task_id, operation_id, base_revision
                )
                and payload.get("willSetCompletedAt") is True
                and payload.get("normalizedPayload") == {"status": "done"}
            )
            if not valid_preview:
                return _tool_error(
                    "Canonical task completion preview could not be verified"
                )
        else:
            try:
                validate_canonical_receipt(
                    payload,
                    expected_operation_id=operation_id,
                    expected_request_hash=body["requestHash"],
                    expected_action="complete",
                    expected_entity_id=task_id,
                    expected_affected_actions={task_id: "update"},
                    read_back_validator=lambda read_back, receipt: (
                        read_back.get("id") == task_id
                        and read_back.get("canonicalRevision")
                        == receipt.get("canonicalRevision")
                        and read_back.get("canonicalUpdatedAt")
                        == receipt.get("canonicalUpdatedAt")
                        and read_back.get("status") == "done"
                        and _is_iso_timestamp(read_back.get("completedAt"))
                    ),
                )
            except CanonicalReceiptError:
                return _tool_error(
                    "Canonical task completion receipt could not be verified"
                )
        return _tool_result(payload)
    except _FlowStateApiError as exc:
        logger.error(
            "flowstate_complete_task typed error: code=%s status=%s",
            exc.code,
            exc.status,
        )
        if exc.code == "recurring_task":
            exc = _FlowStateApiError(
                str(exc),
                code=exc.code,
                status=exc.status,
                action="stop_mutations_and_use_done_for_now",
            )
        return _typed_tool_error(exc)
    except RuntimeError as exc:
        logger.error("flowstate_complete_task runtime error: %s", type(exc).__name__)
        message = str(exc)
        if message.startswith("Flow State Local Task API"):
            return _tool_error(message)
        return _tool_error("Flow State canonical task completion failed")
    except Exception as exc:
        logger.error("flowstate_complete_task error: %s", type(exc).__name__)
        return _tool_error("Flow State canonical task completion failed")


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
        payload = _request(
            "GET",
            f"/api/tasks/{urllib.parse.quote(task_id, safe='')}/instances",
            allow_stale_cache=False,
        )
        task = payload.get("task") if isinstance(payload, dict) else None
        instances = payload.get("instances") if isinstance(payload, dict) else None
        valid = (
            isinstance(payload, dict)
            and payload.get("ok") is True
            and payload.get("fresh") is True
            and isinstance(task, dict)
            and task.get("id") == task_id
            and _is_positive_int(task.get("canonicalRevision"))
            and isinstance(instances, list)
        )
        if valid:
            for instance in instances:
                if not isinstance(instance, dict):
                    valid = False
                    break
                block_hash = instance.get("baseWorkBlockHash")
                canonical = {key: value for key, value in instance.items() if key != "baseWorkBlockHash"}
                try:
                    expected_hash = canonical_json_hash(canonical)
                except (TypeError, ValueError):
                    valid = False
                    break
                if not isinstance(block_hash, str) or block_hash != expected_hash:
                    valid = False
                    break
        if not valid:
            return _tool_error("Canonical work-block inventory could not be verified")
        return _tool_result(payload)
    except _FlowStateApiError as exc:
        logger.error("flowstate_list_task_instances typed error: code=%s status=%s", exc.code, exc.status)
        return _typed_tool_error(exc)
    except Exception as exc:
        logger.error("flowstate_list_task_instances error: %s", type(exc).__name__)
        message = str(exc)
        if message.startswith("Flow State Local Task API"):
            return _tool_error(message)
        return _tool_error("Flow State work-block inventory failed")


def _real_date(value: Any) -> bool:
    if not isinstance(value, str) or not _DATE_ONLY_RE.fullmatch(value):
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False


def _valid_time_zone(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 100:
        return False
    try:
        ZoneInfo(value)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def _normalize_work_block_operations(value: Any) -> tuple[Optional[list[dict]], Optional[str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 50:
        return None, "operations must contain 1 to 50 work-block commands"
    normalized: list[dict] = []
    seen_targets: set[tuple[str, str]] = set()
    task_revisions: dict[str, int] = {}
    common = {"kind", "taskId", "baseRevision"}
    for raw in value:
        if not isinstance(raw, dict):
            return None, "each work-block operation must be an object"
        kind = raw.get("kind")
        task_id = raw.get("taskId")
        revision = raw.get("baseRevision")
        if kind not in {"create", "move", "resize", "remove"}:
            return None, "kind must be create, move, resize, or remove"
        if not isinstance(task_id, str) or not task_id.strip() or task_id != task_id.strip():
            return None, "taskId is required and must be trimmed"
        if not _is_positive_int(revision):
            return None, "baseRevision must be a positive integer"
        if task_id in task_revisions and task_revisions[task_id] != revision:
            return None, "one task cannot carry conflicting baseRevision values"
        task_revisions[task_id] = revision
        if kind == "create":
            allowed = common | {"clientId", "scheduledDate", "scheduledTime", "duration"}
            client_id = raw.get("clientId")
            if not isinstance(client_id, str) or not client_id.strip() or client_id != client_id.strip():
                return None, "clientId is required for create"
            target = (task_id, f"client:{client_id}")
        else:
            allowed = common | {"workBlockId", "baseWorkBlockHash"}
            if kind == "move":
                allowed |= {"scheduledDate", "scheduledTime", "duration"}
            elif kind == "resize":
                allowed.add("duration")
            work_block_id = raw.get("workBlockId")
            if not isinstance(work_block_id, str) or not work_block_id.strip() or work_block_id != work_block_id.strip():
                return None, "workBlockId is required for move, resize, and remove"
            block_hash = raw.get("baseWorkBlockHash")
            if not isinstance(block_hash, str) or not _SHA256_HEX_RE.fullmatch(block_hash):
                return None, "baseWorkBlockHash must be a lowercase SHA-256 digest"
            target = (task_id, f"block:{work_block_id}")
        unknown = sorted(set(raw) - allowed)
        if unknown:
            return None, f"unsupported work-block fields: {', '.join(unknown)}"
        if target in seen_targets:
            return None, "one batch cannot target the same work block twice"
        seen_targets.add(target)
        if kind in {"create", "move"}:
            if not _real_date(raw.get("scheduledDate")):
                return None, "scheduledDate must be a real YYYY-MM-DD date"
            if not isinstance(raw.get("scheduledTime"), str) or not _TIME_ONLY_RE.fullmatch(raw["scheduledTime"]):
                return None, "scheduledTime must be HH:mm"
        if kind in {"create", "resize"} or (kind == "move" and "duration" in raw):
            duration = raw.get("duration")
            if not _is_positive_int(duration) or duration > 1440:
                return None, "duration must be an integer from 1 to 1440"
        normalized.append(dict(raw))
    return normalized, None


def _work_block_operations_match(returned: Any, requested: list[dict]) -> bool:
    if not isinstance(returned, list) or len(returned) != len(requested):
        return False
    for expected, actual in zip(requested, returned):
        if not isinstance(actual, dict):
            return False
        if any(actual.get(key) != value for key, value in expected.items()):
            return False
        if expected["kind"] == "create" and not (
            isinstance(actual.get("workBlockId"), str) and actual["workBlockId"]
        ):
            return False
    return True


def _work_block_task_ids(operations: list[dict]) -> list[str]:
    return list(dict.fromkeys(operation["taskId"] for operation in operations))


def _valid_work_block_read_backs(value: Any, operations: list[dict], *, applied: bool) -> bool:
    task_ids = _work_block_task_ids(operations)
    revisions = {operation["taskId"]: operation["baseRevision"] for operation in operations}
    if not isinstance(value, list) or len(value) != len(task_ids):
        return False
    for task_id in task_ids:
        task = next((item for item in value if isinstance(item, dict) and item.get("id") == task_id), None)
        if not isinstance(task, dict):
            return False
        revision = task.get("canonicalRevision")
        if not _is_positive_int(revision) or (
            revision <= revisions[task_id] if applied else revision != revisions[task_id]
        ):
            return False
        if not isinstance(task.get("instances"), list):
            return False
        if not isinstance(task.get("status"), str) or not task["status"]:
            return False
        if not _is_iso_timestamp(task.get("canonicalUpdatedAt")):
            return False
    return True


def _work_block_outcomes_match(operations: list[dict], read_backs: list[dict]) -> bool:
    for operation in operations:
        task = next((item for item in read_backs if item.get("id") == operation["taskId"]), None)
        if not task:
            return False
        instances = task["instances"]
        if operation["kind"] == "create":
            block = next((item for item in instances if item.get("clientId") == operation["clientId"]), None)
        else:
            block = next((item for item in instances if item.get("id") == operation["workBlockId"]), None)
        if operation["kind"] == "remove":
            if block is not None:
                return False
            continue
        if not isinstance(block, dict):
            return False
        for field in ("scheduledDate", "scheduledTime", "duration"):
            if field in operation and block.get(field) != operation[field]:
                return False
    return True


def _valid_work_block_preview(payload: Any, request: dict) -> bool:
    normalized = payload.get("normalizedPayload") if isinstance(payload, dict) else None
    finish_by = request.get("finishBy")
    return (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("result") == "preview"
        and payload.get("contractVersion") == _CANONICAL_TASK_CONTRACT
        and payload.get("action") == "work_block_batch"
        and payload.get("operationId") == request["operationId"]
        and payload.get("timeZone") == request["timeZone"]
        and (
            payload.get("finishBy") is None
            if finish_by is None
            else _same_iso_instant(payload.get("finishBy"), finish_by)
        )
        and isinstance(payload.get("requestHash"), str)
        and bool(_SHA256_HEX_RE.fullmatch(payload["requestHash"]))
        and isinstance(payload.get("previewDigest"), str)
        and bool(_SHA256_HEX_RE.fullmatch(payload["previewDigest"]))
        and _is_iso_timestamp(payload.get("previewExpiresAt"))
        and isinstance(normalized, dict)
        and normalized.get("timeZone") == request["timeZone"]
        and (
            normalized.get("finishBy") is None
            if finish_by is None
            else _same_iso_instant(normalized.get("finishBy"), finish_by)
        )
        and _work_block_operations_match(normalized.get("operations"), request["operations"])
        and isinstance(payload.get("overlapWarnings"), list)
        and all(isinstance(item, dict) for item in payload["overlapWarnings"])
        and _valid_work_block_read_backs(payload.get("readBack"), request["operations"], applied=False)
        and _work_block_outcomes_match(request["operations"], payload["readBack"])
    )


def _valid_work_block_receipt(payload: Any, request: dict) -> bool:
    if not (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("result") == "committed"
        and payload.get("action") == "work_block_batch"
        and payload.get("operationId") == request["operationId"]
        and payload.get("requestHash") == request["requestHash"]
        and isinstance(payload.get("receipt"), dict)
    ):
        return False
    receipt = payload["receipt"]
    read_back = receipt.get("readBack")
    task_ids = _work_block_task_ids(request["operations"])
    try:
        valid_envelope = (
            receipt.get("ok") is True
            and receipt.get("status") in {"committed", "replayed"}
            and receipt.get("replayed") is (receipt.get("status") == "replayed")
            and receipt.get("contractVersion") == _CANONICAL_TASK_CONTRACT
            and receipt.get("operationId") == request["operationId"]
            and receipt.get("requestHash") == request["requestHash"]
            and receipt.get("source") == "local-api"
            and receipt.get("entityType") == "batch"
            and receipt.get("entityId") == request["operationId"]
            and receipt.get("action") == "work_block_batch"
            and _is_positive_int(receipt.get("canonicalRevision"))
            and _is_positive_int(receipt.get("changeSequence"))
            and _is_iso_timestamp(receipt.get("committedAt"))
            and _valid_work_block_read_backs(read_back, request["operations"], applied=True)
            and receipt.get("readBackHash") == canonical_json_hash(read_back)
            and _work_block_outcomes_match(request["operations"], read_back)
            and isinstance(receipt.get("affected"), list)
            and len(receipt["affected"]) == len(task_ids)
        )
    except (TypeError, ValueError):
        return False
    if not valid_envelope:
        return False
    for task_id in task_ids:
        task = next(item for item in read_back if item["id"] == task_id)
        affected = next((item for item in receipt["affected"] if isinstance(item, dict) and item.get("entityId") == task_id), None)
        try:
            valid_affected = (
                isinstance(affected, dict)
                and affected.get("entityType") == "task"
                and affected.get("action") == "update"
                and affected.get("canonicalRevision") == task["canonicalRevision"]
                and _is_positive_int(affected.get("changeSequence"))
                and affected.get("readBack") == task
                and affected.get("readBackHash") == canonical_json_hash(task)
            )
        except (TypeError, ValueError):
            return False
        if not valid_affected:
            return False
    return True


def _handle_work_block_command(args: dict, **kw) -> str:
    unknown = sorted(set(args) - _WORK_BLOCK_COMMAND_FIELDS)
    if unknown:
        return _tool_error(f"unsupported work-block command fields: {', '.join(unknown)}")
    operation_id = args.get("operationId")
    if not isinstance(operation_id, str) or not operation_id.strip() or operation_id != operation_id.strip() or len(operation_id) > 160:
        return _tool_error("operationId is required and must be at most 160 trimmed characters")
    if not _valid_time_zone(args.get("timeZone")):
        return _tool_error("timeZone must be a valid IANA timezone")
    finish_by = args.get("finishBy")
    if finish_by is not None and not _is_iso_timestamp(finish_by):
        return _tool_error("finishBy must be an ISO timestamp with an offset")
    operations, operation_error = _normalize_work_block_operations(args.get("operations"))
    if operation_error:
        return _tool_error(operation_error)
    preview = args.get("preview", True)
    if not isinstance(preview, bool):
        return _tool_error("preview must be a boolean")
    body = {
        "operationId": operation_id,
        "timeZone": args["timeZone"],
        **({"finishBy": finish_by} if finish_by is not None else {}),
        "operations": operations,
        "preview": preview,
    }
    if not preview:
        for field in ("previewDigest", "previewExpiresAt", "requestHash"):
            value = args.get(field)
            valid = _is_iso_timestamp(value) if field == "previewExpiresAt" else (
                isinstance(value, str) and bool(_SHA256_HEX_RE.fullmatch(value))
            )
            if not valid:
                return _tool_error(f"{field} is required when preview is false")
            body[field] = value
    try:
        payload = _request("POST", "/api/work-blocks/batch", body, allow_stale_cache=False)
        request = dict(body)
        if preview:
            if not _valid_work_block_preview(payload, request):
                return _tool_error("Canonical work-block preview could not be verified")
        elif not _valid_work_block_receipt(payload, request):
            return _tool_error("Canonical work-block receipt could not be verified")
        return _tool_result(payload)
    except _FlowStateApiError as exc:
        logger.error("flowstate_work_block_command typed error: code=%s status=%s", exc.code, exc.status)
        return _typed_tool_error(exc)
    except RuntimeError as exc:
        logger.error("flowstate_work_block_command runtime error: %s", type(exc).__name__)
        message = str(exc)
        if message.startswith("Flow State Local Task API"):
            return _tool_error(message)
        return _tool_error("Flow State canonical work-block command failed")
    except Exception as exc:
        logger.error("flowstate_work_block_command error: %s", type(exc).__name__)
        return _tool_error("Flow State canonical work-block command failed")


def _handle_schedule_task_instance(args: dict, **kw) -> str:
    task_id = args.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        return _tool_error("id is required")
    command = {
        key: value for key, value in args.items()
        if key not in {"id", "baseRevision", "clientId", "scheduledDate", "scheduledTime", "duration"}
    }
    command["operations"] = [{
        "kind": "create",
        "taskId": task_id.strip(),
        "baseRevision": args.get("baseRevision"),
        "clientId": args.get("clientId"),
        "scheduledDate": args.get("scheduledDate"),
        "scheduledTime": args.get("scheduledTime"),
        "duration": args.get("duration"),
    }]
    return _handle_work_block_command(command)


def _valid_action_preview(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("preview") is True
        and isinstance(payload.get("requestId"), str)
        and bool(payload["requestId"].strip())
        and isinstance(payload.get("previewVersion"), str)
        and bool(payload["previewVersion"].strip())
        and isinstance(payload.get("requestHash"), str)
        and bool(_SHA256_HEX_RE.fullmatch(payload["requestHash"]))
    )


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
    raw_request_id = args.get("requestId")
    if raw_request_id is not None and (
        not isinstance(raw_request_id, str)
        or raw_request_id != raw_request_id.strip()
    ):
        return _tool_error("requestId must not contain surrounding whitespace")
    request_id = raw_request_id or ""
    preview_version = str(args.get("previewVersion") or "").strip()
    raw_request_hash = args.get("requestHash")
    if raw_request_hash is not None and (
        not isinstance(raw_request_hash, str)
        or raw_request_hash != raw_request_hash.strip()
    ):
        return _tool_error("requestHash must not contain surrounding whitespace")
    request_hash = raw_request_hash or ""
    if request_hash and not _SHA256_HEX_RE.fullmatch(request_hash):
        return _tool_error("requestHash must be a 64-character lowercase SHA-256 digest")
    if not preview and not request_id:
        return _tool_error("requestId is required when preview is false")
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
        payload = _request("POST", path, body)
        if preview:
            if not _valid_action_preview(payload):
                return _tool_error("Canonical Done for now preview could not be verified")
        else:
            try:
                receipt = payload.get("receipt") if isinstance(payload, dict) else None
                read_back = receipt.get("readBack") if isinstance(receipt, dict) else None
                completed = (
                    read_back.get("completedOccurrence")
                    if isinstance(read_back, dict)
                    else None
                )
                completed_id = (
                    completed.get("id") if isinstance(completed, dict) else None
                )
                if (
                    not isinstance(completed_id, str)
                    or not completed_id
                    or completed_id == task_id
                ):
                    raise CanonicalReceiptError(
                        "canonical completion identity does not match"
                    )
                validate_canonical_receipt(
                    payload,
                    expected_operation_id=request_id,
                    expected_request_hash=request_hash,
                    expected_action="done_for_now",
                    expected_entity_id=task_id,
                    expected_affected_actions={
                        task_id: "update",
                        completed_id: "create",
                    },
                    read_back_validator=lambda read_back, receipt: (
                        read_back.get("id") == task_id
                        and read_back.get("canonicalRevision")
                        == receipt.get("canonicalRevision")
                        and isinstance(receipt.get("affected"), list)
                        and len(receipt["affected"]) == 2
                        and isinstance(receipt["affected"][1], dict)
                        and isinstance(read_back.get("completedOccurrence"), dict)
                        and read_back["completedOccurrence"].get("id") == completed_id
                        and read_back["completedOccurrence"].get("canonicalRevision")
                        == receipt["affected"][1].get("canonicalRevision")
                        and read_back["completedOccurrence"].get("changeSequence")
                        == receipt["affected"][1].get("changeSequence")
                        and isinstance(read_back.get("nextOccurrence"), dict)
                        and read_back["nextOccurrence"].get("taskId") == task_id
                    ),
                )
            except CanonicalReceiptError:
                return _tool_error("Canonical Done for now receipt could not be verified")
        return _tool_result(payload)
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
    raw_request_id = args.get("requestId")
    if raw_request_id is not None and (
        not isinstance(raw_request_id, str)
        or raw_request_id != raw_request_id.strip()
    ):
        return _tool_error("requestId must not contain surrounding whitespace")
    request_id = raw_request_id or ""
    preview_version = str(args.get("previewVersion") or "").strip()
    raw_request_hash = args.get("requestHash")
    if raw_request_hash is not None and (
        not isinstance(raw_request_hash, str)
        or raw_request_hash != raw_request_hash.strip()
    ):
        return _tool_error("requestHash must not contain surrounding whitespace")
    request_hash = raw_request_hash or ""
    if request_hash and not _SHA256_HEX_RE.fullmatch(request_hash):
        return _tool_error("requestHash must be a 64-character lowercase SHA-256 digest")
    if not preview and not request_id:
        return _tool_error("requestId is required when preview is false")
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
        payload = _request("POST", path, body)
        if preview:
            if not _valid_action_preview(payload):
                return _tool_error("Canonical merge preview could not be verified")
        else:
            try:
                validate_canonical_receipt(
                    payload,
                    expected_operation_id=request_id,
                    expected_request_hash=request_hash,
                    expected_action="merge",
                    expected_entity_id=survivor_task_id,
                    expected_affected_actions={
                        survivor_task_id: "update",
                        duplicate_task_id: "archive",
                    },
                    read_back_validator=lambda read_back, receipt: (
                        read_back.get("id") == survivor_task_id
                        and read_back.get("canonicalRevision")
                        == receipt.get("canonicalRevision")
                        and read_back.get("survivorTaskId") == survivor_task_id
                        and read_back.get("duplicateTaskId") == duplicate_task_id
                        and read_back.get("duplicateArchived") is True
                    ),
                )
            except CanonicalReceiptError:
                return _tool_error("Canonical merge receipt could not be verified")
        return _tool_result(payload)
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


def _normalize_subtask_operation(operation: Any) -> tuple[Optional[dict], Optional[str]]:
    if not isinstance(operation, dict):
        return None, "each subtask operation must be an object"
    kind = operation.get("kind")
    if kind not in {"create", "update", "delete"}:
        return None, "each operation kind must be create|update|delete"
    allowed = (
        {
            "kind", "clientId", "title", "description", "doneEnough", "estimateMinutes",
            "completedPomodoros", "canvasPosition", "isCompleted", "order",
        }
        if kind == "create"
        else {
            "kind", "subtaskId", "title", "description", "doneEnough", "estimateMinutes",
            "completedPomodoros", "canvasPosition", "isCompleted", "order",
        }
    )
    unknown = sorted(set(operation) - allowed)
    if unknown:
        return None, f"unsupported subtask operation fields: {', '.join(unknown)}"
    normalized: Dict[str, Any] = {"kind": kind}
    if kind == "create":
        client_id = operation.get("clientId")
        title = operation.get("title")
        if not isinstance(client_id, str) or not client_id.strip() or client_id != client_id.strip():
            return None, "create operations require a trimmed clientId"
        if not isinstance(title, str) or not title.strip() or title != title.strip():
            return None, "create operations require a trimmed title"
        normalized.update({"clientId": client_id, "title": title})
    else:
        subtask_id = operation.get("subtaskId")
        if not isinstance(subtask_id, str) or not subtask_id.strip() or subtask_id != subtask_id.strip():
            return None, f"{kind} operations require a trimmed subtaskId"
        normalized["subtaskId"] = subtask_id
    if kind == "delete":
        return normalized, None
    for key in ("title", "description", "doneEnough"):
        if key not in operation:
            continue
        value = operation[key]
        if key == "doneEnough" and value is None:
            normalized[key] = None
            continue
        if not isinstance(value, str) or (key == "title" and not value.strip()):
            return None, f"{key} must be text{'' if key != 'title' else ' and non-empty'}"
        if key == "title" and value != value.strip():
            return None, "title must not contain surrounding whitespace"
        normalized[key] = value
    if "estimateMinutes" in operation:
        estimate = operation["estimateMinutes"]
        if estimate is not None and (not _is_positive_int(estimate) or estimate > 1440):
            return None, "estimateMinutes must be an integer from 1 to 1440"
        normalized["estimateMinutes"] = estimate
    if "completedPomodoros" in operation:
        completed = operation["completedPomodoros"]
        if not isinstance(completed, int) or isinstance(completed, bool) or completed < 0:
            return None, "completedPomodoros must be a non-negative integer"
        normalized["completedPomodoros"] = completed
    if "canvasPosition" in operation:
        position = operation["canvasPosition"]
        if position is not None and (
            not isinstance(position, dict)
            or set(position) != {"x", "y"}
            or any(not isinstance(position[axis], (int, float)) or isinstance(position[axis], bool) for axis in ("x", "y"))
        ):
            return None, "canvasPosition must be null or an object with numeric x and y"
        normalized["canvasPosition"] = position
    if "isCompleted" in operation:
        if not isinstance(operation["isCompleted"], bool):
            return None, "isCompleted must be a boolean"
        normalized["isCompleted"] = operation["isCompleted"]
    if "order" in operation:
        order = operation["order"]
        if not isinstance(order, int) or isinstance(order, bool) or order < 0:
            return None, "order must be a non-negative integer"
        normalized["order"] = order
    if kind == "update" and set(normalized) == {"kind", "subtaskId"}:
        return None, "update operations require at least one changed field"
    return normalized, None


def _canonical_subtask_body(args: dict, operations: list[dict]) -> tuple[Optional[dict], Optional[str]]:
    operation_id = args.get("operationId")
    if (
        not isinstance(operation_id, str)
        or not operation_id.strip()
        or operation_id != operation_id.strip()
        or len(operation_id) > 160
    ):
        return None, "operationId is required and must be at most 160 trimmed characters"
    revision = args.get("baseRevision")
    if not _is_positive_int(revision):
        return None, "baseRevision is required and must be a positive integer"
    preview = args.get("preview", True)
    if not isinstance(preview, bool):
        return None, "preview must be a boolean"
    body: Dict[str, Any] = {
        "operationId": operation_id,
        "baseRevision": revision,
        "preview": preview,
        "operations": operations,
    }
    if not preview:
        digest = args.get("previewDigest")
        expiry = args.get("previewExpiresAt")
        request_hash = args.get("requestHash")
        if not isinstance(digest, str) or not _SHA256_HEX_RE.fullmatch(digest):
            return None, "previewDigest is required when preview is false"
        if not _is_iso_timestamp(expiry):
            return None, "previewExpiresAt is required when preview is false"
        if not isinstance(request_hash, str) or not _SHA256_HEX_RE.fullmatch(request_hash):
            return None, "requestHash is required when preview is false"
        body.update({
            "previewDigest": digest,
            "previewExpiresAt": expiry,
            "requestHash": request_hash,
        })
    return body, None


def _valid_subtask_preview(payload: Any, task_id: str, body: dict) -> bool:
    normalized = payload.get("normalizedPayload") if isinstance(payload, dict) else None
    returned_operations = normalized.get("operations") if isinstance(normalized, dict) else None
    operations_match = (
        isinstance(returned_operations, list)
        and len(returned_operations) == len(body["operations"])
        and all(
            isinstance(returned, dict)
            and returned.get("kind") == requested.get("kind")
            and all(returned.get(key) == value for key, value in requested.items())
            for requested, returned in zip(body["operations"], returned_operations)
        )
    )
    return (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("result") == "preview"
        and payload.get("contractVersion") == _CANONICAL_TASK_CONTRACT
        and payload.get("action") == "subtask_batch"
        and payload.get("operationId") == body["operationId"]
        and payload.get("taskId") == task_id
        and payload.get("baseRevision") == body["baseRevision"]
        and isinstance(payload.get("previewDigest"), str)
        and bool(_SHA256_HEX_RE.fullmatch(payload["previewDigest"]))
        and isinstance(payload.get("requestHash"), str)
        and bool(_SHA256_HEX_RE.fullmatch(payload["requestHash"]))
        and _is_iso_timestamp(payload.get("previewExpiresAt"))
        and isinstance(normalized, dict)
        and normalized.get("taskId") == task_id
        and operations_match
        and isinstance(payload.get("readBack"), dict)
        and payload["readBack"].get("id") == task_id
        and payload["readBack"].get("canonicalRevision") == body["baseRevision"]
        and isinstance(payload["readBack"].get("subtasks"), list)
    )


def _subtask_operations_reflected(operations: list[dict], subtasks: Any) -> bool:
    if not isinstance(subtasks, list) or not all(isinstance(item, dict) for item in subtasks):
        return False
    for operation in operations:
        if operation["kind"] == "delete":
            if any(item.get("id") == operation["subtaskId"] for item in subtasks):
                return False
            continue
        identity_key = "clientId" if operation["kind"] == "create" else "id"
        identity = operation.get("clientId") if operation["kind"] == "create" else operation.get("subtaskId")
        index = next(
            (position for position, item in enumerate(subtasks) if item.get(identity_key) == identity),
            None,
        )
        if index is None:
            return False
        item = subtasks[index]
        for field in (
            "title", "description", "doneEnough", "estimateMinutes",
            "completedPomodoros", "canvasPosition", "isCompleted",
        ):
            if field in operation and item.get(field) != operation[field]:
                return False
        if "order" in operation and index != operation["order"]:
            return False
    return True


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

    operation: Dict[str, Any] = {
        "kind": "create",
        "clientId": args.get("clientId"),
        "title": title,
    }
    for key in (
        "description", "doneEnough", "estimateMinutes", "completedPomodoros",
        "canvasPosition", "isCompleted", "order",
    ):
        if key in args:
            operation[key] = args[key]
    return _handle_subtask_batch({**args, "operations": [operation]})


def _handle_update_subtask(args: dict, **kw) -> str:
    task_id = str(args.get("taskId") or "").strip()
    if not task_id:
        return _tool_error("taskId is required")
    subtask_id = str(args.get("subtaskId") or "").strip()
    if not subtask_id:
        return _tool_error("subtaskId is required")

    operation: Dict[str, Any] = {"kind": "update", "subtaskId": subtask_id}
    field_aliases = {"completed": "isCompleted"}
    for key in (
        "title", "description", "doneEnough", "estimateMinutes", "completedPomodoros",
        "canvasPosition", "isCompleted", "completed", "order",
    ):
        if key in args:
            operation[field_aliases.get(key, key)] = args[key]
    return _handle_subtask_batch({**args, "operations": [operation]})


def _handle_delete_subtask(args: dict, **kw) -> str:
    task_id = str(args.get("taskId") or "").strip()
    if not task_id:
        return _tool_error("taskId is required")
    subtask_id = str(args.get("subtaskId") or "").strip()
    if not subtask_id:
        return _tool_error("subtaskId is required")
    return _handle_subtask_batch({
        **args,
        "operations": [{"kind": "delete", "subtaskId": subtask_id}],
    })


def _handle_subtask_batch(args: dict, **kw) -> str:
    task_id = str(args.get("taskId") or "").strip()
    if not task_id:
        return _tool_error("taskId is required")
    operations = args.get("operations")
    if not isinstance(operations, list) or not operations or len(operations) > 50:
        return _tool_error("operations must contain 1 to 50 items")
    normalized: list[dict] = []
    for operation in operations:
        item, error = _normalize_subtask_operation(operation)
        if error:
            return _tool_error(error)
        assert item is not None
        normalized.append(item)
    body, error = _canonical_subtask_body(args, normalized)
    if error:
        return _tool_error(error)
    assert body is not None
    try:
        payload = _request("POST", f"{_subtask_path(task_id)}/batch", body)
        if body["preview"]:
            if not _valid_subtask_preview(payload, task_id, body):
                return _tool_error("Canonical subtask preview could not be verified")
        else:
            if (
                not isinstance(payload, dict)
                or payload.get("result") != "committed"
                or payload.get("operationId") != body["operationId"]
                or payload.get("requestHash") != body["requestHash"]
            ):
                return _tool_error("Canonical subtask receipt could not be verified")
            try:
                validate_canonical_receipt(
                    payload,
                    expected_operation_id=body["operationId"],
                    expected_request_hash=body["requestHash"],
                    expected_action="subtask_batch",
                    expected_entity_id=task_id,
                    expected_affected_actions={task_id: "update"},
                    read_back_validator=lambda read_back, receipt: (
                        read_back.get("id") == task_id
                        and read_back.get("canonicalRevision")
                        == receipt.get("canonicalRevision")
                        and read_back.get("canonicalUpdatedAt")
                        == receipt.get("canonicalUpdatedAt")
                        and isinstance(read_back.get("subtasks"), list)
                        and _subtask_operations_reflected(
                            body["operations"], read_back.get("subtasks")
                        )
                    ),
                )
            except CanonicalReceiptError:
                return _tool_error("Canonical subtask receipt could not be verified")
        return _tool_result(payload)
    except _FlowStateApiError as exc:
        logger.error(
            "flowstate_subtask_batch API error: status=%s code=%s",
            exc.status,
            exc.code,
        )
        return _typed_tool_error(exc)
    except Exception as exc:
        logger.error("flowstate_subtask_batch error: %s", exc)
        return _tool_error(str(exc))


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

FLOWSTATE_LIST_TASKS_SCHEMA = {
    "name": "flowstate_list_tasks",
    "description": (
        "List Flow State tasks. With no due filter and open/todo status, this returns the complete "
        "live open-task inventory with explicit completeness, freshness, total, and receipt metadata. "
        "Filtered or due-date results are bounded samples with complete=false and cannot prove a total. "
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
        "Exact-title results are bounded samples with complete=false and cannot prove a total. "
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
        "Preview or apply canonical Flow State task creation. Defaults to a zero-write preview "
        "that issues a deterministic taskId; apply only after explicit approval by resending "
        "that exact taskId, operation, digest, expiry, and request hash with preview=false. "
        "Omit projectId unless the user explicitly provided a known Flow State project id. "
        "When the user asks for real FlowState creation, call this tool; do not substitute "
        "Markdown or a hermes-ui/task-triage artifact."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operationId": {"type": "string", "description": "Stable idempotency key for preview and apply."},
            "baseRevision": {"type": "integer", "minimum": 0, "maximum": 0, "description": "Must be 0 for create."},
            "taskId": {"type": "string", "description": "Server-issued preview task id; required unchanged for apply."},
            "title": {"type": "string", "description": "Task title."},
            "description": {"type": "string", "description": "Optional task description."},
            "priority": {"type": ["string", "null"], "enum": ["low", "medium", "high", None], "description": "Optional priority."},
            "dueDate": {"type": "string", "description": "Optional YYYY-MM-DD due date."},
            "projectId": {"type": "string", "description": "Optional known Flow State project id."},
            "preview": {"type": "boolean", "description": "Defaults true; set false only after explicit approval."},
            "previewDigest": {"type": "string", "description": "Server-issued digest required unchanged for apply."},
            "previewExpiresAt": {"type": "string", "description": "Server-issued expiry required for apply."},
            "requestHash": {"type": "string", "description": "Server-issued request hash required unchanged for apply."},
        },
        "required": ["operationId", "baseRevision", "title"],
    },
}

FLOWSTATE_UPDATE_TASK_SCHEMA = {
    "name": "flowstate_update_task",
    "description": (
        "Preview an exact Flow State task patch before mutation. Apply only after the user "
        "approves that preview by resending the exact operation, base revision, patch, "
        "preview digest, and expiry with preview=false. This generic patch is not a substitute "
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
            "requestHash": {
                "type": "string",
                "description": "Server-issued request hash from preview; required unchanged for apply.",
            },
        },
        "required": ["id", "operationId", "baseRevision", "patch"],
        "additionalProperties": False,
    },
}

FLOWSTATE_COMPLETE_TASK_SCHEMA = {
    "name": "flowstate_complete_task",
    "description": (
        "Preview or apply the canonical completion of one exact non-recurring Flow State task. "
        "Defaults to preview and applies only after explicit approval of the exact server-issued "
        "digest, expiry, and request hash. Recurring tasks are rejected; use Done for now through "
        "flowstate_done_for_now instead. A successful apply is returned only after Hermes verifies "
        "the canonical receipt, completed status, and completed timestamp."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "Exact Flow State task id."},
            "operationId": {
                "type": "string",
                "description": "Stable idempotency key for preview and apply.",
            },
            "baseRevision": {
                "type": "integer",
                "minimum": 1,
                "description": "Canonical revision returned by an exact task read.",
            },
            "preview": {
                "type": "boolean",
                "description": "Defaults true; set false only after explicit approval.",
            },
            "previewDigest": {
                "type": "string",
                "description": "Server-issued digest required unchanged for apply.",
            },
            "previewExpiresAt": {
                "type": "string",
                "description": "Server-issued preview expiry required for apply.",
            },
            "requestHash": {
                "type": "string",
                "description": "Server-issued request hash required unchanged for apply.",
            },
        },
        "required": ["taskId", "operationId", "baseRevision"],
        "additionalProperties": False,
    },
}

FLOWSTATE_DELETE_TASK_SCHEMA = {
    "name": "flowstate_delete_task",
    "description": (
        "Preview or apply canonical soft-delete for one exact Flow State task. Defaults to "
        "preview; apply requires explicit approval of the exact revision, digest, expiry, "
        "and request hash. Success is returned only after receipt and tombstone verification."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "description": "Exact Flow State task id."},
            "operationId": {"type": "string", "description": "Stable idempotency key."},
            "baseRevision": {"type": "integer", "minimum": 1},
            "preview": {"type": "boolean", "description": "Defaults true."},
            "previewDigest": {"type": "string"},
            "previewExpiresAt": {"type": "string"},
            "requestHash": {"type": "string"},
        },
        "required": ["id", "operationId", "baseRevision"],
    },
}

FLOWSTATE_RESTORE_TASK_SCHEMA = {
    "name": "flowstate_restore_task",
    "description": (
        "Preview or apply canonical restore for one exact soft-deleted Flow State task. "
        "Defaults to preview and verifies the cleared tombstone after approved apply."
    ),
    "parameters": {
        **FLOWSTATE_DELETE_TASK_SCHEMA["parameters"],
        "properties": dict(FLOWSTATE_DELETE_TASK_SCHEMA["parameters"]["properties"]),
    },
}

FLOWSTATE_REOPEN_TASK_SCHEMA = {
    "name": "flowstate_reopen_task",
    "description": (
        "Preview or apply canonical reopen for one exact completed non-recurring Flow State task. "
        "Defaults to preview; recurring tasks fail closed and must not fall back to a generic patch."
    ),
    "parameters": {
        **FLOWSTATE_DELETE_TASK_SCHEMA["parameters"],
        "properties": dict(FLOWSTATE_DELETE_TASK_SCHEMA["parameters"]["properties"]),
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

FLOWSTATE_WORK_BLOCK_COMMAND_SCHEMA = {
    "name": "flowstate_work_block_command",
    "description": (
        "Preview or apply one atomic canonical FlowState work-block batch. Supports create, move, "
        "resize, and remove across one or more exact task ids. Defaults to preview. Apply only after "
        "the user approves the returned digest, expiry, and request hash. A stale task rejects the "
        "whole batch; due dates remain deadlines and recurring occurrences require their own command."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "operationId": {"type": "string", "description": "Stable identity for this exact preview/apply action."},
            "timeZone": {"type": "string", "description": "IANA timezone for the local calendar interval."},
            "finishBy": {"type": "string", "description": "Optional ISO timestamp boundary with offset."},
            "operations": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["create", "move", "resize", "remove"]},
                        "taskId": {"type": "string"},
                        "baseRevision": {"type": "integer", "minimum": 1},
                        "clientId": {"type": "string", "description": "Required only for create."},
                        "workBlockId": {"type": "string", "description": "Required for move, resize, and remove."},
                        "baseWorkBlockHash": {"type": "string", "description": "Exact SHA-256 identity of the current block."},
                        "scheduledDate": {"type": "string", "description": "YYYY-MM-DD for create or move."},
                        "scheduledTime": {"type": "string", "description": "HH:mm for create or move."},
                        "duration": {"type": "integer", "minimum": 1, "maximum": 1440},
                    },
                    "required": ["kind", "taskId", "baseRevision"],
                },
            },
            "preview": {"type": "boolean", "description": "Defaults true. False requires all approval fields."},
            "previewDigest": {"type": "string"},
            "previewExpiresAt": {"type": "string"},
            "requestHash": {"type": "string"},
        },
        "required": ["operationId", "timeZone", "operations"],
    },
}

FLOWSTATE_SCHEDULE_TASK_INSTANCE_SCHEMA = {
    "name": "flowstate_schedule_task_instance",
    "description": (
        "Compatibility create adapter for the canonical FlowState work-block command. Defaults to "
        "preview and requires stable operation, task revision, client identity, and timezone. Apply "
        "requires the exact server-issued approval proof. Prefer flowstate_work_block_command for "
        "move, resize, remove, or atomic multi-task scheduling."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Exact Flow State task id."},
            "operationId": {"type": "string"},
            "baseRevision": {"type": "integer", "minimum": 1},
            "clientId": {"type": "string"},
            "timeZone": {"type": "string"},
            "scheduledDate": {"type": "string", "description": "YYYY-MM-DD scheduled date."},
            "scheduledTime": {"type": "string", "description": "HH:mm 24-hour scheduled time."},
            "duration": {"type": "integer", "description": "Duration in minutes, 1-1440."},
            "preview": {
                "type": "boolean",
                "description": "Omit or set true for a non-mutating preview; set false only after explicit approval.",
            },
            "previewDigest": {"type": "string"},
            "previewExpiresAt": {"type": "string"},
            "requestHash": {"type": "string"},
        },
        "required": [
            "id", "operationId", "baseRevision", "clientId", "timeZone",
            "scheduledDate", "scheduledTime", "duration",
        ],
    },
}


FLOWSTATE_DONE_FOR_NOW_SCHEMA = {
    "name": "flowstate_done_for_now",
    "description": (
        "Preview or apply FlowState's real Done for now operation for one exact recurring task. "
        "Defaults to preview and never treats a generic status, progress, or due-date update as "
        "recurring completion. Apply only after explicit user approval of the preview; preview=false "
        "requires the stable requestId, previewVersion, and exact server-issued requestHash returned "
        "by FlowState. The response includes "
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
                "description": "Stable idempotency key. Required when preview is false.",
            },
            "previewVersion": {
                "type": "string",
                "description": "State-bound version returned by preview. Required when preview is false.",
            },
            "requestHash": {
                "type": "string",
                "description": "Server-issued request hash returned by preview. Required unchanged for apply.",
            },
        },
        "required": ["taskId"],
    },
}


FLOWSTATE_MERGE_TASKS_SCHEMA = {
    "name": "flowstate_merge_tasks",
    "description": (
        "Preview or apply FlowState's safe merge for two exact task ids. Defaults to preview and "
        "returns FlowState's retained fields, transfers, conflicts, archival behavior, receipt, and "
        "read-back unchanged. Apply only after explicit approval of that preview and provide its "
        "requestId and previewVersion. Title similarity is never approval, and this tool does not "
        "apply without the exact server-issued requestHash from that preview. "
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
                "description": "Stable idempotency key. Required when preview is false.",
            },
            "previewVersion": {
                "type": "string",
                "description": "State-bound version returned by preview. Required when preview is false.",
            },
            "requestHash": {
                "type": "string",
                "description": "Server-issued request hash returned by preview. Required unchanged for apply.",
            },
        },
        "required": ["survivorTaskId", "duplicateTaskId"],
    },
}


_SUBTASK_MUTATION_PROPERTIES = {
    "operationId": {
        "type": "string",
        "description": "Stable operation identity required for preview, apply, and replay.",
    },
    "baseRevision": {
        "type": "integer",
        "minimum": 1,
        "description": "Exact canonical revision of the parent task.",
    },
    "preview": {
        "type": "boolean",
        "description": "Defaults true; set false only after the user approves the exact change.",
    },
    "previewDigest": {
        "type": "string",
        "description": "Server-issued preview digest required unchanged for apply.",
    },
    "previewExpiresAt": {
        "type": "string",
        "description": "Server-issued preview expiry required unchanged for apply.",
    },
    "requestHash": {
        "type": "string",
        "description": "Server-issued request hash required unchanged for apply.",
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
            "clientId": {"type": "string", "description": "Stable client identity for this new step."},
            "title": {"type": "string", "description": "Subtask title."},
            "description": {"type": "string"},
            "doneEnough": {"type": ["string", "null"], "description": "Optional sufficient stopping condition; null clears it."},
            "estimateMinutes": {"type": ["integer", "null"], "minimum": 1, "maximum": 1440},
            "completedPomodoros": {"type": "integer", "minimum": 0},
            "canvasPosition": {
                "anyOf": [
                    {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}}, "required": ["x", "y"], "additionalProperties": False},
                    {"type": "null"},
                ]
            },
            "isCompleted": {"type": "boolean"},
            "order": {"type": "integer", "description": "Optional zero-based order."},
            **_SUBTASK_MUTATION_PROPERTIES,
        },
        "required": ["taskId", "operationId", "baseRevision", "clientId", "title"],
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
            "description": {"type": "string"},
            "doneEnough": {"type": ["string", "null"], "description": "Optional sufficient stopping condition; null clears it."},
            "estimateMinutes": {"type": ["integer", "null"], "minimum": 1, "maximum": 1440},
            "completedPomodoros": {"type": "integer", "minimum": 0},
            "canvasPosition": {
                "anyOf": [
                    {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}}, "required": ["x", "y"], "additionalProperties": False},
                    {"type": "null"},
                ]
            },
            "isCompleted": {"type": "boolean", "description": "Optional completion state."},
            "order": {"type": "integer", "description": "Optional zero-based order."},
            **_SUBTASK_MUTATION_PROPERTIES,
        },
        "required": ["taskId", "operationId", "baseRevision", "subtaskId"],
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
        "required": ["taskId", "operationId", "baseRevision", "subtaskId"],
    },
}

FLOWSTATE_SUBTASK_BATCH_SCHEMA = {
    "name": "flowstate_subtask_batch",
    "description": (
        "Preview or atomically apply 1-50 ordered subtask create, update, and delete operations. "
        "Defaults to preview and returns one receipt for the full approved outcome."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "Exact parent task id."},
            "operations": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["create", "update", "delete"]},
                        "clientId": {"type": "string"},
                        "subtaskId": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "doneEnough": {"type": ["string", "null"]},
                        "estimateMinutes": {"type": ["integer", "null"], "minimum": 1, "maximum": 1440},
                        "completedPomodoros": {"type": "integer", "minimum": 0},
                        "canvasPosition": {
                            "anyOf": [
                                {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}}, "required": ["x", "y"], "additionalProperties": False},
                                {"type": "null"},
                            ]
                        },
                        "isCompleted": {"type": "boolean"},
                        "order": {"type": "integer", "minimum": 0},
                    },
                    "required": ["kind"],
                    "additionalProperties": False,
                },
            },
            **_SUBTASK_MUTATION_PROPERTIES,
        },
        "required": ["taskId", "operationId", "baseRevision", "operations"],
    },
}


from tools.registry import registry

for _name, _schema, _handler in [
    ("flowstate_get_assistant_context", FLOWSTATE_ASSISTANT_CONTEXT_SCHEMA, _handle_assistant_context),
    ("flowstate_health", FLOWSTATE_HEALTH_SCHEMA, _handle_health),
    ("flowstate_list_tasks", FLOWSTATE_LIST_TASKS_SCHEMA, _handle_list_tasks),
    ("flowstate_search_tasks", FLOWSTATE_SEARCH_TASKS_SCHEMA, _handle_search_tasks),
    ("flowstate_get_task", FLOWSTATE_GET_TASK_SCHEMA, _handle_get_task),
    ("flowstate_create_task", FLOWSTATE_CREATE_TASK_SCHEMA, _handle_create_task),
    ("flowstate_update_task", FLOWSTATE_UPDATE_TASK_SCHEMA, _handle_update_task),
    ("flowstate_complete_task", FLOWSTATE_COMPLETE_TASK_SCHEMA, _handle_complete_task),
    ("flowstate_delete_task", FLOWSTATE_DELETE_TASK_SCHEMA, _handle_delete_task),
    ("flowstate_restore_task", FLOWSTATE_RESTORE_TASK_SCHEMA, _handle_restore_task),
    ("flowstate_reopen_task", FLOWSTATE_REOPEN_TASK_SCHEMA, _handle_reopen_task),
    ("flowstate_get_current_timer", FLOWSTATE_CURRENT_TIMER_SCHEMA, _handle_current_timer),
    ("flowstate_get_timer_diagnostics", FLOWSTATE_TIMER_DIAGNOSTICS_SCHEMA, _handle_timer_diagnostics),
    ("flowstate_list_task_instances", FLOWSTATE_LIST_TASK_INSTANCES_SCHEMA, _handle_list_task_instances),
    ("flowstate_work_block_command", FLOWSTATE_WORK_BLOCK_COMMAND_SCHEMA, _handle_work_block_command),
    ("flowstate_schedule_task_instance", FLOWSTATE_SCHEDULE_TASK_INSTANCE_SCHEMA, _handle_schedule_task_instance),
    ("flowstate_done_for_now", FLOWSTATE_DONE_FOR_NOW_SCHEMA, _handle_done_for_now),
    ("flowstate_merge_tasks", FLOWSTATE_MERGE_TASKS_SCHEMA, _handle_merge_tasks),
    ("flowstate_list_subtasks", FLOWSTATE_LIST_SUBTASKS_SCHEMA, _handle_list_subtasks),
    ("flowstate_create_subtask", FLOWSTATE_CREATE_SUBTASK_SCHEMA, _handle_create_subtask),
    ("flowstate_update_subtask", FLOWSTATE_UPDATE_SUBTASK_SCHEMA, _handle_update_subtask),
    ("flowstate_delete_subtask", FLOWSTATE_DELETE_SUBTASK_SCHEMA, _handle_delete_subtask),
    ("flowstate_subtask_batch", FLOWSTATE_SUBTASK_BATCH_SCHEMA, _handle_subtask_batch),
]:
    registry.register(
        name=_name,
        toolset="flowstate",
        schema=_schema,
        handler=_handler,
        check_fn=_check_flowstate_available,
        emoji="📋",
    )
