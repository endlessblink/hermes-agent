"""Flow State local task API tools.

Registers Hermes-callable tools that talk to Flow State's localhost Local Task
API. Flow State remains the source of truth; Hermes is only a client.
"""

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://127.0.0.1:5577"
_FLOW_STATE_API_URL: str = ""
_FLOW_STATE_API_TOKEN: str = ""
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_ONLY_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_VALID_STATUS_FILTERS = {"todo", "open", "done"}
_VALID_DUE_FILTERS = {"today", "overdue", "open"}
_VALID_TASK_STATUSES = {"todo", "done"}
_VALID_PRIORITIES = {"low", "medium", "high"}


class _FlowStateApiError(RuntimeError):
    """Safe, typed Local Task API error with no request credentials attached."""

    def __init__(self, message: str, *, code: str, status: int):
        super().__init__(message)
        self.code = code
        self.status = status


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
    except Exception:
        message = None
        code = None
    if not message:
        message = exc.reason or "HTTP error"
    if exc.code == 401:
        message = "Flow State Local Task API rejected the bearer token. Check FLOW_STATE_API_TOKEN."
        code = code or "unauthorized"
    if exc.code == 503:
        message = "Flow State Local Task API is running but is not signed in."
        code = code or "not_signed_in"
    return _FlowStateApiError(
        str(message),
        code=str(code or f"http_{exc.code}"),
        status=exc.code,
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
        raise RuntimeError(str(payload["error"]))
    if not isinstance(payload, dict):
        raise RuntimeError("Flow State Local Task API returned an unexpected response.")
    return payload


def _tool_result(payload: Dict[str, Any]) -> str:
    return json.dumps({"result": payload})


def _tool_error(message: str) -> str:
    from tools.registry import tool_error

    return tool_error(message)


def _typed_tool_error(exc: _FlowStateApiError) -> str:
    from tools.registry import tool_error

    return tool_error(str(exc), code=exc.code, status=exc.status)


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

    if status not in (None, "") and status not in _VALID_STATUS_FILTERS:
        return _tool_error("status must be todo|open|done")
    if due not in (None, "") and not _is_valid_date_or_due_filter(str(due)):
        return _tool_error("due must be today|overdue|open|YYYY-MM-DD")
    if limit not in (None, ""):
        try:
            n = int(limit)
        except (TypeError, ValueError):
            return _tool_error("limit must be an integer from 1 to 25")
        if n < 1 or n > 25:
            return _tool_error("limit must be an integer from 1 to 25")

    try:
        suffix = f"?{params}" if params else ""
        return _tool_result(_request("GET", f"/api/tasks{suffix}"))
    except Exception as exc:
        logger.error("flowstate_list_tasks error: %s", exc)
        return _tool_error(str(exc))


def _handle_search_tasks(args: dict, **kw) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return _tool_error("query is required")

    limit = args.get("limit")
    params: Dict[str, Any] = {"q": query}
    if limit not in (None, ""):
        if isinstance(limit, bool):
            return _tool_error("limit must be an integer from 1 to 25")
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return _tool_error("limit must be an integer from 1 to 25")
        if limit < 1 or limit > 25:
            return _tool_error("limit must be an integer from 1 to 25")
        params["limit"] = limit

    try:
        return _tool_result(_request("GET", f"/api/tasks/search?{urllib.parse.urlencode(params)}"))
    except Exception as exc:
        logger.error("flowstate_search_tasks error: %s", exc)
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
    if not preview and not request_id:
        return _tool_error("requestId is required when preview is false")
    if not preview and not preview_version:
        return _tool_error("previewVersion is required when preview is false")

    body: Dict[str, Any] = {"preview": preview}
    if next_due_date is not None:
        body["nextDueDate"] = next_due_date
    if request_id:
        body["requestId"] = request_id
    if preview_version:
        body["previewVersion"] = preview_version

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
    if not preview and not request_id:
        return _tool_error("requestId is required when preview is false")
    if not preview and not preview_version:
        return _tool_error("previewVersion is required when preview is false")

    body: Dict[str, Any] = {
        "duplicateTaskId": duplicate_task_id,
        "preview": preview,
    }
    if request_id:
        body["requestId"] = request_id
    if preview_version:
        body["previewVersion"] = preview_version

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


def _handle_subtask_batch(args: dict, **kw) -> str:
    task_id = str(args.get("taskId") or "").strip()
    if not task_id:
        return _tool_error("taskId is required")
    operations = args.get("operations")
    if not isinstance(operations, list) or not operations or len(operations) > 50:
        return _tool_error("operations must contain 1 to 50 items")
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("action") not in {"create", "update", "delete"}:
            return _tool_error("each operation action must be create|update|delete")
        if operation["action"] == "create" and not str(operation.get("title") or "").strip():
            return _tool_error("create operations require title")
        if operation["action"] in {"update", "delete"} and not str(operation.get("subtaskId") or "").strip():
            return _tool_error(f"{operation['action']} operations require subtaskId")

    body: Dict[str, Any] = {"operations": operations, **_preview_metadata(args)}
    error = _validate_apply_request(body)
    if error:
        return _tool_error(error)
    try:
        return _tool_result(_request("POST", f"{_subtask_path(task_id)}/batch", body))
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
        "List Flow State tasks. Flow State is the user's personal task app; "
        "it is not a project name. Use returned task ids for later updates. "
        "Do not answer FlowState list/check requests with Markdown, JSON, or "
        "a hermes-ui/task-triage artifact instead of this tool."
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


FLOWSTATE_SEARCH_TASKS_SCHEMA = {
    "name": "flowstate_search_tasks",
    "description": (
        "Search the signed-in user's Flow State tasks by title through the read-only Local Task API. "
        "Use this before proposing a mutation when the exact task id is not already known. The response "
        "preserves FlowState's exact task identifiers so similar titles are never treated as duplicates."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Non-empty title search text."},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 25,
                "description": "Optional result cap, 1-25.",
            },
        },
        "required": ["query"],
    },
}


