"""Strict Hermes tools for FlowState's protected H7 assistant contracts."""

from __future__ import annotations

import json
import logging
import math
import urllib.parse
from typing import Any, Mapping, Optional

from tools import flowstate_tool as base
from tools.flowstate_receipts import canonical_json_hash
from tools.registry import registry


logger = logging.getLogger(__name__)

_MANIFEST_VERSION = "assistant-capabilities-v1"
_TASK_CONTRACT = "task-v1"
_TIMER_CONTRACT = "timer-v1"
_ORGANIZATION_INVENTORY_CONTRACT = "organization-inventory-v1"
_RECURRENCE_ACTIONS = frozenset({"edit_future", "pause", "resume", "end_series"})
_TIMER_ACTIONS = frozenset({
    "start",
    "pause",
    "resume",
    "stop",
    "switch_task",
    "extend",
})
_ORGANIZATION_ACTIONS = frozenset({"assign_project", "set_canvas_group"})
_APPROVAL_FIELDS = frozenset({"previewDigest", "previewExpiresAt", "requestHash"})
_CAPABILITY_MODES = frozenset({"read", "write"})
_CAPABILITY_APPROVALS = frozenset({"none", "canonical_preview_apply"})
_CAPABILITY_SCOPES = frozenset({"personal_only", "personal_or_active_workspace"})
_READ_CAPABILITIES = frozenset({
    "recurrence.chain",
    "timer.session",
    "organization.inventory",
})


def _result(payload: Any) -> str:
    return base._tool_result(payload)


def _error(message: str) -> str:
    return base._tool_error(message)


def _request_error(exc: Exception, fallback: str) -> str:
    if isinstance(exc, base._FlowStateApiError):
        return base._typed_tool_error(exc)
    return _error(fallback)


def _non_empty(value: Any, *, maximum: int = 256) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and bool(value)
        and len(value) <= maximum
    )


def _uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(base._UUID_RE.fullmatch(value))


def _scope(value: Any) -> bool:
    return value is None or _uuid(value)


def _digest(value: Any) -> bool:
    return isinstance(value, str) and bool(base._SHA256_HEX_RE.fullmatch(value))


def _same_json(left: Any, right: Any) -> bool:
    try:
        return canonical_json_hash(left) == canonical_json_hash(right)
    except (TypeError, ValueError):
        return False


def _hash_matches(value: Any, expected: Any) -> bool:
    try:
        return _digest(expected) and canonical_json_hash(value) == expected
    except (TypeError, ValueError):
        return False


def _same_instant(left: Any, right: Any) -> bool:
    return base._same_iso_instant(left, right)


