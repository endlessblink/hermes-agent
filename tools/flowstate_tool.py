"""Flow State local task API tools.

Registers Hermes-callable tools that talk to Flow State's localhost Local Task
API. Flow State remains the source of truth; Hermes is only a client.
"""

import hashlib
import json
import logging
import math
import os
import re
from datetime import datetime
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://127.0.0.1:5577"
_FLOW_STATE_API_URL: str = ""
_FLOW_STATE_API_TOKEN: str = ""
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_STATUS_FILTERS = {"todo", "open", "done"}
_VALID_DUE_FILTERS = {"today", "overdue", "open"}
_VALID_TASK_STATUSES = {"todo", "done"}
_VALID_PRIORITIES = {"low", "medium", "high"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SCOPE_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}$")
_ERROR_CODE_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$"
)
_MAX_SAFE_INTEGER = 2**53 - 1
_SUBTASK_FIELDS = {
    "id", "parentTaskId", "clientId", "title", "description", "doneEnough",
    "estimateMinutes", "completedPomodoros", "canvasPosition", "isCompleted",
    "order", "createdAt", "updatedAt",
}


class _FlowStateApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        status: int,
        current_revision: Optional[int] = None,
    ):
        super().__init__(message)
        self.code = code
        self.status = status
        self.current_revision = current_revision


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


def _is_valid_date_or_due_filter(value: str) -> bool:
    return value in _VALID_DUE_FILTERS or bool(_DATE_ONLY_RE.match(value))


def _compact_http_error(exc: urllib.error.HTTPError) -> _FlowStateApiError:
    code = f"http_{exc.code}"
    current_revision = None
    try:
        raw = exc.read().decode("utf-8", errors="replace")
        payload = json.loads(raw) if raw else {}
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = error.get("message")
            candidate_code = str(error.get("code") or "")
            if _ERROR_CODE_RE.fullmatch(candidate_code):
                code = candidate_code
            candidate = error.get("currentRevision")
            if candidate is None and isinstance(error.get("details"), dict):
                candidate = error["details"].get("currentRevision")
            if (
                isinstance(candidate, int)
                and not isinstance(candidate, bool)
                and 1 <= candidate <= _MAX_SAFE_INTEGER
            ):
                current_revision = candidate
        else:
            message = error
            if isinstance(payload, dict) and payload.get("code"):
                candidate_code = str(payload["code"])
                if _ERROR_CODE_RE.fullmatch(candidate_code):
                    code = candidate_code
    except Exception:
        message = None
    if not message:
        message = exc.reason or "HTTP error"
    if exc.code == 401:
        message = "Flow State Local Task API rejected the bearer token. Check FLOW_STATE_API_TOKEN."
        code = "unauthorized"
    if exc.code == 503:
        message = "Flow State Local Task API is running but is not signed in."
        code = "signed_out"
    return _FlowStateApiError(
        str(message), code=code, status=exc.code, current_revision=current_revision
    )