FLOWSTATE_CREATE_TASK_SCHEMA = {
    "name": "flowstate_create_task",
    "description": (
        "Create a personal task in Flow State. Omit projectId unless the user "
        "explicitly provided a known Flow State project id. When the user asks "
        "to create, save, add, or schedule a task in FlowState, call this tool; "
        "do not substitute Markdown, JSON, or a hermes-ui/task-triage artifact."
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
    "description": (
        "Update an existing Flow State task by exact task id. When the user asks "
        "to change, complete, reprioritize, or reschedule a FlowState task, call "
        "this tool instead of returning a passive preview. Generic status or progress "
        "updates are not a substitute for recurring task completion; use Done for now "
        "through flowstate_done_for_now for recurring tasks."
    ),
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


FLOWSTATE_DONE_FOR_NOW_SCHEMA = {
    "name": "flowstate_done_for_now",
    "description": (
        "Preview or apply FlowState's real Done for now operation for one exact recurring task. "
        "Defaults to preview and never treats a generic status, progress, or due-date update as "
        "recurring completion. Apply only after explicit user approval of the preview; preview=false "
        "requires the stable requestId and previewVersion returned by FlowState. The response includes "
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
        "implement or guess merge semantics outside FlowState."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "survivorTaskId": {"type": "string", "description": "Exact task id that will survive."},
            "duplicateTaskId": {"type": "string", "description": "Exact task id to merge and archive."},
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
        },
        "required": ["survivorTaskId", "duplicateTaskId"],
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
                        "action": {"type": "string", "enum": ["create", "update", "delete"]},
                        "subtaskId": {"type": "string"},
                        "title": {"type": "string"},
                        "completed": {"type": "boolean"},
                        "order": {"type": "integer"},
                    },
                    "required": ["action"],
                },
            },
            **_SUBTASK_MUTATION_PROPERTIES,
        },
        "required": ["taskId", "operations"],
    },
}


from tools.registry import registry

for _name, _schema, _handler in [
    ("flowstate_get_assistant_context", FLOWSTATE_ASSISTANT_CONTEXT_SCHEMA, _handle_assistant_context),
    ("flowstate_health", FLOWSTATE_HEALTH_SCHEMA, _handle_health),
    ("flowstate_list_tasks", FLOWSTATE_LIST_TASKS_SCHEMA, _handle_list_tasks),
    ("flowstate_search_tasks", FLOWSTATE_SEARCH_TASKS_SCHEMA, _handle_search_tasks),
    ("flowstate_create_task", FLOWSTATE_CREATE_TASK_SCHEMA, _handle_create_task),
    ("flowstate_update_task", FLOWSTATE_UPDATE_TASK_SCHEMA, _handle_update_task),
    ("flowstate_delete_task", FLOWSTATE_DELETE_TASK_SCHEMA, _handle_delete_task),
    ("flowstate_get_current_timer", FLOWSTATE_CURRENT_TIMER_SCHEMA, _handle_current_timer),
    ("flowstate_get_timer_diagnostics", FLOWSTATE_TIMER_DIAGNOSTICS_SCHEMA, _handle_timer_diagnostics),
    ("flowstate_list_task_instances", FLOWSTATE_LIST_TASK_INSTANCES_SCHEMA, _handle_list_task_instances),
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