def _valid_capability(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    mode = value.get("mode")
    approval = value.get("approval")
    receipt = value.get("receiptVersion")
    valid = (
        _non_empty(value.get("id"))
        and mode in _CAPABILITY_MODES
        and approval in _CAPABILITY_APPROVALS
        and value.get("scope") in _CAPABILITY_SCOPES
        and _non_empty(value.get("contractVersion"))
        and (receipt is None or receipt == "canonical-receipt-v1")
        and (mode != "read" or (approval == "none" and receipt is None))
        and (
            mode != "write"
            or (
                approval == "canonical_preview_apply"
                and receipt == "canonical-receipt-v1"
            )
        )
    )
    if not valid:
        return False
    capability_id = value["id"]
    if capability_id in _READ_CAPABILITIES and value["mode"] != "read":
        return False
    if (
        capability_id.startswith(("timer.", "recurrence.", "organization."))
        and capability_id not in _READ_CAPABILITIES
        and value["mode"] != "write"
    ):
        return False
    if capability_id.startswith("timer."):
        return value["contractVersion"] == _TIMER_CONTRACT
    if capability_id == "organization.inventory":
        return value["contractVersion"] == _ORGANIZATION_INVENTORY_CONTRACT
    if capability_id.startswith("organization.") or capability_id.startswith(
        "recurrence."
    ):
        return value["contractVersion"] == _TASK_CONTRACT
    return True


def _valid_capability_manifest(payload: Any) -> bool:
    if not (
        isinstance(payload, dict)
        and payload.get("manifestVersion") == _MANIFEST_VERSION
        and isinstance(payload.get("capabilities"), list)
    ):
        return False
    capabilities = payload["capabilities"]
    ids = [item.get("id") if isinstance(item, dict) else None for item in capabilities]
    return (
        all(_valid_capability(item) for item in capabilities)
        and all(isinstance(item, str) for item in ids)
        and ids == sorted(ids)
        and len(ids) == len(set(ids))
    )


def _read_capability_manifest() -> tuple[Optional[dict], Optional[str]]:
    try:
        payload = base._request("GET", "/api/capabilities", allow_stale_cache=False)
    except base._FlowStateApiError as exc:
        logger.error(
            "flowstate capabilities typed failure: code=%s status=%s",
            exc.code,
            exc.status,
        )
        return None, base._typed_tool_error(exc)
    except Exception as exc:
        logger.error("flowstate capabilities read failed: %s", type(exc).__name__)
        return None, _error("FlowState capability manifest could not be read")
    if not _valid_capability_manifest(payload):
        return None, _error("FlowState capability manifest could not be verified")
    return payload, None


def _require_capability(capability_id: str) -> Optional[str]:
    payload, error = _read_capability_manifest()
    if error:
        return error
    assert payload is not None
    if not any(item["id"] == capability_id for item in payload["capabilities"]):
        return _error(f"FlowState capability {capability_id} is not available")
    return None


def _handle_flowstate_capabilities(args: dict, **kw) -> str:
    if args:
        return _error("flowstate capabilities does not accept parameters")
    payload, error = _read_capability_manifest()
    return error if error else _result(payload)


def _valid_occurrence(value: Any, *, completed: bool) -> bool:
    return (
        isinstance(value, dict)
        and _non_empty(value.get("id"))
        and isinstance(value.get("recurrenceCount"), int)
        and not isinstance(value.get("recurrenceCount"), bool)
        and value["recurrenceCount"] >= 0
        and base._real_date(value.get("dueDate"))
        and base._is_positive_int(value.get("canonicalRevision"))
        and base._is_iso_timestamp(value.get("canonicalUpdatedAt"))
        and (
            not completed
            or (
                value.get("status") == "done"
                and base._is_iso_timestamp(value.get("completedAt"))
            )
        )
    )


def _valid_recurrence_rule(value: Any) -> bool:
    return isinstance(value, dict) and base._is_canonical_recurrence_rule(value)


def _valid_chain(
    payload: Any,
    *,
    requested_id: str,
    workspace_id: Any = ...,
    series_id: Any = ...,
) -> bool:
    if not (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("fresh") is True
        and payload.get("contractVersion") == _TASK_CONTRACT
        and _non_empty(payload.get("seriesId"))
        and payload.get("id") == payload.get("seriesId")
        and _scope(payload.get("workspaceId"))
        and payload.get("lifecycleStatus") in {"active", "paused", "ended"}
        and base._is_positive_int(payload.get("seriesRevision"))
        and payload.get("canonicalRevision") == payload.get("seriesRevision")
        and base._is_iso_timestamp(payload.get("canonicalUpdatedAt"))
        and isinstance(payload.get("history"), list)
        and all(_valid_occurrence(item, completed=True) for item in payload["history"])
    ):
        return False
    if workspace_id is not ... and payload.get("workspaceId") != workspace_id:
        return False
    if series_id is not ... and payload.get("seriesId") != series_id:
        return False
    history = payload["history"]
    if len({item["id"] for item in history}) != len(history):
        return False
    if len({item["recurrenceCount"] for item in history}) != len(history):
        return False
    if len({item["dueDate"] for item in history}) != len(history):
        return False
    if history != sorted(
        history, key=lambda item: (item["recurrenceCount"], item["dueDate"], item["id"])
    ):
        return False
    current = payload.get("currentOccurrence")
    if current is not None and not _valid_occurrence(current, completed=False):
        return False
    if payload["lifecycleStatus"] != "ended" and current is None:
        return False
    if current is not None and (
        current["id"] in {item["id"] for item in history}
        or current["recurrenceCount"] in {item["recurrenceCount"] for item in history}
    ):
        return False
    if current is not None and (
        current.get("canonicalRevision") != payload.get("canonicalRevision")
        or current.get("canonicalUpdatedAt") != payload.get("canonicalUpdatedAt")
    ):
        return False
    member_ids = {payload["seriesId"], *(item["id"] for item in history)}
    if current is not None:
        member_ids.add(current["id"])
    if requested_id not in member_ids:
        return False
    definition = payload.get("definition")
    if definition is not None and not _valid_recurrence_rule(definition):
        return False
    if payload["lifecycleStatus"] == "active" and definition is None:
        return False
    if payload["lifecycleStatus"] == "ended" and definition is not None:
        return False
    following = payload.get("nextOccurrence")
    if payload["lifecycleStatus"] != "active" and following is not None:
        return False
    if following is not None and not (
        isinstance(following, dict)
        and base._real_date(following.get("dueDate"))
        and isinstance(following.get("recurrenceCount"), int)
        and not isinstance(following.get("recurrenceCount"), bool)
        and (
            current is None or following["recurrenceCount"] > current["recurrenceCount"]
        )
    ):
        return False
    return True


def _handle_recurrence_chain(args: dict, **kw) -> str:
    if set(args) != {"taskId"} or not _non_empty(args.get("taskId")):
        return _error("taskId is required and must be an exact trimmed identity")
    capability_error = _require_capability("recurrence.chain")
    if capability_error:
        return capability_error
    task_id = args["taskId"]
    try:
        path = f"/api/tasks/{urllib.parse.quote(task_id, safe='')}/recurrence"
        payload = base._request("GET", path, allow_stale_cache=False)
    except Exception as exc:
        logger.error("flowstate recurrence chain failed: %s", type(exc).__name__)
        return _request_error(exc, "FlowState recurrence chain could not be read")
    return (
        _result(payload)
        if _valid_chain(payload, requested_id=task_id)
        else _error("Canonical recurrence chain could not be verified")
    )


def _approval_body(args: dict, body: dict, *, preview: bool) -> Optional[str]:
    if preview:
        return None
    for field in _APPROVAL_FIELDS:
        value = args.get(field)
        valid = (
            base._is_iso_timestamp(value)
            if field == "previewExpiresAt"
            else _digest(value)
        )
        if not valid:
            return f"{field} is required when preview is false"
        body[field] = value
    return None


def _valid_common_receipt(
    payload: Any,
    *,
    operation_id: str,
    request_hash: str,
    contract_version: str,
    entity_type: str,
    entity_id: str,
    action: str,
    bind_read_back_id: bool = True,
    bind_primary_read_back: bool = True,
) -> Optional[Mapping[str, Any]]:
    if not (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("result") == "committed"
        and payload.get("operationId") == operation_id
        and payload.get("requestHash") == request_hash
        and payload.get("action") == action
        and _digest(request_hash)
        and isinstance(payload.get("receipt"), dict)
    ):
        return None
    receipt = payload["receipt"]
    if not (
        receipt.get("ok") is True
        and receipt.get("status") in {"committed", "replayed"}
        and receipt.get("replayed") is (receipt.get("status") == "replayed")
        and receipt.get("operationId") == operation_id
        and receipt.get("requestHash") == request_hash
        and receipt.get("contractVersion") == contract_version
        and receipt.get("source") == "local-api"
        and receipt.get("entityType") == entity_type
        and receipt.get("entityId") == entity_id
        and receipt.get("action") == action
        and base._is_positive_int(receipt.get("canonicalRevision"))
        and base._is_positive_int(receipt.get("changeSequence"))
        and base._is_iso_timestamp(receipt.get("canonicalUpdatedAt"))
        and base._is_iso_timestamp(receipt.get("committedAt"))
        and isinstance(receipt.get("readBack"), dict)
        and _hash_matches(receipt["readBack"], receipt.get("readBackHash"))
        and isinstance(receipt.get("affected"), list)
        and receipt["affected"]
        and isinstance(receipt.get("operationContext"), dict)
    ):
        return None
    seen: set[tuple[str, str]] = set()
    for entry in receipt["affected"]:
        if not (
            isinstance(entry, dict)
            and _non_empty(entry.get("entityType"))
            and _non_empty(entry.get("entityId"))
            and _non_empty(entry.get("action"))
            and base._is_positive_int(entry.get("canonicalRevision"))
            and base._is_positive_int(entry.get("changeSequence"))
            and isinstance(entry.get("readBack"), dict)
            and base._is_iso_timestamp(entry["readBack"].get("canonicalUpdatedAt"))
            and (
                not bind_read_back_id
                or entry["readBack"].get("id") == entry["entityId"]
            )
            and entry["readBack"].get("canonicalRevision") == entry["canonicalRevision"]
            and _hash_matches(entry["readBack"], entry.get("readBackHash"))
        ):
            return None
        identity = (entry["entityType"], entry["entityId"])
        if identity in seen:
            return None
        seen.add(identity)
    primary = next(
        (
            item
            for item in receipt["affected"]
            if item["entityType"] == entity_type and item["entityId"] == entity_id
        ),
        None,
    )
    if not (
        primary
        and primary["canonicalRevision"] == receipt["canonicalRevision"]
        and primary["changeSequence"] == receipt["changeSequence"]
        and (
            not bind_primary_read_back
            or _same_json(primary["readBack"], receipt["readBack"])
        )
    ):
        return None
    return receipt


def _recurrence_outcome_matches(
    action: str, read_back: Mapping[str, Any], body: Mapping[str, Any]
) -> bool:
    if action == "edit_future":
        return _same_json(read_back.get("definition"), body.get("recurrenceRule")) and (
            body.get("nextDueDate") is None
            or (
                isinstance(read_back.get("currentOccurrence"), dict)
                and read_back["currentOccurrence"].get("dueDate") == body["nextDueDate"]
            )
        )
    if action == "pause":
        return read_back.get("lifecycleStatus") == "paused"
    if action == "resume":
        return read_back.get("lifecycleStatus") == "active"
    return (
        read_back.get("lifecycleStatus") == "ended"
        and isinstance(read_back.get("currentOccurrence"), dict)
        and read_back.get("definition") is None
        and read_back.get("nextOccurrence") is None
    )


def _valid_recurrence_preview(payload: Any, body: Mapping[str, Any]) -> bool:
    normalized = payload.get("normalizedPayload") if isinstance(payload, dict) else None
    workspace_id = payload.get("workspaceId") if isinstance(payload, dict) else ...
    action = body["action"]
    return (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("result") == "preview"
        and payload.get("preview") is True
        and payload.get("contractVersion") == _TASK_CONTRACT
        and payload.get("action") == f"recurrence_{action}"
        and payload.get("operationId") == body["operationId"]
        and isinstance(payload.get("readBack"), dict)
        and payload.get("seriesId") == payload["readBack"].get("seriesId")
        and payload.get("baseRevision") == body["baseRevision"]
        and _scope(workspace_id)
        and _digest(payload.get("requestHash"))
        and _digest(payload.get("previewDigest"))
        and base._is_iso_timestamp(payload.get("previewExpiresAt"))
        and isinstance(normalized, dict)
        and normalized.get("action") == action
        and _same_json(normalized.get("recurrenceRule"), body.get("recurrenceRule"))
        and normalized.get("nextDueDate") == body.get("nextDueDate")
        and _valid_chain(
            payload.get("readBack"),
            requested_id=body["taskId"],
            workspace_id=workspace_id,
            series_id=payload.get("seriesId"),
        )
        and _recurrence_outcome_matches(action, payload["readBack"], body)
    )


def _valid_recurrence_receipt(payload: Any, body: Mapping[str, Any]) -> bool:
    action = f"recurrence_{body['action']}"
    raw_receipt = payload.get("receipt") if isinstance(payload, dict) else None
    read_back = raw_receipt.get("readBack") if isinstance(raw_receipt, dict) else None
    current = (
        read_back.get("currentOccurrence") if isinstance(read_back, dict) else None
    )
    current_id = current.get("id") if isinstance(current, dict) else None
    if not _non_empty(current_id):
        return False
    receipt = _valid_common_receipt(
        payload,
        operation_id=body["operationId"],
        request_hash=body["requestHash"],
        contract_version=_TASK_CONTRACT,
        entity_type="task",
        entity_id=current_id,
        action=action,
        bind_read_back_id=True,
        bind_primary_read_back=False,
    )
    if receipt is None:
        return False
    context = receipt["operationContext"]
    read_back = receipt["readBack"]
    affected = receipt["affected"]
    return (
        len(affected) == 1
        and affected[0].get("entityType") == "task"
        and affected[0].get("entityId") == current_id
        and affected[0].get("action") == "update"
        and context.get("action") == action
        and _non_empty(context.get("seriesId"))
        and context.get("requestedTaskId") == body["taskId"]
        and context.get("currentTaskId") == current_id
        and context.get("timeZone") == body["timeZone"]
        and _same_json(context.get("recurrenceRule"), body.get("recurrenceRule"))
        and context.get("nextDueDate") == body.get("nextDueDate")
        and _valid_chain(
            read_back,
            requested_id=body["taskId"],
            workspace_id=context.get("workspaceId"),
            series_id=context.get("seriesId"),
        )
        and read_back.get("canonicalRevision") == receipt.get("canonicalRevision")
        and read_back.get("canonicalUpdatedAt") == receipt.get("canonicalUpdatedAt")
        and _recurrence_outcome_matches(body["action"], read_back, body)
    )


def _handle_recurrence_command(args: dict, **kw) -> str:
    action = args.get("action")
    if action not in _RECURRENCE_ACTIONS:
        return _error("action must be edit_future|pause|resume|end_series")
    allowed = {
        "operationId",
        "taskId",
        "action",
        "baseRevision",
        "timeZone",
        "preview",
        "recurrenceRule",
        "nextDueDate",
    } | _APPROVAL_FIELDS
    if set(args) - allowed:
        return _error("unsupported recurrence command fields")
    if not _non_empty(args.get("operationId"), maximum=160):
        return _error(
            "operationId is required and must be at most 160 trimmed characters"
        )
    if not _non_empty(args.get("taskId")):
        return _error("taskId is required")
    if not base._is_positive_int(args.get("baseRevision")):
        return _error("baseRevision is required and must be a positive integer")
    if not base._valid_time_zone(args.get("timeZone")):
        return _error("timeZone must be a valid IANA timezone")
    preview = args.get("preview", True)
    if not isinstance(preview, bool):
        return _error("preview must be a boolean")
    rule = args.get("recurrenceRule")
    next_due = args.get("nextDueDate")
    if action == "edit_future":
        if not _valid_recurrence_rule(rule):
            return _error("recurrenceRule is required for edit_future")
        if next_due is not None and not base._real_date(next_due):
            return _error("nextDueDate must be a real YYYY-MM-DD date")
    elif rule is not None or next_due is not None:
        return _error("only edit_future accepts recurrenceRule or nextDueDate")
    capability_error = _require_capability(f"recurrence.{action}")
    if capability_error:
        return capability_error
    body = {
        "operationId": args["operationId"],
        "taskId": args["taskId"],
        "action": action,
        "baseRevision": args["baseRevision"],
        "timeZone": args["timeZone"],
        **({"recurrenceRule": rule} if rule is not None else {}),
        **({"nextDueDate": next_due} if next_due is not None else {}),
        "preview": preview,
    }
    approval_error = _approval_body(args, body, preview=preview)
    if approval_error:
        return _error(approval_error)
    try:
        path = f"/api/tasks/{urllib.parse.quote(args['taskId'], safe='')}/recurrence"
        payload = base._request("POST", path, body, allow_stale_cache=False)
    except Exception as exc:
        logger.error("flowstate recurrence command failed: %s", type(exc).__name__)
        return _request_error(exc, "FlowState recurrence command failed")
    valid = (
        _valid_recurrence_preview(payload, body)
        if preview
        else _valid_recurrence_receipt(payload, body)
    )
    return (
        _result(payload)
        if valid
        else _error(
            "Canonical recurrence preview could not be verified"
            if preview
            else "Canonical recurrence receipt could not be verified"
        )
    )


def _valid_timer_state(value: Any, *, session_id: Optional[str] = None) -> bool:
    return (
        isinstance(value, dict)
        and _uuid(value.get("id"))
        and (session_id is None or value.get("id") == session_id)
        and _scope(value.get("workspaceId"))
        and _non_empty(value.get("taskId"))
        and base._is_iso_timestamp(value.get("startTime"))
        and base._is_positive_int(value.get("duration"))
        and isinstance(value.get("remainingTime"), int)
        and not isinstance(value.get("remainingTime"), bool)
        and 0 <= value["remainingTime"] <= value["duration"]
        and isinstance(value.get("isActive"), bool)
        and isinstance(value.get("isPaused"), bool)
        and isinstance(value.get("isBreak"), bool)
        and (
            value.get("completedAt") is None
            or base._is_iso_timestamp(value.get("completedAt"))
        )
        and _non_empty(value.get("deviceLeaderId"), maximum=160)
        and base._is_positive_int(value.get("canonicalRevision"))
        and base._is_iso_timestamp(value.get("canonicalUpdatedAt"))
    )


def _handle_timer_session(args: dict, **kw) -> str:
    if set(args) != {"sessionId"} or not _uuid(args.get("sessionId")):
        return _error("sessionId is required and must be a UUID")
    capability_error = _require_capability("timer.session")
    if capability_error:
        return capability_error
    session_id = args["sessionId"]
    try:
        payload = base._request(
            "GET",
            f"/api/timer/sessions/{urllib.parse.quote(session_id, safe='')}",
            allow_stale_cache=False,
        )
    except Exception as exc:
        logger.error("flowstate timer session failed: %s", type(exc).__name__)
        return _request_error(exc, "FlowState timer session could not be read")
    valid = (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("fresh") is True
        and _valid_timer_state(payload.get("session"), session_id=session_id)
    )
    return (
        _result(payload)
        if valid
        else _error("Canonical timer session could not be verified")
    )


def _timer_outcome_matches(
    action: str, state: Mapping[str, Any], body: Mapping[str, Any]
) -> bool:
    expected_paused = None if action == "switch_task" else action == "pause"
    if not (
        state.get("isActive") is (action != "stop")
        and (
            isinstance(state.get("isPaused"), bool)
            if expected_paused is None
            else state.get("isPaused") is expected_paused
        )
        and (
            base._is_iso_timestamp(state.get("completedAt"))
            if action == "stop"
            else state.get("completedAt") is None
        )
    ):
        return False
    if action == "start":
        return (
            state.get("taskId") == body.get("taskId")
            and _same_instant(state.get("startTime"), body.get("startedAt"))
            and state.get("duration") == body.get("durationSeconds")
            and state.get("remainingTime") == body.get("durationSeconds")
            and state.get("isBreak") is body.get("isBreak")
        )
    if action == "switch_task":
        return state.get("taskId") == body.get("taskId") and state.get(
            "remainingTime"
        ) == body.get("remainingSeconds")
    if action == "extend":
        return state.get("remainingTime") == body.get("extensionSeconds")
    return state.get("remainingTime") == body.get("remainingSeconds")


def _timer_normalized_matches(normalized: Any, body: Mapping[str, Any]) -> bool:
    expected_keys = {
        "action",
        "sessionId",
        "baseRevision",
        "deviceId",
        "workspaceId",
        "taskId",
        "startedAt",
        "durationSeconds",
        "isBreak",
        "remainingSeconds",
        "extensionSeconds",
    }
    if not (
        isinstance(normalized, dict)
        and set(normalized) == expected_keys
        and normalized.get("action") == body["action"]
        and normalized.get("sessionId") == body["sessionId"]
        and normalized.get("baseRevision") == body["baseRevision"]
        and normalized.get("deviceId") == body["deviceId"]
        and _scope(normalized.get("workspaceId"))
    ):
        return False
    if body["action"] == "start":
        return (
            normalized.get("taskId") == body["taskId"]
            and _same_instant(normalized.get("startedAt"), body["startedAt"])
            and normalized.get("durationSeconds") == body["durationSeconds"]
            and normalized.get("isBreak") is body["isBreak"]
            and normalized.get("remainingSeconds") is None
            and normalized.get("extensionSeconds") is None
        )
    if not (
        normalized.get("startedAt") is None
        and normalized.get("durationSeconds") is None
        and normalized.get("isBreak") is None
    ):
        return False
    if body["action"] == "switch_task":
        return (
            normalized.get("taskId") == body["taskId"]
            and normalized.get("remainingSeconds") == body["remainingSeconds"]
            and normalized.get("extensionSeconds") is None
        )
    if body["action"] == "extend":
        return (
            normalized.get("taskId") is None
            and normalized.get("remainingSeconds") is None
            and normalized.get("extensionSeconds") == body["extensionSeconds"]
        )
    return (
        normalized.get("taskId") is None
        and normalized.get("remainingSeconds") == body["remainingSeconds"]
        and normalized.get("extensionSeconds") is None
    )


def _valid_timer_preview(payload: Any, body: Mapping[str, Any]) -> bool:
    if not (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("result") == "preview"
        and payload.get("contractVersion") == _TIMER_CONTRACT
        and payload.get("action") == body["action"]
        and payload.get("operationId") == body["operationId"]
        and _digest(payload.get("requestHash"))
        and _digest(payload.get("previewDigest"))
        and base._is_iso_timestamp(payload.get("previewExpiresAt"))
        and _timer_normalized_matches(payload.get("normalizedPayload"), body)
        and _valid_timer_state(payload.get("readBack"), session_id=body["sessionId"])
        and _timer_outcome_matches(body["action"], payload["readBack"], body)
        and payload["readBack"].get("workspaceId")
        == payload["normalizedPayload"].get("workspaceId")
        and isinstance(payload.get("replacedSessions"), list)
    ):
        return False
    replacements = payload["replacedSessions"]
    ids = [item.get("id") if isinstance(item, dict) else None for item in replacements]
    return (
        len(ids) == len(set(ids))
        and body["sessionId"] not in ids
        and (body["action"] == "start" or not replacements)
        and all(
            _valid_timer_state(item)
            and item.get("isActive") is False
            and base._is_iso_timestamp(item.get("completedAt"))
            for item in replacements
        )
    )


def _valid_timer_receipt(payload: Any, body: Mapping[str, Any]) -> bool:
    receipt = _valid_common_receipt(
        payload,
        operation_id=body["operationId"],
        request_hash=body["requestHash"],
        contract_version=_TIMER_CONTRACT,
        entity_type="timer_session",
        entity_id=body["sessionId"],
        action=body["action"],
    )
    if receipt is None:
        return False
    state = receipt["readBack"]
    context = receipt["operationContext"]
    if not (
        _valid_timer_state(state, session_id=body["sessionId"])
        and state.get("canonicalRevision") == receipt.get("canonicalRevision")
        and state.get("canonicalUpdatedAt") == receipt.get("canonicalUpdatedAt")
        and state.get("deviceLeaderId") == body["deviceId"]
        and _timer_outcome_matches(body["action"], state, body)
        and isinstance(context.get("replacedSessionIds"), list)
    ):
        return False
    expected_revision = 1 if body["action"] == "start" else body["baseRevision"] + 1
    if state["canonicalRevision"] != expected_revision:
        return False
    replacement_ids = context["replacedSessionIds"]
    if (
        len(replacement_ids) != len(set(replacement_ids))
        or body["sessionId"] in replacement_ids
        or (body["action"] != "start" and replacement_ids)
    ):
        return False
    affected = receipt["affected"]
    if {item["entityId"] for item in affected} != {body["sessionId"], *replacement_ids}:
        return False
    primary = next(item for item in affected if item["entityId"] == body["sessionId"])
    if primary["action"] != ("inserted" if body["action"] == "start" else "updated"):
        return False
    for replacement_id in replacement_ids:
        replacement = next(
            item for item in affected if item["entityId"] == replacement_id
        )
        if not (
            replacement["entityType"] == "timer_session"
            and replacement["action"] == "updated"
            and _valid_timer_state(replacement["readBack"], session_id=replacement_id)
            and replacement["readBack"].get("isActive") is False
            and base._is_iso_timestamp(replacement["readBack"].get("completedAt"))
        ):
            return False
    return True


def _handle_timer_command(args: dict, **kw) -> str:
    action = args.get("action")
    if action not in _TIMER_ACTIONS:
        return _error("action must be start|pause|resume|stop|switch_task|extend")
    allowed = {
        "operationId",
        "action",
        "sessionId",
        "baseRevision",
        "deviceId",
        "taskId",
        "startedAt",
        "durationSeconds",
        "remainingSeconds",
        "extensionSeconds",
        "isBreak",
        "preview",
    } | _APPROVAL_FIELDS
    if set(args) - allowed:
        return _error("unsupported timer command fields")
    if not _non_empty(args.get("operationId"), maximum=160):
        return _error(
            "operationId is required and must be at most 160 trimmed characters"
        )
    if not _uuid(args.get("sessionId")):
        return _error("sessionId is required and must be a UUID")
    if not _non_empty(args.get("deviceId"), maximum=160):
        return _error("deviceId is required")
    preview = args.get("preview", True)
    if not isinstance(preview, bool):
        return _error("preview must be a boolean")
    if action == "start":
        if args.get("baseRevision") != 0 or isinstance(args.get("baseRevision"), bool):
            return _error("baseRevision must be 0 for timer start")
        if not _non_empty(args.get("taskId")):
            return _error("taskId is required for timer start")
        if not base._is_iso_timestamp(args.get("startedAt")):
            return _error("startedAt must be an ISO timestamp with offset")
        if (
            not base._is_positive_int(args.get("durationSeconds"))
            or args["durationSeconds"] > 86400
        ):
            return _error("durationSeconds must be an integer from 1 to 86400")
        if not isinstance(args.get("isBreak"), bool):
            return _error("isBreak is required for timer start")
        if "remainingSeconds" in args or "extensionSeconds" in args:
            return _error("timer start does not accept transition fields")
    elif not base._is_positive_int(args.get("baseRevision")):
        return _error("baseRevision must be a positive integer for timer transition")
    elif action == "switch_task":
        remaining = args.get("remainingSeconds")
        if (
            not _non_empty(args.get("taskId"))
            or not isinstance(remaining, int)
            or isinstance(remaining, bool)
            or remaining < 0
            or any(
                field in args
                for field in {
                    "startedAt",
                    "durationSeconds",
                    "extensionSeconds",
                    "isBreak",
                }
            )
        ):
            return _error("switch_task requires taskId and remainingSeconds only")
    elif action == "extend":
        extension = args.get("extensionSeconds")
        if (
            not base._is_positive_int(extension)
            or extension > 86400
            or any(
                field in args
                for field in {
                    "taskId",
                    "startedAt",
                    "durationSeconds",
                    "remainingSeconds",
                    "isBreak",
                }
            )
        ):
            return _error("extend requires extensionSeconds only")
    else:
        remaining = args.get("remainingSeconds")
        if (
            not isinstance(remaining, int)
            or isinstance(remaining, bool)
            or remaining < 0
            or any(
                field in args
                for field in {
                    "taskId",
                    "startedAt",
                    "durationSeconds",
                    "extensionSeconds",
                    "isBreak",
                }
            )
        ):
            return _error("pause|resume|stop require remainingSeconds only")
    capability_error = _require_capability(f"timer.{action}")
    if capability_error:
        return capability_error
    body = {
        key: value
        for key, value in args.items()
        if key not in _APPROVAL_FIELDS and key != "preview"
    }
    body["preview"] = preview
    approval_error = _approval_body(args, body, preview=preview)
    if approval_error:
        return _error(approval_error)
    try:
        payload = base._request(
            "POST", "/api/timer/command", body, allow_stale_cache=False
        )
    except Exception as exc:
        logger.error("flowstate timer command failed: %s", type(exc).__name__)
        return _request_error(exc, "FlowState canonical timer command failed")
    valid = (
        _valid_timer_preview(payload, body)
        if preview
        else _valid_timer_receipt(payload, body)
    )
    return (
        _result(payload)
        if valid
        else _error(
            "Canonical timer preview could not be verified"
            if preview
            else "Canonical timer receipt could not be verified"
        )
    )


def _valid_organization_inventory(payload: Any) -> bool:
    if not (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("fresh") is True
        and payload.get("contractVersion") == _ORGANIZATION_INVENTORY_CONTRACT
        and payload.get("scopeKind") in {"personal", "workspace"}
        and _scope(payload.get("workspaceId"))
        and isinstance(payload.get("projects"), list)
        and isinstance(payload.get("groups"), list)
        and ((payload["scopeKind"] == "personal") is (payload["workspaceId"] is None))
    ):
        return False
    workspace_id = payload["workspaceId"]
    projects = payload["projects"]
    groups = payload["groups"]
    if len({item.get("id") for item in projects if isinstance(item, dict)}) != len(
        projects
    ):
        return False
    if len({item.get("id") for item in groups if isinstance(item, dict)}) != len(
        groups
    ):
        return False
    return all(
        isinstance(item, dict)
        and _uuid(item.get("id"))
        and _non_empty(item.get("name"))
        and item.get("workspaceId") == workspace_id
        and base._is_iso_timestamp(item.get("updatedAt"))
        for item in projects
    ) and all(
        isinstance(item, dict)
        and _non_empty(item.get("id"))
        and _non_empty(item.get("name"))
        and _non_empty(item.get("type"))
        and item.get("workspaceId") == workspace_id
        and item.get("assignmentMode") in {"plain", "unsupported_smart"}
        and base._is_iso_timestamp(item.get("updatedAt"))
        for item in groups
    )


def _handle_organization_inventory(args: dict, **kw) -> str:
    if args:
        return _error("organization inventory does not accept parameters")
    capability_error = _require_capability("organization.inventory")
    if capability_error:
        return capability_error
    try:
        payload = base._request("GET", "/api/organization", allow_stale_cache=False)
    except Exception as exc:
        logger.error("flowstate organization inventory failed: %s", type(exc).__name__)
        return _request_error(exc, "FlowState organization inventory could not be read")
    return (
        _result(payload)
        if _valid_organization_inventory(payload)
        else _error("Canonical organization inventory could not be verified")
    )


def _organization_target(action: str, args: Mapping[str, Any]) -> tuple[str, Any]:
    return (
        ("projectId", args.get("projectId"))
        if action == "assign_project"
        else ("groupId", args.get("groupId"))
    )


def _organization_placement(action: str, read_back: Any, target_id: str) -> bool:
    if not isinstance(read_back, dict):
        return False
    if action == "assign_project":
        return read_back.get("projectId") == target_id
    position = read_back.get("position")
    return (
        isinstance(position, dict)
        and position.get("parentId") == target_id
        and all(
            isinstance(position.get(axis), (int, float))
            and not isinstance(position.get(axis), bool)
            and math.isfinite(float(position[axis]))
            for axis in ("x", "y")
        )
        and read_back.get("isInInbox") is False
    )


def _valid_organization_preview(payload: Any, body: Mapping[str, Any]) -> bool:
    target_key, target_id = _organization_target(body["action"], body)
    normalized = payload.get("normalizedPayload") if isinstance(payload, dict) else None
    read_back = payload.get("readBack") if isinstance(payload, dict) else None
    return (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("result") == "preview"
        and payload.get("preview") is True
        and payload.get("contractVersion") == _TASK_CONTRACT
        and payload.get("operationId") == body["operationId"]
        and payload.get("action") == body["action"]
        and payload.get("taskId") == body["taskId"]
        and payload.get("baseRevision") == body["baseRevision"]
        and _digest(payload.get("requestHash"))
        and _digest(payload.get("previewDigest"))
        and base._is_iso_timestamp(payload.get("previewExpiresAt"))
        and isinstance(normalized, dict)
        and normalized.get("taskId") == body["taskId"]
        and normalized.get(target_key) == target_id
        and isinstance(read_back, dict)
        and read_back.get("id") == body["taskId"]
        and _scope(read_back.get("workspaceId"))
        and read_back.get("canonicalRevision") == body["baseRevision"]
        and base._is_iso_timestamp(read_back.get("canonicalUpdatedAt"))
        and _organization_placement(body["action"], read_back, target_id)
    )


def _valid_organization_receipt(payload: Any, body: Mapping[str, Any]) -> bool:
    receipt = _valid_common_receipt(
        payload,
        operation_id=body["operationId"],
        request_hash=body["requestHash"],
        contract_version=_TASK_CONTRACT,
        entity_type="task",
        entity_id=body["taskId"],
        action=body["action"],
    )
    if receipt is None:
        return False
    target_key, target_id = _organization_target(body["action"], body)
    context = receipt["operationContext"]
    read_back = receipt["readBack"]
    return (
        context.get("action") == body["action"]
        and context.get("taskId") == body["taskId"]
        and context.get("baseRevision") == body["baseRevision"]
        and context.get(target_key) == target_id
        and _scope(context.get("workspaceId"))
        and read_back.get("workspaceId") == context.get("workspaceId")
        and read_back.get("canonicalRevision") == receipt.get("canonicalRevision")
        and read_back.get("canonicalUpdatedAt") == receipt.get("canonicalUpdatedAt")
        and _organization_placement(body["action"], read_back, target_id)
        and len(receipt["affected"]) == 1
        and receipt["affected"][0].get("action") == "update"
    )


def _handle_organization_command(args: dict, **kw) -> str:
    action = args.get("action")
    if action not in _ORGANIZATION_ACTIONS:
        return _error("action must be assign_project|set_canvas_group")
    target_key, target_id = _organization_target(action, args)
    allowed = {
        "operationId",
        "action",
        "taskId",
        "baseRevision",
        target_key,
        "preview",
    } | _APPROVAL_FIELDS
    if set(args) - allowed:
        return _error("unsupported organization command fields")
    if not _non_empty(args.get("operationId"), maximum=160):
        return _error(
            "operationId is required and must be at most 160 trimmed characters"
        )
    if not _non_empty(args.get("taskId")):
        return _error("taskId is required")
    if not base._is_positive_int(args.get("baseRevision")):
        return _error("baseRevision is required and must be a positive integer")
    if not _non_empty(target_id):
        return _error(f"{target_key} is required")
    preview = args.get("preview", True)
    if not isinstance(preview, bool):
        return _error("preview must be a boolean")
    capability_error = _require_capability(f"organization.{action}")
    if capability_error:
        return capability_error
    body = {
        "operationId": args["operationId"],
        "baseRevision": args["baseRevision"],
        target_key: target_id,
        "preview": preview,
    }
    approval_error = _approval_body(args, body, preview=preview)
    if approval_error:
        return _error(approval_error)
    route_action = (
        "assign-project" if action == "assign_project" else "set-canvas-group"
    )
    try:
        path = f"/api/tasks/{urllib.parse.quote(args['taskId'], safe='')}/organization/{route_action}"
        payload = base._request("POST", path, body, allow_stale_cache=False)
    except Exception as exc:
        logger.error("flowstate organization command failed: %s", type(exc).__name__)
        return _request_error(exc, "FlowState canonical organization command failed")
    validation_body = {**body, "action": action, "taskId": args["taskId"]}
    valid = (
        _valid_organization_preview(payload, validation_body)
        if preview
        else _valid_organization_receipt(payload, validation_body)
    )
    return (
        _result(payload)
        if valid
        else _error(
            "Canonical organization preview could not be verified"
            if preview
            else "Canonical organization receipt could not be verified"
        )
    )


_APPROVAL_PROPERTIES = {
    "preview": {
        "type": "boolean",
        "description": "Defaults true; false requires the exact approved proof.",
    },
    "previewDigest": {"type": "string"},
    "previewExpiresAt": {"type": "string"},
    "requestHash": {"type": "string"},
}

FLOWSTATE_CAPABILITIES_SCHEMA = {
    "name": "flowstate_get_capabilities",
    "description": "Read and verify FlowState's versioned assistant capability manifest.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

FLOWSTATE_RECURRENCE_CHAIN_SCHEMA = {
    "name": "flowstate_get_recurrence_chain",
    "description": "Read one exact canonical recurrence chain with full current and history evidence.",
    "parameters": {
        "type": "object",
        "properties": {"taskId": {"type": "string"}},
        "required": ["taskId"],
    },
}

FLOWSTATE_RECURRENCE_COMMAND_SCHEMA = {
    "name": "flowstate_recurrence_command",
    "description": "Preview or apply one exact recurrence edit, pause, resume, or end-series command; never patches around recurrence semantics.",
    "parameters": {
        "type": "object",
        "properties": {
            "operationId": {"type": "string"},
            "taskId": {"type": "string"},
            "action": {
                "type": "string",
                "enum": ["edit_future", "pause", "resume", "end_series"],
            },
            "baseRevision": {"type": "integer", "minimum": 1},
            "timeZone": {"type": "string"},
            "recurrenceRule": {"type": "object"},
            "nextDueDate": {"type": "string"},
            **_APPROVAL_PROPERTIES,
        },
        "required": ["operationId", "taskId", "action", "baseRevision", "timeZone"],
    },
}

FLOWSTATE_TIMER_SESSION_SCHEMA = {
    "name": "flowstate_get_timer_session",
    "description": "Read one exact canonical timer session by UUID.",
    "parameters": {
        "type": "object",
        "properties": {"sessionId": {"type": "string"}},
        "required": ["sessionId"],
    },
}

FLOWSTATE_TIMER_COMMAND_SCHEMA = {
    "name": "flowstate_timer_command",
    "description": "Preview or apply an explicit timer start, pause, resume, stop, task switch, or completed-session extension; no toggle or direct-write fallback.",
    "parameters": {
        "type": "object",
        "properties": {
            "operationId": {"type": "string"},
            "action": {
                "type": "string",
                "enum": [
                    "start",
                    "pause",
                    "resume",
                    "stop",
                    "switch_task",
                    "extend",
                ],
            },
            "sessionId": {"type": "string"},
            "baseRevision": {"type": "integer", "minimum": 0},
            "deviceId": {"type": "string"},
            "taskId": {"type": "string"},
            "startedAt": {"type": "string"},
            "durationSeconds": {"type": "integer", "minimum": 1, "maximum": 86400},
            "remainingSeconds": {"type": "integer", "minimum": 0},
            "extensionSeconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 86400,
            },
            "isBreak": {"type": "boolean"},
            **_APPROVAL_PROPERTIES,
        },
        "required": ["operationId", "action", "sessionId", "baseRevision", "deviceId"],
    },
}

FLOWSTATE_ORGANIZATION_INVENTORY_SCHEMA = {
    "name": "flowstate_get_organization",
    "description": "Read exact project and Canvas-group identities in the active FlowState scope.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

FLOWSTATE_ORGANIZATION_COMMAND_SCHEMA = {
    "name": "flowstate_organization_command",
    "description": "Preview or apply exact task assignment to a project or plain Canvas group by ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "operationId": {"type": "string"},
            "action": {
                "type": "string",
                "enum": ["assign_project", "set_canvas_group"],
            },
            "taskId": {"type": "string"},
            "baseRevision": {"type": "integer", "minimum": 1},
            "projectId": {"type": "string"},
            "groupId": {"type": "string"},
            **_APPROVAL_PROPERTIES,
        },
        "required": ["operationId", "action", "taskId", "baseRevision"],
    },
}


