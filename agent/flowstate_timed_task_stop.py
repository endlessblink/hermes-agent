"""Bounded turn-end guard for approved exact-time FlowState task creation."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any


_REQUIRED_TOOLS = frozenset(
    {"flowstate_create_task", "flowstate_create_work_block"}
)
_EXACT_TIME_RE = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)")


def _tool_name(message: dict[str, Any]) -> str:
    name = message.get("name") or message.get("tool_name")
    return str(name or "")


def _payload(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if isinstance(content, dict):
        value = content
    elif isinstance(content, str):
        try:
            value = json.loads(content)
        except (TypeError, ValueError):
            return {}
    else:
        return {}
    result = value.get("result")
    return result if isinstance(result, dict) else value


def _is_success(message: dict[str, Any]) -> bool:
    payload = _payload(message)
    return payload.get("ok") is True or isinstance(payload.get("task"), dict)


def _is_committed(message: dict[str, Any]) -> bool:
    payload = _payload(message)
    receipt = payload.get("receipt")
    committed_statuses = {"committed", "replayed"}
    return payload.get("status") in committed_statuses or (
        isinstance(receipt, dict) and receipt.get("status") in committed_statuses
    )


def _tool_results(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        message
        for message in messages
        if isinstance(message, dict) and message.get("role") == "tool"
    ]


def _current_turn_messages(
    messages: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    items = [message for message in messages if isinstance(message, dict)]
    start = 0
    for index, message in enumerate(items):
        if message.get("role") == "user" and not message.get(
            "_flowstate_timed_task_synthetic"
        ):
            start = index
    return items[start:]


def _matching_instance(instance: Any, expected: dict[str, Any]) -> bool:
    if not isinstance(instance, dict):
        return False
    for key in (
        "id",
        "taskId",
        "scheduledDate",
        "scheduledTime",
        "duration",
        "timezone",
    ):
        value = expected.get(key)
        if value is not None and instance.get(key) != value:
            return False
    return bool(expected.get("id") and expected.get("taskId"))


def _committed_work_block(payload: dict[str, Any]) -> dict[str, Any]:
    receipt = payload.get("receipt")
    if not isinstance(receipt, dict):
        return {}
    read_back = receipt.get("readBack")
    if not isinstance(read_back, dict):
        read_back = {}
    work_block = read_back.get("workBlock")
    if not isinstance(work_block, dict):
        work_block = {}
    expected = dict(work_block)
    expected.setdefault("id", receipt.get("workBlockId"))
    expected.setdefault("taskId", receipt.get("entityId") or read_back.get("id"))
    return expected


def _created_task_id(results: list[dict[str, Any]]) -> str | None:
    for message in reversed(results):
        if _tool_name(message) != "flowstate_create_task" or not _is_committed(
            message
        ):
            continue
        payload = _payload(message)
        receipt = payload.get("receipt")
        if isinstance(receipt, dict):
            entity_id = receipt.get("entityId")
            if isinstance(entity_id, str) and entity_id:
                return entity_id
            read_back = receipt.get("readBack")
            if isinstance(read_back, dict):
                entity_id = read_back.get("id")
                if isinstance(entity_id, str) and entity_id:
                    return entity_id
        task = payload.get("task")
        if isinstance(task, dict):
            entity_id = task.get("id")
            if isinstance(entity_id, str) and entity_id:
                return entity_id
    return None


def _timed_task_sequence_complete(
    results: list[dict[str, Any]], created_task_id: str
) -> bool:
    committed_index = next(
        (
            index
            for index, message in enumerate(results)
            if _tool_name(message) == "flowstate_create_work_block"
            and _is_committed(message)
        ),
        None,
    )
    if committed_index is None:
        return False
    previewed = any(
        _tool_name(message) == "flowstate_create_work_block"
        and _payload(message).get("status") == "preview"
        for message in results[:committed_index]
    )
    if not previewed:
        return False
    expected = _committed_work_block(_payload(results[committed_index]))
    if (
        not expected.get("id")
        or expected.get("taskId") != created_task_id
    ):
        return False
    later = results[committed_index + 1 :]
    task_confirmed = any(
        _tool_name(message) == "flowstate_get_task"
        and _is_success(message)
        and isinstance(_payload(message).get("task"), dict)
        and _payload(message)["task"].get("id") == expected["taskId"]
        and any(
            _matching_instance(instance, expected)
            for instance in _payload(message)["task"].get("instances", [])
        )
        for message in later
    )
    instances_confirmed = any(
        _tool_name(message) == "flowstate_list_task_instances"
        and _is_success(message)
        and (
            not isinstance(_payload(message).get("task"), dict)
            or _payload(message)["task"].get("id") == expected["taskId"]
        )
        and any(
            _matching_instance(instance, expected)
            for instance in _payload(message).get("instances", [])
        )
        for message in later
    )
    return task_confirmed and instances_confirmed


def build_flowstate_timed_task_stop_nudge(
    *,
    user_message: str,
    messages: Iterable[dict[str, Any]] | None,
    valid_tool_names: Iterable[str] | None,
    attempts: int = 0,
    max_attempts: int = 6,
) -> str | None:
    """Keep an exact-time task turn alive until its work block is verified."""
    if not _REQUIRED_TOOLS.issubset(set(valid_tool_names or ())):
        return None
    if not _EXACT_TIME_RE.search(str(user_message or "")):
        return None

    results = _tool_results(_current_turn_messages(messages or ()))
    created_task_id = _created_task_id(results)
    if not created_task_id or _timed_task_sequence_complete(
        results, created_task_id
    ):
        return None
    if attempts >= max_attempts:
        return None

    return (
        "[System: The approved exact-time FlowState task workflow is incomplete. "
        "Do not send a final answer yet. Continue with `flowstate_create_work_block`: "
        "preview the requested block, apply the exact preview, then call "
        "`flowstate_get_task` and `flowstate_list_task_instances`. Only report success "
        "after both read-backs confirm the scheduled work block.]"
    )


__all__ = ["build_flowstate_timed_task_stop_nudge"]