def _request(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
        raise RuntimeError(
            f"Flow State Local Task API is unavailable at {base_url}. "
            "Open Flow State and enable Local Task API, then check FLOW_STATE_API_URL."
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Flow State Local Task API timed out at {base_url}.") from exc

    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("Flow State Local Task API returned non-JSON data.") from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError("Flow State Local Task API returned an error response.")
    if not isinstance(payload, dict):
        raise RuntimeError("Flow State Local Task API returned an unexpected response.")
    return payload


def _tool_result(payload: Dict[str, Any]) -> str:
    return json.dumps({"result": payload})


def _tool_error(message: str) -> str:
    from tools.registry import tool_error

    return tool_error(message)


def _validated_inventory_receipt(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Fail closed before a model or monitor treats an inventory as exact."""

    try:
        captured = datetime.fromisoformat(
            str(payload["capturedAt"]).replace("Z", "+00:00")
        )
        items = payload["items"]
        page = payload["page"]
        ids = [item["id"] for item in items]
        valid = (
            payload.get("source") == "flowstate"
            and isinstance(payload.get("scope"), str)
            and bool(payload["scope"].strip())
            and payload.get("scopeKind") in {"personal", "workspace"}
            and bool(
                _SCOPE_FINGERPRINT_RE.fullmatch(
                    str(payload.get("scopeFingerprint") or "")
                )
            )
            and isinstance(payload.get("appVersion"), str)
            and bool(payload["appVersion"])
            and captured.utcoffset() is not None
            and payload.get("fresh") is True
            and payload.get("complete") is True
            and isinstance(items, list)
            and all(isinstance(item, dict) for item in items)
            and all(
                isinstance(task_id, str) and bool(_UUID_RE.fullmatch(task_id))
                for task_id in ids
            )
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


def _typed_flowstate_error(exc: _FlowStateApiError) -> str:
    from tools.registry import tool_error

    messages = {
        "unauthorized": "Flow State Local Task API rejected the bearer token. Check FLOW_STATE_API_TOKEN.",
        "signed_out": "Flow State Local Task API is running but is not signed in.",
        "stale_revision": "Task changed since preview.",
        "incompatible_revision": "Task changed since preview.",
        "invalid_existing_subtasks": "Existing FlowState subtask data needs bounded repair.",
        "invalid_inventory_receipt": (
            "Flow State returned an invalid inventory receipt; no exact count is available."
        ),
        "subtask_limit_exceeded": "This task already has the maximum number of subtasks.",
        "not_found": "task not found",
    }
    message = messages.get(exc.code, "Flow State request failed.")
    extra = {"code": exc.code, "status": exc.status}
    if exc.code in {"stale_revision", "incompatible_revision"} and exc.current_revision is not None:
        extra["currentRevision"] = exc.current_revision
    return tool_error(message, **extra)


def _check_flowstate_available() -> bool:
    base_url, token = _get_config()
    if base_url != _DEFAULT_BASE_URL:
        return True
    return bool(token)


def _handle_health(args: dict, **kw) -> str:
    try:
        return _tool_result(_request("GET", "/api/health"))
    except Exception as exc:
        logger.error("flowstate_health error: %s", exc)
        return _tool_error(str(exc))


def _handle_list_tasks(args: dict, **kw) -> str:
    status = args.get("status")
    due = args.get("due")
    limit = args.get("limit")

    if status not in (None, "") and status not in _VALID_STATUS_FILTERS:
        return _tool_error("status must be todo|open|done")
    if due not in (None, "") and not _is_valid_date_or_due_filter(str(due)):
        return _tool_error("due must be today|overdue|open|YYYY-MM-DD")
    use_inventory = status in (None, "", "todo", "open") and due in (
        None,
        "",
    )
    max_limit = 100 if use_inventory else 25
    if limit not in (None, ""):
        try:
            n = int(limit)
        except (TypeError, ValueError):
            return _tool_error(
                f"limit must be an integer from 1 to {max_limit}"
            )
        if n < 1 or n > max_limit:
            return _tool_error(
                f"limit must be an integer from 1 to {max_limit}"
            )
        limit = n

    try:
        if use_inventory:
            params = urllib.parse.urlencode(
                {"limit": limit} if limit not in (None, "") else {}
            )
            suffix = f"?{params}" if params else ""
            payload = _request(
                "GET",
                f"/api/tasks/inventory{suffix}",
            )
            return _tool_result(_validated_inventory_receipt(payload))
        params = urllib.parse.urlencode(
            {
                key: value
                for key, value in {
                    "status": status,
                    "due": due,
                    "limit": limit,
                }.items()
                if value not in (None, "")
            }
        )
        suffix = f"?{params}" if params else ""
        return _tool_result(_request("GET", f"/api/tasks{suffix}"))
    except _FlowStateApiError as exc:
        logger.error(
            "flowstate_list_tasks API error: status=%s code=%s",
            exc.status,
            exc.code,
        )
        return _typed_flowstate_error(exc)
    except Exception as exc:
        logger.error("flowstate_list_tasks error: %s", exc)
        return _tool_error(str(exc))


def _handle_create_task(args: dict, **kw) -> str:
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
    elif not _DATE_ONLY_RE.match(str(due_date)):
        return _tool_error("dueDate must be YYYY-MM-DD")

    project_id = args.get("projectId")
    if project_id in ("", None):
        project_id = None

    body = {
        "title": title,
        "description": str(args.get("description") or ""),
        "priority": priority,
        "dueDate": due_date,
        "projectId": project_id,
    }
    try:
        return _tool_result(_request("POST", "/api/tasks", body))
    except Exception as exc:
        logger.error("flowstate_create_task error: %s", exc)
        return _tool_error(str(exc))


def _handle_update_task(args: dict, **kw) -> str:
    task_id = str(args.get("id") or "").strip()
    if not task_id:
        return _tool_error("id is required")

    body: Dict[str, Any] = {}
    if "status" in args and args.get("status") not in ("", None):
        status = args["status"]
        if status not in _VALID_TASK_STATUSES:
            return _tool_error("status must be todo|done")
        body["status"] = status
    if "title" in args and args.get("title") not in ("", None):
        title = str(args["title"]).strip()
        if not title:
            return _tool_error("title cannot be empty")
        body["title"] = title
    if "priority" in args:
        priority = args.get("priority")
        if priority in ("", None):
            body["priority"] = None
        elif priority in _VALID_PRIORITIES:
            body["priority"] = priority
        else:
            return _tool_error("priority must be low|medium|high or null")
    if "dueDate" in args:
        due_date = args.get("dueDate")
        if due_date in ("", None):
            body["dueDate"] = None
        elif _DATE_ONLY_RE.match(str(due_date)):
            body["dueDate"] = due_date
        else:
            return _tool_error("dueDate must be YYYY-MM-DD")
    if "progress" in args and args.get("progress") not in ("", None):
        try:
            progress = float(args["progress"])
        except (TypeError, ValueError):
            return _tool_error("progress must be a number from 0 to 100")
        if progress < 0 or progress > 100:
            return _tool_error("progress must be a number from 0 to 100")
        body["progress"] = progress

    if not body:
        return _tool_error("provide at least one field to update")

    try:
        return _tool_result(_request("PATCH", f"/api/tasks/{urllib.parse.quote(task_id, safe='')}", body))
    except Exception as exc:
        logger.error("flowstate_update_task error: %s", exc)
        return _tool_error(str(exc))


def _handle_delete_task(args: dict, **kw) -> str:
    task_id = str(args.get("id") or "").strip()
    if not task_id:
        return _tool_error("id is required")
    try:
        return _tool_result(_request("DELETE", f"/api/tasks/{urllib.parse.quote(task_id, safe='')}"))
    except Exception as exc:
        logger.error("flowstate_delete_task error: %s", exc)
        return _tool_error(str(exc))


def _handle_current_timer(args: dict, **kw) -> str:
    try:
        return _tool_result(_request("GET", "/api/timer/current"))
    except Exception as exc:
        logger.error("flowstate_get_current_timer error: %s", exc)
        return _tool_error(str(exc))


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        return False
    try:
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _trimmed_text(value: Any, limit: int) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip() and len(value) <= limit


def _subtask_path(
    task_id: str, *, limit: Optional[int] = None, cursor: Optional[str] = None
) -> str:
    path = f"/api/tasks/{urllib.parse.quote(task_id, safe='')}/subtasks"
    query = {}
    if limit is not None:
        query["limit"] = str(limit)
    if cursor is not None:
        query["cursor"] = cursor
    return f"{path}?{urllib.parse.urlencode(query)}" if query else path


def _valid_subtask_row(value: Any, index: int, task_id: str) -> bool:
    if not isinstance(value, dict) or set(value) - _SUBTASK_FIELDS:
        return False
    if not _trimmed_text(value.get("id"), 256) or not _trimmed_text(value.get("title"), 500):
        return False
    if value.get("order") != index or isinstance(value.get("order"), bool):
        return False
    if "parentTaskId" in value and value["parentTaskId"] != task_id:
        return False
    if "clientId" in value and not _trimmed_text(value["clientId"], 160):
        return False
    if "description" in value and (
        not isinstance(value["description"], str) or len(value["description"]) > 10_000
    ):
        return False
    if "doneEnough" in value and not (
        value["doneEnough"] is None
        or isinstance(value["doneEnough"], str) and len(value["doneEnough"]) <= 2_000
    ):
        return False
    if "estimateMinutes" in value and not (
        value["estimateMinutes"] is None
        or _is_positive_int(value["estimateMinutes"]) and value["estimateMinutes"] <= 1_440
    ):
        return False
    if "completedPomodoros" in value and not (
        isinstance(value["completedPomodoros"], int)
        and not isinstance(value["completedPomodoros"], bool)
        and 0 <= value["completedPomodoros"] <= 1_000_000
    ):
        return False
    if "isCompleted" in value and not isinstance(value["isCompleted"], bool):
        return False
    if "canvasPosition" in value and value["canvasPosition"] is not None:
        position = value["canvasPosition"]
        if (
            not isinstance(position, dict)
            or set(position) != {"x", "y"}
            or any(
                not isinstance(position[axis], (int, float))
                or isinstance(position[axis], bool)
                or not math.isfinite(position[axis])
                for axis in ("x", "y")
            )
        ):
            return False
    return all(_is_timestamp(value[key]) for key in ("createdAt", "updatedAt") if key in value)


def _valid_subtask_collection(
    value: Any,
    task_id: str,
    *,
    start_order: int = 0,
    max_items: int = 10_001,
) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= max_items
        and all(
            _valid_subtask_row(item, start_order + index, task_id)
            for index, item in enumerate(value)
        )
        and len({item["id"] for item in value}) == len(value)
        and len({item["clientId"] for item in value if "clientId" in item})
        == sum("clientId" in item for item in value)
    )


def _valid_subtask_page(
    value: Any,
    subtasks: Any,
    *,
    requested_limit: Optional[int],
    cursor: Optional[str],
    task_id: str,
) -> bool:
    if not isinstance(value, dict) or set(value) != {"limit", "total", "hasMore", "nextCursor"}:
        return False
    limit = value.get("limit")
    total = value.get("total")
    has_more = value.get("hasMore")
    next_cursor = value.get("nextCursor")
    if (
        not _is_positive_int(limit)
        or limit > 100
        or requested_limit is not None and limit != requested_limit
        or not isinstance(total, int)
        or isinstance(total, bool)
        or not 0 <= total <= 10_001
        or not isinstance(has_more, bool)
        or has_more and not _trimmed_text(next_cursor, 2_048)
        or not has_more and next_cursor is not None
        or not isinstance(subtasks, list)
        or len(subtasks) > limit
    ):
        return False
    if not subtasks:
        return cursor is None and total == 0 and not has_more
    start_order = subtasks[0].get("order") if isinstance(subtasks[0], dict) else None
    if (
        not isinstance(start_order, int)
        or isinstance(start_order, bool)
        or start_order < 0
        or cursor is None and start_order != 0
        or cursor is not None and start_order < 1
        or start_order + len(subtasks) > total
        or has_more != (start_order + len(subtasks) < total)
    ):
        return False
    return _valid_subtask_collection(
        subtasks,
        task_id,
        start_order=start_order,
        max_items=100,
    )


def _handle_list_subtasks(args: dict, **kw) -> str:
    task_id = args.get("taskId")
    if not _trimmed_text(task_id, 160):
        return _tool_error("taskId is required")
    limit = args.get("limit")
    if limit is not None and (not _is_positive_int(limit) or limit > 100):
        return _tool_error("limit must be an integer from 1 to 100")
    cursor = args.get("cursor")
    if cursor is not None and not _trimmed_text(cursor, 2_048):
        return _tool_error("cursor must be non-empty bounded text")
    try:
        payload = _request("GET", _subtask_path(task_id, limit=limit, cursor=cursor))
        task = payload.get("task")
        subtasks = payload.get("subtasks")
        page = payload.get("page")
        if not (
            payload.get("ok") is True
            and isinstance(task, dict)
            and task.get("id") == task_id
            and isinstance(task.get("title"), str)
            and (
                task.get("workspaceId") is None
                or isinstance(task.get("workspaceId"), str)
                and bool(_UUID_RE.fullmatch(task["workspaceId"]))
            )
        ):
            raise ValueError
        if not (
            _is_positive_int(task.get("canonicalRevision"))
            and task["canonicalRevision"] <= _MAX_SAFE_INTEGER
            and _is_timestamp(task.get("canonicalUpdatedAt"))
            and _valid_subtask_page(
                page,
                subtasks,
                requested_limit=limit,
                cursor=cursor,
                task_id=task_id,
            )
        ):
            raise ValueError
        return _tool_result({
            "ok": True,
            "task": {
                "id": task["id"],
                "title": task["title"],
                "workspaceId": task.get("workspaceId"),
                "canonicalRevision": task["canonicalRevision"],
                "canonicalUpdatedAt": task["canonicalUpdatedAt"],
            },
            "subtasks": subtasks,
            "page": page,
        })
    except _FlowStateApiError as exc:
        logger.error(
            "flowstate_list_subtasks API error: status=%s code=%s", exc.status, exc.code
        )
        return _typed_flowstate_error(exc)
    except RuntimeError as exc:
        logger.error("flowstate_list_subtasks connector error: %s", type(exc).__name__)
        return _tool_error(str(exc))
    except (KeyError, TypeError, ValueError):
        logger.error("flowstate_list_subtasks failed", exc_info=True)
        return _tool_error("Fresh canonical subtask state could not be verified")


def _normalize_subtask_operation(operation: Any) -> tuple[Optional[dict], Optional[str]]:
    if not isinstance(operation, dict):
        return None, "each subtask operation must be an object"
    kind = operation.get("kind")
    if kind not in {"create", "update", "delete"}:
        return None, "each operation kind must be create|update|delete"
    identity = "clientId" if kind == "create" else "subtaskId"
    allowed = {"kind", identity}
    if kind != "delete":
        allowed |= {
            "title", "description", "doneEnough", "estimateMinutes", "completedPomodoros",
            "canvasPosition", "isCompleted", "order",
        }
    if unknown := sorted(set(operation) - allowed):
        return None, f"unsupported subtask operation fields: {', '.join(unknown)}"
    limit = 160 if kind == "create" else 256
    if not _trimmed_text(operation.get(identity), limit):
        return None, f"{kind} operations require a trimmed {identity}"
    normalized: Dict[str, Any] = {"kind": kind, identity: operation[identity]}
    if kind == "delete":
        return normalized, None
    if kind == "create" and not _trimmed_text(operation.get("title"), 500):
        return None, "create operations require a trimmed title"
    for key in ("title", "description", "doneEnough"):
        if key not in operation:
            continue
        value = operation[key]
        if key == "doneEnough" and value is None:
            normalized[key] = None
            continue
        if not isinstance(value, str):
            return None, f"{key} must be text"
        if key == "title" and not _trimmed_text(value, 500):
            return None, "title must be non-empty trimmed text"
        if key == "description" and len(value) > 10_000:
            return None, "description must be at most 10000 characters"
        if key == "doneEnough" and len(value) > 2_000:
            return None, "doneEnough must be at most 2000 characters"
        normalized[key] = value
    if "estimateMinutes" in operation:
        value = operation["estimateMinutes"]
        if value is not None and (not _is_positive_int(value) or value > 1_440):
            return None, "estimateMinutes must be an integer from 1 to 1440"
        normalized["estimateMinutes"] = value
    if "completedPomodoros" in operation:
        value = operation["completedPomodoros"]
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1_000_000:
            return None, "completedPomodoros must be a non-negative integer"
        normalized["completedPomodoros"] = value
    if "canvasPosition" in operation:
        value = operation["canvasPosition"]
        if value is not None and (
            not isinstance(value, dict)
            or set(value) != {"x", "y"}
            or any(
                not isinstance(value[axis], (int, float))
                or isinstance(value[axis], bool)
                or not math.isfinite(value[axis])
                for axis in ("x", "y")
            )
        ):
            return None, "canvasPosition must be null or numeric x and y"
        normalized["canvasPosition"] = value
    if "isCompleted" in operation:
        if not isinstance(operation["isCompleted"], bool):
            return None, "isCompleted must be a boolean"
        normalized["isCompleted"] = operation["isCompleted"]
    if "order" in operation:
        value = operation["order"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None, "order must be a non-negative integer"
        normalized["order"] = value
    if kind == "update" and set(normalized) == {"kind", "subtaskId"}:
        return None, "update operations require at least one changed field"
    return normalized, None


def _subtask_operations_reflected(operations: list[dict], subtasks: Any) -> bool:
    if not isinstance(subtasks, list) or not all(isinstance(item, dict) for item in subtasks):
        return False
    for operation in operations:
        if operation["kind"] == "delete":
            if any(item.get("id") == operation["subtaskId"] for item in subtasks):
                return False
            continue
        identity_key = "clientId" if operation["kind"] == "create" else "id"
        identity = operation.get("clientId", operation.get("subtaskId"))
        match = next((item for item in subtasks if item.get(identity_key) == identity), None)
        if match is None:
            return False
        if any(
            match.get(key) != value
            for key, value in operation.items()
            if key not in {"kind", "order", "clientId", "subtaskId"}
        ):
            return False
        if "order" in operation and subtasks.index(match) != operation["order"]:
            return False
    return True


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _valid_subtask_preview(payload: Any, task_id: str, body: dict) -> bool:
    normalized = payload.get("normalizedPayload") if isinstance(payload, dict) else None
    return (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("result") == "preview"
        and payload.get("contractVersion") == "task-v1"
        and payload.get("action") == "subtask_batch"
        and payload.get("taskId") == task_id
        and payload.get("operationId") == body["operationId"]
        and payload.get("baseRevision") == body["baseRevision"]
        and isinstance(normalized, dict)
        and normalized.get("taskId") == task_id
        and normalized.get("operations") == body["operations"]
        and bool(_SHA256_RE.fullmatch(str(payload.get("previewDigest") or "")))
        and bool(_SHA256_RE.fullmatch(str(payload.get("requestHash") or "")))
        and _is_timestamp(payload.get("previewExpiresAt"))
        and isinstance(payload.get("readBack"), dict)
        and payload["readBack"].get("id") == task_id
        and payload["readBack"].get("canonicalRevision") == body["baseRevision"]
        and _valid_subtask_collection(payload["readBack"].get("subtasks"), task_id)
    )


def _valid_subtask_receipt(payload: Any, task_id: str, body: dict) -> bool:
    receipt = payload.get("receipt") if isinstance(payload, dict) else None
    if not isinstance(receipt, dict):
        return False
    read_back = receipt.get("readBack")
    affected = receipt.get("affected")
    return (
        payload.get("result") == "committed"
        and payload.get("operationId") == body["operationId"]
        and payload.get("requestHash") == body["requestHash"]
        and receipt.get("ok") is True
        and receipt.get("status") in {"committed", "replayed"}
        and (
            "replayed" not in receipt
            or isinstance(receipt.get("replayed"), bool)
            and receipt.get("replayed") is (receipt.get("status") == "replayed")
        )
        and receipt.get("contractVersion") == "task-v1"
        and receipt.get("source") == "local-api"
        and receipt.get("entityType") == "task"
        and receipt.get("operationId") == body["operationId"]
        and receipt.get("requestHash") == body["requestHash"]
        and receipt.get("action") == "subtask_batch"
        and receipt.get("entityId") == task_id
        and _is_positive_int(receipt.get("canonicalRevision"))
        and _is_timestamp(receipt.get("canonicalUpdatedAt"))
        and isinstance(receipt.get("changeSequence"), int)
        and not isinstance(receipt.get("changeSequence"), bool)
        and receipt["changeSequence"] >= 0
        and _is_timestamp(receipt.get("committedAt"))
        and isinstance(read_back, dict)
        and read_back.get("id") == task_id
        and read_back.get("canonicalRevision") == receipt.get("canonicalRevision")
        and read_back.get("canonicalUpdatedAt") == receipt.get("canonicalUpdatedAt")
        and receipt.get("readBackHash") == _canonical_hash(read_back)
        and _valid_subtask_collection(read_back.get("subtasks"), task_id)
        and _subtask_operations_reflected(body["operations"], read_back.get("subtasks"))
        and isinstance(affected, list)
        and len(affected) == 1
        and isinstance(affected[0], dict)
        and affected[0].get("entityType") == "task"
        and affected[0].get("entityId") == task_id
        and affected[0].get("action") == "update"
        and affected[0].get("canonicalRevision") == receipt.get("canonicalRevision")
        and affected[0].get("changeSequence") == receipt.get("changeSequence")
        and affected[0].get("readBackHash") == _canonical_hash(affected[0].get("readBack"))
        and affected[0].get("readBack") == read_back
    )


def _handle_subtask_batch(args: dict, **kw) -> str:
    task_id = args.get("taskId")
    if not _trimmed_text(task_id, 160):
        return _tool_error("taskId is required")
    operations = args.get("operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 50:
        return _tool_error("operations must contain 1 to 50 items")
    normalized = []
    identities = set()
    for operation in operations:
        item, error = _normalize_subtask_operation(operation)
        if error:
            return _tool_error(error)
        assert item is not None
        identity = (
            "clientId" if item["kind"] == "create" else "subtaskId",
            item.get("clientId", item.get("subtaskId")),
        )
        if identity in identities:
            return _tool_error("subtask operation identities must be unique")
        identities.add(identity)
        normalized.append(item)
    operation_id = args.get("operationId")
    if not _trimmed_text(operation_id, 160):
        return _tool_error("operationId is required and must be trimmed")
    revision = args.get("baseRevision")
    if not _is_positive_int(revision) or revision > _MAX_SAFE_INTEGER:
        return _tool_error("baseRevision is required and must be a positive integer")
    proposal_id = args.get("proposalId")
    proposal_revision = args.get("proposalRevision")
    if (
        not _trimmed_text(proposal_id, 120)
        or not _is_positive_int(proposal_revision)
        or proposal_revision > _MAX_SAFE_INTEGER
    ):
        return _tool_error("proposalId and proposalRevision are required")
    preview = args.get("preview", True)
    if not isinstance(preview, bool):
        return _tool_error("preview must be a boolean")
    body = {
        "operationId": operation_id,
        "baseRevision": revision,
        "preview": preview,
        "operations": normalized,
    }
    if not preview:
        for key in ("previewDigest", "requestHash"):
            if not _SHA256_RE.fullmatch(str(args.get(key) or "")):
                return _tool_error(f"{key} is required when preview is false")
            body[key] = args[key]
        if not _is_timestamp(args.get("previewExpiresAt")):
            return _tool_error("previewExpiresAt is required when preview is false")
        body["previewExpiresAt"] = args["previewExpiresAt"]
        capability = args.get("approvalCapability")
        if not isinstance(capability, str) or not capability:
            return _tool_error("approvalCapability is required for apply")
        try:
            from agent.subtask_approval_capabilities import subtask_approval_capabilities
            from tools.approval import get_current_session_key

            subtask_approval_capabilities.authorize(
                get_current_session_key(default=""),
                capability,
                {
                    "taskId": task_id,
                    "operationId": operation_id,
                    "baseRevision": revision,
                    "operations": normalized,
                    "previewDigest": body["previewDigest"],
                    "previewExpiresAt": body["previewExpiresAt"],
                    "requestHash": body["requestHash"],
                    "proposalId": proposal_id,
                    "proposalRevision": proposal_revision,
                },
            )
        except Exception:
            return _tool_error("approvalCapability is invalid for this exact apply")
    try:
        payload = _request("POST", f"{_subtask_path(task_id)}/batch", body)
        if preview:
            if not _valid_subtask_preview(payload, task_id, body):
                return _tool_error("Canonical subtask preview could not be verified")
            payload = {**payload, "approvalRequest": {
                "action": "subtask_batch",
                "contractVersion": "task-v1",
                "taskId": task_id,
                "operationId": operation_id,
                "baseRevision": revision,
                "operations": normalized,
                "previewDigest": payload["previewDigest"],
                "previewExpiresAt": payload["previewExpiresAt"],
                "requestHash": payload["requestHash"],
                "proposalId": proposal_id,
                "proposalRevision": proposal_revision,
            }}
        elif not _valid_subtask_receipt(payload, task_id, body):
            return _tool_error("Canonical subtask receipt could not be verified")
        return _tool_result(payload)
    except _FlowStateApiError as exc:
        logger.error(
            "flowstate_subtask_batch API error: status=%s code=%s", exc.status, exc.code
        )
        return _typed_flowstate_error(exc)
    except Exception as exc:
        logger.error("flowstate_subtask_batch error: %s", exc)
        return _tool_error(str(exc))


FLOWSTATE_HEALTH_SCHEMA = {
    "name": "flowstate_health",
    "description": "Check whether the local Flow State API sidecar is reachable.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

FLOWSTATE_LIST_TASKS_SCHEMA = {
    "name": "flowstate_list_tasks",
    "description": (
        "List Flow State tasks. Flow State is the user's personal task app; "
        "it is not a project name. Use returned task ids for later updates."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "Optional: todo, open, or done."},
            "due": {"type": "string", "description": "Optional: today, overdue, open, or YYYY-MM-DD."},
            "limit": {"type": "integer", "description": "Optional result cap, 1-25."},
        },
        "required": [],
    },
}

FLOWSTATE_CREATE_TASK_SCHEMA = {
    "name": "flowstate_create_task",
    "description": (
        "Create a personal task in Flow State. Omit projectId unless the user "
        "explicitly provided a known Flow State project id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Task title."},
            "description": {"type": "string", "description": "Optional task description."},
            "priority": {"type": "string", "description": "Optional: low, medium, high, or null."},
            "dueDate": {"type": "string", "description": "Optional YYYY-MM-DD due date."},
            "projectId": {"type": "string", "description": "Optional known Flow State project id."},
        },
        "required": ["title"],
    },
}

FLOWSTATE_UPDATE_TASK_SCHEMA = {
    "name": "flowstate_update_task",
    "description": "Update an existing Flow State task by exact task id.",
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Exact Flow State task id."},
            "status": {"type": "string", "description": "Optional: todo or done."},
            "title": {"type": "string", "description": "Optional new title."},
            "priority": {"type": "string", "description": "Optional: low, medium, high, or null."},
            "dueDate": {"type": "string", "description": "Optional YYYY-MM-DD or null."},
            "progress": {"type": "number", "description": "Optional progress from 0 to 100."},
        },
        "required": ["id"],
    },
}

FLOWSTATE_DELETE_TASK_SCHEMA = {
    "name": "flowstate_delete_task",
    "description": "Soft-delete an existing Flow State task by exact task id.",
    "parameters": {
        "type": "object",
        "properties": {"id": {"type": "string", "description": "Exact Flow State task id."}},
        "required": ["id"],
    },
}

FLOWSTATE_CURRENT_TIMER_SCHEMA = {
    "name": "flowstate_get_current_timer",
    "description": "Get the current Flow State timer session, if one is active.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

FLOWSTATE_LIST_SUBTASKS_SCHEMA = {
    "name": "flowstate_list_subtasks",
    "description": (
        "Read fresh mutation-authoritative ordered subtasks and the parent canonical revision."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string", "description": "Exact parent task id."},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Optional canonical page size; defaults to 100.",
            },
            "cursor": {
                "type": "string",
                "description": "Opaque nextCursor from the preceding page.",
            },
        },
        "required": ["taskId"],
    },
}

_SUBTASK_PROOF_PROPERTIES = {
    "operationId": {"type": "string", "description": "Stable preview/apply operation id."},
    "baseRevision": {"type": "integer", "minimum": 1},
    "preview": {"type": "boolean", "description": "Defaults true."},
    "previewDigest": {"type": "string"},
    "previewExpiresAt": {"type": "string"},
    "requestHash": {"type": "string"},
    "proposalId": {"type": "string"},
    "proposalRevision": {"type": "integer", "minimum": 1},
    "approvalCapability": {
        "type": "string",
        "description": "Trusted gateway token issued only for the user's exact approved preview.",
    },
}

FLOWSTATE_SUBTASK_BATCH_SCHEMA = {
    "name": "flowstate_subtask_batch",
    "description": (
        "Preview or atomically apply 1-50 ordered subtask operations. Preview first and copy "
        "the returned approvalRequest unchanged into the interactive approval UI."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string"},
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
                        "estimateMinutes": {"type": ["integer", "null"]},
                        "completedPomodoros": {"type": "integer"},
                        "canvasPosition": {"type": ["object", "null"]},
                        "isCompleted": {"type": "boolean"},
                        "order": {"type": "integer", "minimum": 0},
                    },
                    "required": ["kind"],
                    "additionalProperties": False,
                },
            },
            **_SUBTASK_PROOF_PROPERTIES,
        },
        "required": [
            "taskId", "operationId", "baseRevision", "operations",
            "proposalId", "proposalRevision",
        ],
    },
}


from tools.registry import registry

_FLOWSTATE_TOOL_REGISTRATIONS = [
    ("flowstate_health", FLOWSTATE_HEALTH_SCHEMA, _handle_health),
    ("flowstate_list_tasks", FLOWSTATE_LIST_TASKS_SCHEMA, _handle_list_tasks),
    ("flowstate_create_task", FLOWSTATE_CREATE_TASK_SCHEMA, _handle_create_task),
    ("flowstate_update_task", FLOWSTATE_UPDATE_TASK_SCHEMA, _handle_update_task),
    ("flowstate_delete_task", FLOWSTATE_DELETE_TASK_SCHEMA, _handle_delete_task),
    ("flowstate_get_current_timer", FLOWSTATE_CURRENT_TIMER_SCHEMA, _handle_current_timer),
    ("flowstate_list_subtasks", FLOWSTATE_LIST_SUBTASKS_SCHEMA, _handle_list_subtasks),
    ("flowstate_subtask_batch", FLOWSTATE_SUBTASK_BATCH_SCHEMA, _handle_subtask_batch),
]

for _name, _schema, _handler in _FLOWSTATE_TOOL_REGISTRATIONS:
    registry.register(
        name=_name,
        toolset="flowstate",
        schema=_schema,
        handler=_handler,
        check_fn=_check_flowstate_available,
        emoji="📋",
    )