for _name, _schema, _handler in [
    (
        "flowstate_get_capabilities",
        FLOWSTATE_CAPABILITIES_SCHEMA,
        _handle_flowstate_capabilities,
    ),
    (
        "flowstate_get_recurrence_chain",
        FLOWSTATE_RECURRENCE_CHAIN_SCHEMA,
        _handle_recurrence_chain,
    ),
    (
        "flowstate_recurrence_command",
        FLOWSTATE_RECURRENCE_COMMAND_SCHEMA,
        _handle_recurrence_command,
    ),
    (
        "flowstate_get_timer_session",
        FLOWSTATE_TIMER_SESSION_SCHEMA,
        _handle_timer_session,
    ),
    ("flowstate_timer_command", FLOWSTATE_TIMER_COMMAND_SCHEMA, _handle_timer_command),
    (
        "flowstate_get_organization",
        FLOWSTATE_ORGANIZATION_INVENTORY_SCHEMA,
        _handle_organization_inventory,
    ),
    (
        "flowstate_organization_command",
        FLOWSTATE_ORGANIZATION_COMMAND_SCHEMA,
        _handle_organization_command,
    ),
]:
    registry.register(
        name=_name,
        toolset="flowstate",
        schema=_schema,
        handler=_handler,
        check_fn=base._check_flowstate_available,
        emoji="📋",
    )
