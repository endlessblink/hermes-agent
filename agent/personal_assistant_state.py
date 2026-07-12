"""Durable profile-scoped state for the office-work personal assistant."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from utils import atomic_json_write

SCHEMA_VERSION = 1


def _default() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "version": 0,
        "revision": 0,
        "canonical_session_id": None,
        "working_picture": {
            "current_focus": None,
            "priorities": [],
            "commitments": [],
            "constraints": [],
            "notes": [],
        },
        "episode_summaries": [],
        "pending_approvals": [],
        "pending_proposals": [],
        "sync_status": {"status": "unknown", "updated_at": None, "detail": None},
        "outcomes": [],
        "commitments": [],
        "blockers": [],
        "deferred": [],
        "preferences": [],
        "capacity": {"summary": None, "updatedAt": None},
        "focus": None,
        "pendingApprovals": [],
        "captureProposals": [],
        "unreadCount": 0,
        "sync": {"status": "unknown", "lastCheckedAt": None, "lastVerifiedAt": None},
        "archived_at": None,
        "updated_at": None,
        "idempotency_keys": [],
        "durableSource": {"kind": "obsidian", "version": 0, "hash": None},
    }


class PersonalAssistantStateStore:
    def __init__(self, profile_home: Path):
        self.path = Path(profile_home) / "state" / "personal-assistant" / "home.json"
        self.lock_path = self.path.with_suffix(".lock")

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return _default()
        state = _default()
        if isinstance(raw, dict):
            state.update(raw)
        state["schema_version"] = SCHEMA_VERSION
        return state

    def read(self) -> dict[str, Any]:
        return copy.deepcopy(self._read())

    def update(self, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = self._read()
            mutate(state)
            state["revision"] = int(state.get("revision") or 0) + 1
            state["version"] = state["revision"]
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            atomic_json_write(self.path, state, indent=2, mode=0o600)
            return copy.deepcopy(state)

    def set_canonical_session(self, session_id: str | None) -> dict[str, Any]:
        return self.update(lambda state: state.__setitem__("canonical_session_id", session_id))

    def append_episode(
        self, *, trigger: str, user_intent: str, idempotency_key: str | None = None
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        result: dict[str, Any] = {}
        duplicate = False

        def mutate(state: dict[str, Any]) -> None:
            nonlocal duplicate
            episodes = state.setdefault("episode_summaries", [])
            if idempotency_key:
                for episode in episodes:
                    if episode.get("idempotency_key") == idempotency_key:
                        result.update(episode)
                        duplicate = True
                        return
            episode = {
                "episode_id": uuid4().hex,
                "trigger": trigger,
                "user_intent": user_intent,
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "archived_at": None,
                "idempotency_key": idempotency_key,
            }
            episodes.append(episode)
            result.update(episode)

        state = self.update(mutate)
        return state, copy.deepcopy(result), duplicate

    def mark_episode_status(self, episode_id: str, status: str) -> tuple[dict[str, Any], dict[str, Any]]:
        result: dict[str, Any] = {}

        def mutate(state: dict[str, Any]) -> None:
            for episode in state.get("episode_summaries", []):
                if episode.get("episode_id") == episode_id:
                    episode["status"] = status
                    result.update(episode)
                    return
            raise ValueError(f"personal assistant episode not found: {episode_id}")

        state = self.update(mutate)
        return state, copy.deepcopy(result)

    def patch(
        self,
        action: str,
        values: dict[str, Any],
        *,
        expected_version: int | None = None,
        operations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if action == "inspect" and not operations:
            return self.read()
        editable = {
            "working_picture", "pending_approvals", "pending_proposals", "sync_status",
            "outcomes", "blockers", "deferred", "capacity", "focus",
            "commitments", "preferences", "pendingApprovals", "captureProposals",
            "unreadCount", "sync",
        }

        def mutate(state: dict[str, Any]) -> None:
            if expected_version is not None and int(state.get("version") or 0) != expected_version:
                raise StateVersionConflict(int(state.get("version") or 0))
            if operations:
                for operation in operations:
                    _apply_operation(state, operation, editable)
                return
            if action == "edit":
                for key in editable:
                    if key in values:
                        state[key] = copy.deepcopy(values[key])
                return
            episode_id = str(values.get("episode_id") or "")
            if action == "archive":
                if episode_id:
                    for episode in state.get("episode_summaries", []):
                        if episode.get("episode_id") == episode_id:
                            episode["archived_at"] = datetime.now(timezone.utc).isoformat()
                            return
                state["archived_at"] = datetime.now(timezone.utc).isoformat()
                return
            if action == "forget":
                if episode_id:
                    state["episode_summaries"] = [
                        episode for episode in state.get("episode_summaries", [])
                        if episode.get("episode_id") != episode_id
                    ]
                else:
                    canonical = state.get("canonical_session_id")
                    state.clear()
                    state.update(_default())
                    state["canonical_session_id"] = canonical
                return
            raise ValueError(f"unsupported state action: {action}")

        return self.update(mutate)

    def public(self) -> dict[str, Any]:
        return public_state(self.read())


class StateVersionConflict(ValueError):
    def __init__(self, current_version: int):
        super().__init__(f"personal assistant state version conflict; current version is {current_version}")
        self.current_version = current_version


def _apply_operation(
    state: dict[str, Any], operation: dict[str, Any], editable: set[str]
) -> None:
    op = str(operation.get("op") or operation.get("action") or "").strip()
    field = str(
        operation.get("section") or operation.get("field") or operation.get("collection") or ""
    ).strip()
    if field not in editable:
        raise ValueError(f"unsupported personal assistant state field: {field}")
    if op in {"set", "edit"}:
        state[field] = copy.deepcopy(operation.get("value"))
        return
    items = state.get(field)
    if not isinstance(items, list):
        raise ValueError(f"personal assistant state field is not an item collection: {field}")
    item_id = str(operation.get("id") or "").strip()
    if not item_id:
        raise ValueError(f"{op} operation requires id")
    index = next(
        (i for i, item in enumerate(items) if isinstance(item, dict) and str(item.get("id")) == item_id),
        None,
    )
    if op == "upsert":
        value = copy.deepcopy(operation.get("value") or {})
        if not isinstance(value, dict):
            raise ValueError("upsert operation value must be an object")
        value["id"] = item_id
        if index is None:
            items.append(value)
        else:
            items[index] = {**items[index], **value}
        return
    if op == "archive":
        if index is not None:
            items[index]["archivedAt"] = datetime.now(timezone.utc).isoformat()
        return
    if op == "forget":
        if index is not None:
            items.pop(index)
        return
    raise ValueError(f"unsupported personal assistant state operation: {op}")


def public_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return the stable camelCase desktop contract, hiding storage metadata."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "version": int(state.get("version") or 0),
        "sessionId": state.get("canonical_session_id"),
        "outcomes": copy.deepcopy(state.get("outcomes") or []),
        "commitments": copy.deepcopy(state.get("commitments") or []),
        "capacity": copy.deepcopy(
            state.get("capacity") or {"summary": None, "updatedAt": None}
        ),
        "focus": copy.deepcopy(state.get("focus")),
        "blockers": copy.deepcopy(state.get("blockers") or []),
        "deferred": copy.deepcopy(state.get("deferred") or []),
        "preferences": copy.deepcopy(state.get("preferences") or []),
        "pendingApprovals": copy.deepcopy(state.get("pendingApprovals") or []),
        "captureProposals": copy.deepcopy(state.get("captureProposals") or []),
        "sync": copy.deepcopy(
            state.get("sync")
            or {"status": "unknown", "lastCheckedAt": None, "lastVerifiedAt": None}
        ),
        "unreadCount": int(state.get("unreadCount") or 0),
        "episodes": copy.deepcopy(state.get("episode_summaries") or []),
        "source": copy.deepcopy(state.get("durableSource") or {"kind": "obsidian", "version": 0, "hash": None}),
    }
