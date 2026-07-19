"""Persistent native Telegram interactions for ``hermes-ui`` artifacts."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils import atomic_replace

_SCHEMA_VERSION = 1
_TTL_SECONDS = 7 * 24 * 60 * 60
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CLEANUP_INTERVAL_SECONDS = 60 * 60
_MAX_STATE_FILES = 2_000
_last_cleanup: dict[str, float] = {}


@dataclass(frozen=True)
class InteractionResult:
    outcome: str
    state: dict[str, Any]
    payload: str = ""
    prompt: str = ""
    error: str = ""


def _default_root() -> Path:
    override = os.environ.get("HERMES_TELEGRAM_UI_STATE_DIR")
    if override:
        return Path(override)
    from hermes_constants import get_default_hermes_root

    return Path(get_default_hermes_root()) / "state" / "telegram-hermes-ui"


def _root(root: Path | str | None) -> Path:
    path = Path(root) if root is not None else _default_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cleanup_stale_files(path: Path) -> None:
    """Bound the persistent callback store without delaying normal clicks."""
    now = time.time()
    key = str(path.resolve())
    if now - _last_cleanup.get(key, 0) < _CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup[key] = now
    candidates = sorted(
        (item for item in path.iterdir() if item.is_file()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    state_files = [item for item in candidates if item.suffix == ".json"]
    keep = {item.name for item in state_files[:_MAX_STATE_FILES]}
    cutoff = now - _TTL_SECONDS
    for item in candidates:
        if item.stat().st_mtime >= cutoff and (item.suffix != ".json" or item.name in keep):
            continue
        try:
            item.unlink()
        except FileNotFoundError:
            pass


def _state_path(token: str, root: Path | str | None) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,32}", token):
        raise ValueError("invalid interaction token")
    return _root(root) / f"{token}.json"


def _write(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(3)}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, 0o600)
    atomic_replace(tmp, path)


def _locked(token: str, root: Path | str | None):
    path = _state_path(token, root)
    lock_path = path.with_suffix(".lock")
    handle = open(lock_path, "a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle, path


def load_interaction(token: str, *, root: Path | str | None = None) -> dict[str, Any]:
    path = _state_path(token, root)
    with open(path, encoding="utf-8") as handle:
        state = json.load(handle)
    if float(state.get("expires_at", 0)) < time.time() and state.get("status") not in {"submitted", "cancelled"}:
        state["status"] = "expired"
        state["controls"] = []
        _write(path, state)
    return state


def _option(option: object) -> tuple[str, str]:
    if isinstance(option, dict):
        label = str(option.get("label") or option.get("value") or "").strip()
        value = str(option.get("value") if option.get("value") is not None else label)
        return label, value
    value = str(option or "").strip()
    return value, value


def _callback(token: str, revision: int, index: int) -> str:
    return f"hu:{token}:{revision:x}:{index:x}"


def parse_callback(data: str) -> tuple[str, int, int] | None:
    match = re.fullmatch(r"hu:([A-Za-z0-9_-]{8,32}):([0-9a-f]+):([0-9a-f]+)", str(data or ""))
    if not match:
        return None
    return match.group(1), int(match.group(2), 16), int(match.group(3), 16)


def _append_control(state: dict[str, Any], text: str, kind: str, **payload: Any) -> None:
    index = len(state["controls"])
    state["controls"].append(
        {
            "text": str(text)[:60],
            "kind": kind,
            "callback_data": _callback(state["token"], int(state["revision"]), index),
            **payload,
        }
    )


def _collect_actions(value: object, output: list[dict[str, str]]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_actions(item, output)
        return
    if not isinstance(value, dict):
        return
    actions = value.get("actions")
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, dict):
                continue
            label = str(action.get("label") or "").strip()
            submit_text = str(action.get("submitText") or "").strip()
            copy_text = str(action.get("copyText") or "").strip()
            if label and (submit_text or copy_text):
                output.append({"label": label, "submitText": submit_text, "copyText": copy_text})
    for key, child in value.items():
        if key != "actions" and isinstance(child, (dict, list)):
            _collect_actions(child, output)


def _form_fields(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [field for field in state["artifact"].get("fields", []) if isinstance(field, dict)]


def _refresh_controls(state: dict[str, Any]) -> None:
    state["controls"] = []
    if state.get("status") == "awaiting_text":
        _append_control(state, "✕ Cancel", "cancel")
        return
    artifact = state["artifact"]
    artifact_type = artifact.get("type")
    if artifact_type == "form":
        fields = _form_fields(state)
        if not fields:
            _append_control(state, "Close", "cancel")
            return
        field_index = min(int(state.get("current_field", 0)), len(fields) - 1)
        field = fields[field_index]
        field_type = field.get("type")
        options = field.get("options") if isinstance(field.get("options"), list) else []
        if field_type in {"single-choice", "multi-choice"}:
            selected = state["values"].get(field.get("id"), [] if field_type == "multi-choice" else "")
            for option_index, raw_option in enumerate(options):
                label, value = _option(raw_option)
                checked = value in selected if isinstance(selected, list) else selected == value
                icon = "✅" if checked else ("☐" if field_type == "multi-choice" else "○")
                _append_control(
                    state,
                    f"{icon} {label}",
                    "toggle-option" if field_type == "multi-choice" else "select-option",
                    field_index=field_index,
                    option_index=option_index,
                )
            if field.get("allowCustomAnswer") is True:
                field_id = str(field.get("id"))
                custom = str(state.get("custom_answers", {}).get(field_id) or "")
                custom_label = str(field.get("customAnswerLabel") or "Other answer")
                text = f"✍️ {custom_label}" if not custom else f"✍️ {custom_label}: {custom}"
                _append_control(
                    state,
                    text,
                    "request-custom-answer",
                    field_index=field_index,
                )
            if field_type == "multi-choice":
                _append_control(state, "המשך / Continue", "submit-form" if field_index == len(fields) - 1 else "next-field")
            elif selected not in (None, ""):
                _append_control(state, "שליחה / Submit" if field_index == len(fields) - 1 else "המשך / Continue", "submit-form" if field_index == len(fields) - 1 else "next-field")
        elif field_type == "boolean":
            current = state["values"].get(field.get("id"))
            _append_control(state, f"{'✅' if current is True else '○'} כן / Yes", "select-boolean", field_index=field_index, value=True)
            _append_control(state, f"{'✅' if current is False else '○'} לא / No", "select-boolean", field_index=field_index, value=False)
            if isinstance(current, bool):
                _append_control(state, "שליחה / Submit" if field_index == len(fields) - 1 else "המשך / Continue", "submit-form" if field_index == len(fields) - 1 else "next-field")
        else:
            label = str(field.get("label") or "Answer")
            current = state["values"].get(str(field.get("id")))
            _append_control(state, f"✍️ {label}" if current in (None, "") else f"✍️ {label}: {current}", "request-text", field_index=field_index)
            if current not in (None, ""):
                _append_control(state, "שליחה / Submit" if field_index == len(fields) - 1 else "המשך / Continue", "submit-form" if field_index == len(fields) - 1 else "next-field")
            if not field.get("required"):
                _append_control(state, "דלג / Skip", "skip-field", field_index=field_index)
        if field_index > 0:
            _append_control(state, "⬅️ Back", "previous-field")
        _append_control(state, "✕ Cancel", "cancel")
        return

    if artifact_type in {"checklist", "questionnaire"}:
        items = artifact.get("items")
        if not isinstance(items, list):
            items = artifact.get("questions") if isinstance(artifact.get("questions"), list) else []
        selected = set(state.get("selected_items", []))
        page = max(0, int(state.get("page", 0)))
        page_size = 8
        visible = items[page * page_size:(page + 1) * page_size]
        for offset, item in enumerate(visible):
            if not isinstance(item, dict):
                continue
            item_index = page * page_size + offset
            item_id = str(item.get("id") or item.get("name") or item_index)
            label = str(item.get("label") or item.get("question") or item.get("prompt") or item_id)
            _append_control(state, f"{'✅' if item_id in selected else '☐'} {label}", "toggle-item", item_index=item_index)
        if page > 0:
            _append_control(state, "⬅️ Previous", "page", page=page - 1)
        if (page + 1) * page_size < len(items):
            _append_control(state, "Next ➡️", "page", page=page + 1)
        _append_control(state, "☑ Mark all", "select-all-items")
        _append_control(state, "☐ Clear", "clear-items")
        _append_control(state, "✅ Save", "submit-checklist")

    actions: list[dict[str, str]] = []
    _collect_actions(artifact, actions)
    for action_index, action in enumerate(actions):
        _append_control(state, action["label"], "submit-action", action_index=action_index)
    state["actions"] = actions

    if artifact_type in {"flowstate-task-batch", "flowstate-planning-session"} and not actions:
        tasks = [task for task in artifact.get("tasks", []) if isinstance(task, dict)]
        decisions = state.setdefault("task_decisions", {})
        for task_index, task in enumerate(tasks):
            task_id = str(task.get("id") or task_index)
            decision = str(decisions.get(task_id) or task.get("recommendation") or "unset")
            icon = {"today": "☀️", "not_today": "⏸", "later": "🕒", "discuss": "💬"}.get(decision, "○")
            _append_control(state, f"{icon} {task.get('title') or task_id}", "cycle-task", task_index=task_index)
        _append_control(state, "✅ Submit decisions", "submit-task-batch")
    elif artifact_type == "task-triage" and not actions:
        for value, label in (("today", "Today"), ("not_today", "Not today"), ("later", "Later"), ("discuss", "Discuss")):
            _append_control(state, label, "triage", value=value)
    elif artifact_type == "task-breakdown" and not actions:
        _append_control(state, "✅ Approve preview", "approve-breakdown")
        _append_control(state, "✍️ Revise", "request-revision")
    elif artifact_type not in {"checklist", "questionnaire"} and not actions:
        _append_control(state, "💬 Discuss / Revise", "discuss")


def prepare_interaction(
    artifact: dict[str, Any],
    *,
    chat_id: str,
    thread_id: str | None = None,
    user_id: str | None = None,
    profile: str | None = None,
    root: Path | str | None = None,
) -> dict[str, Any]:
    state_root = _root(root)
    _cleanup_stale_files(state_root)
    token = secrets.token_urlsafe(12)
    now = time.time()
    values: dict[str, Any] = {}
    if artifact.get("type") == "form":
        for field in artifact.get("fields", []):
            if not isinstance(field, dict):
                continue
            default = field.get("default")
            if default is not None:
                values[str(field.get("id"))] = default
    state: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "token": token,
        "artifact": artifact,
        "artifact_type": artifact.get("type"),
        "chat_id": str(chat_id),
        "thread_id": str(thread_id) if thread_id is not None else None,
        "user_id": str(user_id) if user_id is not None else None,
        "profile": profile,
        "message_id": None,
        "status": "prepared",
        "revision": 0,
        "created_at": now,
        "expires_at": now + _TTL_SECONDS,
        "current_field": 0,
        "values": values,
        "custom_answers": {},
        "selected_items": [],
        "page": 0,
        "task_decisions": {},
        "controls": [],
        "actions": [],
        "prompt_message_id": None,
    }
    _refresh_controls(state)
    _write(_state_path(token, state_root), state)
    return state


def bind_message(token: str, message_id: str, *, root: Path | str | None = None) -> dict[str, Any]:
    handle, path = _locked(token, root)
    try:
        state = load_interaction(token, root=root)
        state["message_id"] = str(message_id)
        state["status"] = "active"
        _write(path, state)
        return state
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def bind_user(token: str, user_id: str, *, root: Path | str | None = None) -> dict[str, Any]:
    handle, path = _locked(token, root)
    try:
        state = load_interaction(token, root=root)
        if not state.get("user_id"):
            state["user_id"] = str(user_id)
            _write(path, state)
        return state
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def bind_prompt(token: str, *, chat_id: str, message_id: str, root: Path | str | None = None) -> None:
    handle, path = _locked(token, root)
    try:
        state = load_interaction(token, root=root)
        state["prompt_message_id"] = str(message_id)
        state["status"] = "awaiting_text"
        _write(path, state)
        ref = _root(root) / f"reply-{hashlib.sha256(str(chat_id).encode()).hexdigest()[:12]}-{message_id}.ref"
        ref.write_text(token, encoding="utf-8")
        os.chmod(ref, 0o600)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def lookup_prompt(chat_id: str, message_id: str, *, root: Path | str | None = None) -> str | None:
    ref = _root(root) / f"reply-{hashlib.sha256(str(chat_id).encode()).hexdigest()[:12]}-{message_id}.ref"
    try:
        return ref.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _form_payload(state: dict[str, Any]) -> str:
    artifact = state["artifact"]
    values: dict[str, Any] = {}
    for field in _form_fields(state):
        field_id = str(field.get("id"))
        fallback: Any = [] if field.get("type") == "multi-choice" else False if field.get("type") == "boolean" else ""
        values[field_id] = state["values"].get(field_id, fallback)
    artifact_id = str(artifact.get("id") or f"telegram-{state['token']}")
    stable = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{state['token']}:{artifact_id}:{stable}".encode()).hexdigest()[:16]
    envelope = {
        "actionId": "submit",
        "artifactId": artifact_id,
        "continuationInstruction": "Continue the active workflow after processing this response. Supporting tool results are not completion; stop only when the workflow is complete or another user answer is required.",
        "idempotencyKey": f"form:{digest}",
        "schemaVersion": 1,
        "type": "form-response",
        "values": values,
    }
    return "Hermes UI form response:\n" + json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


def _finish(state: dict[str, Any], path: Path, payload: str) -> InteractionResult:
    state["status"] = "submitting"
    state["submission_payload"] = payload
    state["revision"] += 1
    state["controls"] = []
    _write(path, state)
    return InteractionResult("submit", state, payload=payload)


def _advance_form(state: dict[str, Any], path: Path) -> InteractionResult:
    fields = _form_fields(state)
    if int(state["current_field"]) >= len(fields) - 1:
        state["status"] = "active"
    else:
        state["current_field"] += 1
    state["revision"] += 1
    state["status"] = "active"
    _refresh_controls(state)
    _write(path, state)
    return InteractionResult("edit", state)


def apply_control(
    token: str,
    revision: int,
    control_index: int,
    *,
    root: Path | str | None = None,
) -> InteractionResult:
    handle, path = _locked(token, root)
    try:
        state = load_interaction(token, root=root)
        if state.get("status") in {"submitting", "submitted", "cancelled", "expired"}:
            return InteractionResult("resolved", state)
        if int(state.get("revision", -1)) != int(revision):
            return InteractionResult("stale", state)
        controls = state.get("controls", [])
        if not 0 <= int(control_index) < len(controls):
            return InteractionResult("error", state, error="Invalid control")
        control = controls[int(control_index)]
        kind = control.get("kind")
        artifact = state["artifact"]

        if kind in {"select-option", "toggle-option"}:
            field = _form_fields(state)[int(control["field_index"])]
            _, value = _option(field.get("options", [])[int(control["option_index"])])
            field_id = str(field.get("id"))
            if kind == "select-option":
                state["values"][field_id] = value
                state.setdefault("custom_answers", {}).pop(field_id, None)
                state["revision"] += 1
                _refresh_controls(state)
                _write(path, state)
                return InteractionResult("edit", state)
            selected = list(state["values"].get(field_id, []))
            selected = [entry for entry in selected if entry != value] if value in selected else [*selected, value]
            state["values"][field_id] = selected
            state["revision"] += 1
            _refresh_controls(state)
            _write(path, state)
            return InteractionResult("edit", state)

        if kind == "select-boolean":
            field = _form_fields(state)[int(control["field_index"])]
            state["values"][str(field.get("id"))] = bool(control["value"])
            state["revision"] += 1
            _refresh_controls(state)
            _write(path, state)
            return InteractionResult("edit", state)
        if kind == "next-field":
            field = _form_fields(state)[int(state["current_field"])]
            value = state["values"].get(str(field.get("id")), [])
            if field.get("required") and not value:
                return InteractionResult("error", state, error="This field is required")
            return _advance_form(state, path)
        if kind == "submit-form":
            field = _form_fields(state)[int(state["current_field"])]
            value = state["values"].get(str(field.get("id")), [])
            if field.get("required") and not value:
                return InteractionResult("error", state, error="This field is required")
            return _finish(state, path, _form_payload(state))
        if kind == "request-text":
            field = _form_fields(state)[int(control["field_index"])]
            state["status"] = "awaiting_text"
            state["revision"] += 1
            _refresh_controls(state)
            _write(path, state)
            return InteractionResult("prompt", state, prompt=str(field.get("label") or "Answer"))
        if kind == "request-custom-answer":
            field = _form_fields(state)[int(control["field_index"])]
            state["status"] = "awaiting_text"
            state["free_text_mode"] = "custom-answer"
            state["custom_field_index"] = int(control["field_index"])
            state["revision"] += 1
            _refresh_controls(state)
            _write(path, state)
            return InteractionResult(
                "prompt",
                state,
                prompt=str(field.get("customAnswerLabel") or "Other answer"),
            )
        if kind == "skip-field":
            return _advance_form(state, path)
        if kind == "previous-field":
            state["current_field"] = max(0, int(state["current_field"]) - 1)
            state["revision"] += 1
            _refresh_controls(state)
            _write(path, state)
            return InteractionResult("edit", state)
        if kind == "toggle-item":
            items = artifact.get("items") or artifact.get("questions") or []
            item_index = int(control["item_index"])
            item = items[item_index]
            item_id = str(item.get("id") or item.get("name") or item_index)
            selected = list(state.get("selected_items", []))
            selected = [entry for entry in selected if entry != item_id] if item_id in selected else [*selected, item_id]
            state["selected_items"] = selected
            state["revision"] += 1
            _refresh_controls(state)
            _write(path, state)
            return InteractionResult("edit", state)
        if kind == "page":
            state["page"] = max(0, int(control["page"]))
            state["revision"] += 1
            _refresh_controls(state)
            _write(path, state)
            return InteractionResult("edit", state)
        if kind in {"select-all-items", "clear-items"}:
            items = artifact.get("items") or artifact.get("questions") or []
            state["selected_items"] = (
                [str(item.get("id") or item.get("name") or index) for index, item in enumerate(items) if isinstance(item, dict)]
                if kind == "select-all-items"
                else []
            )
            state["revision"] += 1
            _refresh_controls(state)
            _write(path, state)
            return InteractionResult("edit", state)
        if kind == "submit-checklist":
            payload = "Hermes UI checklist response:\n" + json.dumps(
                {"artifactId": artifact.get("id") or token, "selectedItemIds": state.get("selected_items", [])},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return _finish(state, path, payload)
        if kind == "submit-action":
            action = state.get("actions", [])[int(control["action_index"])]
            if action.get("submitText"):
                return _finish(state, path, action["submitText"])
            return InteractionResult("copy", state, payload=action.get("copyText", ""))
        if kind == "cycle-task":
            tasks = [task for task in artifact.get("tasks", []) if isinstance(task, dict)]
            task = tasks[int(control["task_index"])]
            task_id = str(task.get("id") or control["task_index"])
            cycle = ["unset", "today", "not_today", "later", "discuss"]
            current = str(state.setdefault("task_decisions", {}).get(task_id) or "unset")
            state["task_decisions"][task_id] = cycle[(cycle.index(current) + 1) % len(cycle)] if current in cycle else "today"
            state["revision"] += 1
            _refresh_controls(state)
            _write(path, state)
            return InteractionResult("edit", state)
        if kind == "submit-task-batch":
            payload = "Hermes UI planning decisions:\n" + json.dumps(
                {
                    "artifactId": artifact.get("id") or token,
                    "decisions": state.get("task_decisions", {}),
                    "instruction": "Prepare previews only; do not mutate FlowState until explicitly approved.",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return _finish(state, path, payload)
        if kind == "triage":
            task = artifact.get("task", {})
            payload = f"FlowState task triage decision\nID: {task.get('id', '')}\nTitle: {task.get('title', '')}\nDecision: {control['value']}\nShow me a preview before applying a real FlowState change."
            return _finish(state, path, payload)
        if kind == "approve-breakdown":
            payload = f"I approve the exact task-breakdown preview {artifact.get('id') or token}. Prepare the typed FlowState mutation preview; do not apply it yet."
            return _finish(state, path, payload)
        if kind in {"request-revision", "discuss"}:
            state["status"] = "awaiting_text"
            state["free_text_mode"] = kind
            state["revision"] += 1
            _refresh_controls(state)
            _write(path, state)
            return InteractionResult("prompt", state, prompt="What would you like to change or discuss?")
        if kind == "cancel":
            state["status"] = "cancelled"
            state["controls"] = []
            _write(path, state)
            return InteractionResult("cancelled", state)
        return InteractionResult("error", state, error="Unsupported control")
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def apply_text_reply(token: str, text: str, *, root: Path | str | None = None) -> InteractionResult:
    handle, path = _locked(token, root)
    try:
        state = load_interaction(token, root=root)
        if state.get("status") != "awaiting_text":
            return InteractionResult("stale", state)
        if state.get("artifact_type") != "form":
            payload = f"Regarding Hermes UI {state.get('artifact_type')} {state['artifact'].get('id') or token}:\n{text}"
            return _finish(state, path, payload)
        field = _form_fields(state)[int(state["current_field"])]
        value = str(text or "").strip()
        if field.get("required") and not value:
            return InteractionResult("error", state, error="This field is required")
        field_type = field.get("type")
        if field_type == "number":
            try:
                float(value)
            except ValueError:
                return InteractionResult("error", state, error="Enter a valid number")
        if field_type == "date" and value and not _DATE_RE.fullmatch(value):
            return InteractionResult("error", state, error="Use YYYY-MM-DD")
        if field_type == "time" and value and not _TIME_RE.fullmatch(value):
            return InteractionResult("error", state, error="Use 24-hour time HH:mm")
        field_id = str(field.get("id"))
        if state.get("free_text_mode") == "custom-answer":
            prior_custom = str(state.setdefault("custom_answers", {}).get(field_id) or "")
            if field_type == "multi-choice":
                selected = list(state["values"].get(field_id, []))
                selected = [entry for entry in selected if entry != prior_custom]
                state["values"][field_id] = [*selected, value]
            else:
                state["values"][field_id] = value
            state["custom_answers"][field_id] = value
            state.pop("free_text_mode", None)
            state.pop("custom_field_index", None)
        else:
            state["values"][field_id] = value
        state["prompt_message_id"] = None
        return _advance_form(state, path)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def mark_dispatched(token: str, *, root: Path | str | None = None) -> dict[str, Any]:
    handle, path = _locked(token, root)
    try:
        state = load_interaction(token, root=root)
        if state.get("status") == "submitting":
            state["status"] = "submitted"
            _write(path, state)
        return state
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def reopen_submission(token: str, *, root: Path | str | None = None) -> dict[str, Any]:
    handle, path = _locked(token, root)
    try:
        state = load_interaction(token, root=root)
        if state.get("status") == "submitting":
            state["status"] = "active"
            state["revision"] += 1
            _refresh_controls(state)
            _write(path, state)
        return state
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def keyboard_rows(state: dict[str, Any], *, columns: int = 1) -> list[list[dict[str, str]]]:
    buttons = [{"text": control["text"], "callback_data": control["callback_data"]} for control in state.get("controls", [])]
    width = max(1, min(int(columns), 3))
    return [buttons[index:index + width] for index in range(0, len(buttons), width)]
