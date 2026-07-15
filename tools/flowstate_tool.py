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

from tools.flowstate_receipts import CanonicalReceiptError, validate_canonical_receipt

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
    ("flowstate_get_task", FLOWSTATE_GET_TASK_SCHEMA, _handle_get_task),
    ("flowstate_create_task", FLOWSTATE_CREATE_TASK_SCHEMA, _handle_create_task),
    ("flowstate_update_task", FLOWSTATE_UPDATE_TASK_SCHEMA, _handle_update_task),
    ("flowstate_complete_task", FLOWSTATE_COMPLETE_TASK_SCHEMA, _handle_complete_task),
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
