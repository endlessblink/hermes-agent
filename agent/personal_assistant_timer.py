"""Deterministic, approval-gated Personal Assistant timer actions."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Mapping
import uuid

from agent.personal_assistant_intent import resolve_turn_intention
from tools.registry import registry


def _payload(raw: Any) -> dict[str, Any] | None:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    result = value.get("result")
    if isinstance(result, dict) and not any(
        key in value for key in ("items", "task", "active", "ok", "error")
    ):
        return result
    return value


def _error(payload: Mapping[str, Any] | None) -> bool:
    return not isinstance(payload, Mapping) or bool(payload.get("error")) or payload.get("ok") is False


def _task_items(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items")
    return [dict(item) for item in items or [] if isinstance(item, Mapping)]


def _session_value(session: Mapping[str, Any], camel: str, snake: str) -> Any:
    value = session.get(camel)
    return session.get(snake) if value is None else value


def _duration_minutes(task: Mapping[str, Any]) -> int:
    instances = task.get("instances")
    if isinstance(instances, list):
        due_date = str(task.get("dueDate") or "").strip()
        ordered_instances = sorted(
            (item for item in instances if isinstance(item, Mapping)),
            key=lambda item: (
                str(item.get("scheduledDate") or "").strip() != due_date,
                str(item.get("scheduledDate") or ""),
            ),
        )
    else:
        ordered_instances = []
    for container in (
        task,
        task.get("currentInstance"),
        task.get("nextInstance"),
        task.get("instance"),
        *ordered_instances,
    ):
        if not isinstance(container, Mapping):
            continue
        for key in ("estimatedDuration", "durationMinutes"):
            value = container.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 480:
                return value
        value = container.get("duration")
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 480:
            return value
    return 25


def _artifact(*, title: str, description: str, label: str, submit_text: str) -> str:
    artifact = {
        "type": "task-table",
        "direction": "rtl",
        "title": title,
        "description": description,
        "columns": ["task"],
        "rows": [
            {
                "id": "timer-confirmation",
                "title": title,
                "cells": {},
                "actions": [
                    {
                        "id": "confirm-timer-action",
                        "label": label,
                        "submitText": submit_text,
                    }
                ],
            }
        ],
    }
    return "```hermes-ui\n" + json.dumps(
        artifact, ensure_ascii=False, separators=(",", ":")
    ) + "\n```"


def _expired(timestamp: Any) -> bool:
    try:
        expires_at = datetime.fromisoformat(str(timestamp))
        now = datetime.now(expires_at.tzinfo)
    except (TypeError, ValueError):
        return True
    return expires_at <= now


def _commit_pending(agent: Any, pending: Mapping[str, Any]) -> str:
    store = agent.personal_assistant_state_store
    title = str(pending["taskTitle"])
    kind = str(pending["kind"])
    if _expired(pending.get("previewExpiresAt")):
        store.set_pending_timer_action(None)
        return "האישור פג לפני שבוצע שינוי. לא שיניתי את הטיימר; נסה שוב."

    args = {
        "sessionId": pending["sessionId"],
        "operationId": pending["operationId"],
        "preview": False,
        "previewDigest": pending["previewDigest"],
        "requestHash": pending["requestHash"],
        "previewExpiresAt": pending["previewExpiresAt"],
    }
    tool = "flowstate_start_timer" if kind == "start" else "flowstate_stop_timer"
    if kind == "start":
        args.update(
            {
                "taskId": pending["taskId"],
                "durationMinutes": pending["durationMinutes"],
            }
        )
    else:
        args["baseRevision"] = pending["baseRevision"]

    committed = _payload(registry.dispatch(tool, args))
    if _error(committed) or committed.get("result") != "committed":
        return (
            "FlowState לא אישר את השינוי, ולכן לא הצגתי הצלחה. "
            "הטיימר נשאר כפי שהיה; נסה שוב."
        )

    current = _payload(registry.dispatch("flowstate_get_current_timer", {}))
    if _error(current):
        return (
            "FlowState ביצע את השינוי, אבל לא הצלחתי לאמת את מצב הטיימר. "
            "בדוק את הטיימר לפני פעולה נוספת."
        )
    session = current.get("session")
    if kind == "start":
        verified = (
            current.get("active") is True
            and isinstance(session, Mapping)
            and str(_session_value(session, "taskId", "task_id") or "")
            == str(pending["taskId"])
        )
    else:
        verified = current.get("active") is False and session is None
    if not verified:
        return (
            "מצב הטיימר אחרי האישור אינו תואם לפעולה שביקשת. "
            "לא הצגתי הצלחה; בדוק את FlowState."
        )

    store.set_pending_timer_action(None)
    if kind == "start":
        duration = int(pending["durationMinutes"])
        return f"התחלתי טיימר של {duration} דקות למשימה „{title}”. הטיימר פעיל עכשיו."
    return f"הטיימר של „{title}” נעצר. אין כרגע טיימר פעיל."


def _preview_start(agent: Any, query: str) -> str:
    inventory = _payload(registry.dispatch("flowstate_list_tasks", {"limit": 100}))
    if _error(inventory) or inventory.get("complete") is not True:
        return "לא הצלחתי לבדוק את כל משימות FlowState, ולכן לא התחלתי טיימר."
    normalized = query.strip().casefold()
    matches = [
        task
        for task in _task_items(inventory)
        if str(task.get("title") or "").strip().casefold() == normalized
    ]
    if len(matches) != 1:
        return (
            "לא מצאתי משימה פתוחה אחת בשם הזה. "
            "כתוב את שם המשימה בדיוק כפי שהוא מופיע ב-FlowState."
        )
    task_id = str(matches[0].get("id") or matches[0].get("taskId") or "")
    title = str(matches[0].get("title") or "").strip()
    current = _payload(registry.dispatch("flowstate_get_current_timer", {}))
    if _error(current):
        return "לא הצלחתי לבדוק את הטיימר הנוכחי, ולכן לא התחלתי טיימר נוסף."
    if current.get("active") is True:
        return "כבר פועל טיימר. עצור אותו לפני התחלת משימה אחרת."
    exact = _payload(registry.dispatch("flowstate_get_task", {"taskId": task_id}))
    if _error(exact):
        return f"לא הצלחתי לאמת את המשימה „{title}”, ולכן לא התחלתי טיימר."
    task = exact.get("task") if isinstance(exact.get("task"), Mapping) else exact
    duration = _duration_minutes(task)
    session_id = str(uuid.uuid4())
    operation_id = f"personal-assistant-start-{uuid.uuid4()}"
    preview = _payload(
        registry.dispatch(
            "flowstate_start_timer",
            {
                "taskId": task_id,
                "sessionId": session_id,
                "operationId": operation_id,
                "durationMinutes": duration,
                "preview": True,
            },
        )
    )
    if _error(preview) or preview.get("result") != "preview":
        return f"FlowState לא הצליח להכין התחלה בטוחה עבור „{title}”. לא שיניתי דבר."
    confirm_text = f"אשר להתחיל טיימר של {duration} דקות למשימה {title}."
    pending = {
        "kind": "start",
        "taskId": task_id,
        "taskTitle": title,
        "durationMinutes": duration,
        "sessionId": session_id,
        "operationId": operation_id,
        "previewDigest": preview.get("previewDigest"),
        "requestHash": preview.get("requestHash"),
        "previewExpiresAt": preview.get("previewExpiresAt"),
        "confirmText": confirm_text,
    }
    agent.personal_assistant_state_store.set_pending_timer_action(pending)
    return _artifact(
        title=title,
        description=f"FlowState הכין התחלה של {duration} דקות. הטיימר עוד לא התחיל.",
        label=f"להתחיל {duration} דקות",
        submit_text=confirm_text,
    )


def _preview_stop(agent: Any) -> str:
    current = _payload(registry.dispatch("flowstate_get_current_timer", {}))
    if _error(current):
        return "לא הצלחתי לבדוק את הטיימר הנוכחי, ולכן לא עצרתי דבר."
    session = current.get("session")
    if current.get("active") is not True or not isinstance(session, Mapping):
        return "אין כרגע טיימר פעיל."
    task_id = str(_session_value(session, "taskId", "task_id") or "")
    exact = _payload(registry.dispatch("flowstate_get_task", {"taskId": task_id}))
    if _error(exact):
        return "לא הצלחתי לאמת לאיזו משימה שייך הטיימר, ולכן לא עצרתי אותו."
    task = exact.get("task") if isinstance(exact.get("task"), Mapping) else exact
    title = str(task.get("title") or "").strip()
    operation_id = f"personal-assistant-stop-{uuid.uuid4()}"
    preview = _payload(
        registry.dispatch(
            "flowstate_stop_timer",
            {
                "sessionId": session.get("id"),
                "operationId": operation_id,
                "baseRevision": _session_value(
                    session, "canonicalRevision", "canonical_revision"
                ),
                "preview": True,
            },
        )
    )
    if _error(preview) or preview.get("result") != "preview":
        return f"FlowState לא הצליח להכין עצירה בטוחה עבור „{title}”. לא שיניתי דבר."
    confirm_text = f"אשר לעצור את הטיימר של המשימה {title}."
    pending = {
        "kind": "stop",
        "taskId": task_id,
        "taskTitle": title,
        "sessionId": session.get("id"),
        "baseRevision": _session_value(
            session, "canonicalRevision", "canonical_revision"
        ),
        "operationId": operation_id,
        "previewDigest": preview.get("previewDigest"),
        "requestHash": preview.get("requestHash"),
        "previewExpiresAt": preview.get("previewExpiresAt"),
        "confirmText": confirm_text,
    }
    agent.personal_assistant_state_store.set_pending_timer_action(pending)
    return _artifact(
        title=title,
        description="FlowState הכין עצירה בטוחה. הטיימר עדיין פעיל.",
        label="לעצור את הטיימר",
        submit_text=confirm_text,
    )


def build_deterministic_timer_response(agent: Any, user_message: Any) -> str | None:
    if (
        not bool(getattr(agent, "personal_assistant_mode", False))
        or not isinstance(user_message, str)
        or not hasattr(getattr(agent, "personal_assistant_state_store", None), "get_pending_timer_action")
    ):
        return None
    store = agent.personal_assistant_state_store
    pending = store.get_pending_timer_action()
    text = user_message.strip()
    if isinstance(pending, Mapping):
        if text == str(pending.get("confirmText") or ""):
            return _commit_pending(agent, pending)
        if text.casefold() in {"לא", "בטל", "תבטל", "cancel", "no"}:
            store.set_pending_timer_action(None)
            return "ביטלתי. הטיימר לא השתנה."

    intent = resolve_turn_intention(text)
    if intent.action == "task.timer.start.lookup":
        return _preview_start(agent, str(intent.metadata.get("taskQuery") or ""))
    if intent.action == "task.timer.stop":
        return _preview_stop(agent)
    return None


__all__ = ["build_deterministic_timer_response"]
