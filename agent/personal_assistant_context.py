"""Runtime facts exposed to the office-work personal assistant.

This module describes data and installed capabilities.  It intentionally does
not prescribe a planning workflow: the assistant chooses an appropriate mode
from the user's request and the live FlowState state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any


_CAPABILITY_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "understand what needs attention",
        (
            ("flowstate_get_assistant_context", "read assistant context"),
            ("flowstate_list_tasks", "list tasks"),
            ("flowstate_get_current_timer", "see the current focus session"),
        ),
    ),
    (
        "organize my tasks",
        (
            ("flowstate_create_task", "create tasks"),
            ("flowstate_update_task", "update tasks"),
            ("flowstate_delete_task", "remove tasks"),
        ),
    ),
    (
        "break large tasks into steps",
        (
            ("flowstate_list_subtasks", "read subtasks"),
            ("flowstate_create_subtask", "create subtasks"),
            ("flowstate_update_subtask", "update and reorder subtasks"),
            ("flowstate_delete_subtask", "remove subtasks"),
            ("flowstate_subtask_batch", "apply an approved subtask plan"),
        ),
    ),
    (
        "plan time for my work",
        (
            ("flowstate_list_task_instances", "read scheduled work blocks"),
            ("flowstate_schedule_task_instance", "preview or schedule a work block"),
        ),
    ),
)

_SENSITIVE_KEY_PARTS = ("authorization", "cookie", "password", "secret", "token")


def _safe_context_value(value: Any, *, depth: int = 0) -> Any:
    """Copy JSON-like context while excluding credentials and unbounded data."""

    if depth > 6:
        return None
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:100]:
            key = str(raw_key)
            normalized = re.sub(r"[^a-z]", "", key.lower())
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                continue
            copied = _safe_context_value(item, depth=depth + 1)
            if copied is not None:
                safe[key] = copied
        return safe
    if isinstance(value, (list, tuple)):
        return [
            copied
            for item in list(value)[:100]
            if (copied := _safe_context_value(item, depth=depth + 1)) is not None
        ]
    if isinstance(value, str):
        if re.search(r"\bBearer\s+\S+", value, flags=re.IGNORECASE):
            return None
        return value[:2000]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:2000]


def build_personal_assistant_runtime_context(
    *,
    registered_tool_names: Iterable[str],
    flowstate_available: bool,
    assistant_context_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return safe runtime facts for prompt or transport-layer injection."""

    registered = set(registered_tool_names)
    capabilities = {
        label: [description for tool, description in entries if tool in registered]
        for label, entries in _CAPABILITY_GROUPS
    }
    capabilities = {label: actions for label, actions in capabilities.items() if actions}

    return {
        "source": "FlowState",
        "availability": "available" if flowstate_available else "unavailable",
        "capabilities": capabilities,
        "live_context": (
            _safe_context_value(assistant_context_result)
            if flowstate_available and isinstance(assistant_context_result, Mapping)
            else {}
        ),
    }
