"""Durable profile-scoped state for the office-work personal assistant."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import fcntl
import json
import math
from pathlib import Path
import re
from typing import Any, Callable
from uuid import uuid4

from utils import atomic_json_write

SCHEMA_VERSION = 1
CONTEXT_LEDGER_LIMIT = 128
ASSISTANT_MUTATION_LIMIT = 128
MONITOR_EVENT_BATCH_LIMIT = 32
MONITOR_EVENT_BYTES_LIMIT = 16_384
_MONITOR_EVENT_VERSION = 1
_MONITOR_EVENT_FIELDS = {
    "id", "version", "kind", "subject", "occurrence", "evidence", "created_at",
}
_CONTEXT_ENTRY_FIELDS = {
    "eventId", "event", "taskIds", "operationIds", "disposition",
    "episodeId", "firstSeenAt", "updatedAt",
}
_MONITOR_DISPOSITIONS = {
    "pending", "merged", "processing", "retry_wait", "suppressed", "handled", "failed",
}
_TERMINAL_MONITOR_DISPOSITIONS = {"merged", "suppressed", "handled"}
_ACTIVE_CONTEXT_DISPOSITIONS = {"pending", "processing", "retry_wait"}
_SENSITIVE_KEY_PARTS = ("authorization", "cookie", "password", "secret", "token")
_TASK_EVENT_KINDS = {"blocker", "changed_high_priority"}


def _bounded_identifier(value: Any, label: str, *, limit: int = 160) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > limit:
        raise ValueError(f"monitor {label} must be a non-empty trimmed string up to {limit} characters")
    return value


def _iso_timestamp(value: Any, label: str) -> str:
    value = _bounded_identifier(value, label, limit=80)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"monitor {label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"monitor {label} must include a timezone")
    return value


def _safe_monitor_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        raise ValueError("monitor evidence exceeds the maximum depth")
    if isinstance(value, dict):
        if len(value) > 50:
            raise ValueError("monitor evidence object is too large")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 100:
                raise ValueError("monitor evidence keys must be bounded strings")
            normalized = re.sub(r"[^a-z]", "", key.lower())
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                raise ValueError(f"monitor evidence contains sensitive field: {key}")
            result[key] = _safe_monitor_json(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        if len(value) > 50:
            raise ValueError("monitor evidence list is too large")
        return [_safe_monitor_json(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        if len(value) > 2_000:
            raise ValueError("monitor evidence string is too large")
        return value
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("monitor evidence numbers must be finite")
        return value
    raise ValueError("monitor evidence must contain JSON-safe values")


def _validate_monitor_event(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("monitor event must be an object")
    fields = set(raw)
    if fields != _MONITOR_EVENT_FIELDS:
        missing = sorted(_MONITOR_EVENT_FIELDS - fields)
        unknown = sorted(fields - _MONITOR_EVENT_FIELDS)
        raise ValueError(
            f"monitor event fields are invalid; missing={missing}, unknown={unknown}"
        )
    event_id = _bounded_identifier(raw.get("id"), "event id")
    version = raw.get("version")
    if version != _MONITOR_EVENT_VERSION:
        raise ValueError(f"unsupported monitor event version: {version}")
    kind = _bounded_identifier(raw.get("kind"), "kind", limit=100)
    subject = _bounded_identifier(raw.get("subject"), "subject")
    occurrence = raw.get("occurrence")
    if not isinstance(occurrence, int) or isinstance(occurrence, bool) or occurrence < 1:
        raise ValueError("monitor event occurrence must be a positive integer")
    evidence = _safe_monitor_json(raw.get("evidence"))
    if not isinstance(evidence, dict):
        raise ValueError("monitor event evidence must be an object")
    event = {
        "id": event_id,
        "version": version,
        "kind": kind,
        "subject": subject,
        "occurrence": occurrence,
        "evidence": evidence,
        "created_at": _iso_timestamp(raw.get("created_at"), "created_at"),
    }
    encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MONITOR_EVENT_BYTES_LIMIT:
        raise ValueError("monitor event is too large")
    return event


def validate_monitor_events(events: Any) -> list[dict[str, Any]]:
    """Validate and copy a bounded batch at the RPC/storage trust boundary."""

    if not isinstance(events, list) or not events:
        raise ValueError("monitor events must be a non-empty list")
    if len(events) > MONITOR_EVENT_BATCH_LIMIT:
        raise ValueError(
            f"monitor event batch exceeds {MONITOR_EVENT_BATCH_LIMIT} events"
        )
    validated: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for raw in events:
        event = _validate_monitor_event(raw)
        previous = by_id.get(event["id"])
        if previous is not None and previous != event:
            raise ValueError(f"monitor event identity collision: {event['id']}")
        if previous is None:
            by_id[event["id"]] = event
            validated.append(event)
    return validated


def _collect_evidence_ids(event: dict[str, Any]) -> tuple[list[str], list[str]]:
    task_ids: set[str] = set()
    operation_ids: set[str] = set()

    def walk(value: Any, parent_key: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = re.sub(r"[^a-z]", "", key.lower())
                if normalized == "operationid" and isinstance(item, str) and item.strip():
                    operation_ids.add(item.strip()[:160])
                elif normalized == "operationids" and isinstance(item, list):
                    operation_ids.update(
                        candidate.strip()[:160]
                        for candidate in item
                        if isinstance(candidate, str) and candidate.strip()
                    )
                elif normalized == "taskid" and isinstance(item, str) and item.strip():
                    task_ids.add(item.strip()[:160])
                elif normalized == "taskids" and isinstance(item, list):
                    task_ids.update(
                        candidate.strip()[:160]
                        for candidate in item
                        if isinstance(candidate, str) and candidate.strip()
                    )
                elif (
                    normalized == "id"
                    and event["kind"] in _TASK_EVENT_KINDS
                    and isinstance(item, str)
                    and item.strip()
                ):
                    task_ids.add(item.strip()[:160])
                walk(item, key)
        elif isinstance(value, list):
            for item in value:
                walk(item, parent_key)

    walk(event["evidence"])
    return sorted(task_ids)[:32], sorted(operation_ids)[:32]


def _collect_task_operation_ids(event: dict[str, Any]) -> dict[str, set[str]]:
    task_operations: dict[str, set[str]] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            local_tasks: set[str] = set()
            local_operations: set[str] = set()
            for key, item in value.items():
                normalized = re.sub(r"[^a-z]", "", key.lower())
                if normalized == "taskid" and isinstance(item, str) and item.strip():
                    local_tasks.add(item.strip()[:160])
                elif normalized == "taskids" and isinstance(item, list):
                    local_tasks.update(
                        candidate.strip()[:160]
                        for candidate in item
                        if isinstance(candidate, str) and candidate.strip()
                    )
                elif (
                    normalized == "id"
                    and event["kind"] in _TASK_EVENT_KINDS
                    and isinstance(item, str)
                    and item.strip()
                ):
                    local_tasks.add(item.strip()[:160])
                elif normalized == "operationid" and isinstance(item, str) and item.strip():
                    local_operations.add(item.strip()[:160])
                elif normalized == "operationids" and isinstance(item, list):
                    local_operations.update(
                        candidate.strip()[:160]
                        for candidate in item
                        if isinstance(candidate, str) and candidate.strip()
                    )
            for task_id in local_tasks:
                task_operations.setdefault(task_id, set()).update(local_operations)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(event["evidence"])
    return task_operations


def _validate_disposition(value: Any) -> str:
    if value not in _MONITOR_DISPOSITIONS:
        raise ValueError(
            "monitor disposition must be one of: "
            + ", ".join(sorted(_MONITOR_DISPOSITIONS))
        )
    return str(value)


def _validate_context_entry(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != _CONTEXT_ENTRY_FIELDS:
        raise ValueError("monitor context ledger entry fields are invalid")
    event = _validate_monitor_event(raw.get("event"))
    event_id = _bounded_identifier(raw.get("eventId"), "event id")
    if event_id != event["id"]:
        raise ValueError("monitor context ledger event identity mismatch")
    task_ids = raw.get("taskIds")
    operation_ids = raw.get("operationIds")
    if not isinstance(task_ids, list) or len(task_ids) > 32:
        raise ValueError("monitor context ledger task ids are invalid")
    if not isinstance(operation_ids, list) or len(operation_ids) > 32:
        raise ValueError("monitor context ledger operation ids are invalid")
    safe_task_ids = sorted({_bounded_identifier(value, "task id") for value in task_ids})
    safe_operation_ids = sorted(
        {_bounded_identifier(value, "operation id") for value in operation_ids}
    )
    episode_id = raw.get("episodeId")
    if episode_id is not None:
        episode_id = _bounded_identifier(episode_id, "episode id")
    return {
        "eventId": event_id,
        "event": event,
        "taskIds": safe_task_ids,
        "operationIds": safe_operation_ids,
        "disposition": _validate_disposition(raw.get("disposition")),
        "episodeId": episode_id,
        "firstSeenAt": _iso_timestamp(raw.get("firstSeenAt"), "firstSeenAt"),
        "updatedAt": _iso_timestamp(raw.get("updatedAt"), "updatedAt"),
    }


def _safe_context_ledger(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    safe: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in raw[-CONTEXT_LEDGER_LIMIT:]:
        try:
            entry = _validate_context_entry(value)
        except ValueError:
            continue
        if entry["eventId"] in seen:
            continue
        seen.add(entry["eventId"])
        safe.append(entry)
    return safe


def _validate_assistant_mutation(raw: Any) -> dict[str, Any]:
    fields = {
        "operationId", "sessionKey", "episodeId", "turnId", "tasks", "recordedAt",
    }
    if not isinstance(raw, dict) or set(raw) != fields:
        raise ValueError("assistant mutation fields are invalid")
    episode_id = raw.get("episodeId")
    if episode_id is not None:
        episode_id = _bounded_identifier(episode_id, "episode id")
    turn_id = raw.get("turnId")
    if turn_id is not None:
        turn_id = _bounded_identifier(turn_id, "turn id")
    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or not tasks or len(tasks) > 64:
        raise ValueError("assistant mutation tasks are invalid")
    safe_tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {
            "taskId", "canonicalRevision", "changeSequence",
        }:
            raise ValueError("assistant mutation task proof is invalid")
        task_id = _bounded_identifier(task.get("taskId"), "task id")
        revision = task.get("canonicalRevision")
        sequence = task.get("changeSequence")
        if (
            not isinstance(revision, int) or isinstance(revision, bool) or revision < 1
            or not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1
            or task_id in seen
        ):
            raise ValueError("assistant mutation task proof is invalid")
        seen.add(task_id)
        safe_tasks.append({
            "taskId": task_id,
            "canonicalRevision": revision,
            "changeSequence": sequence,
        })
    return {
        "operationId": _bounded_identifier(raw.get("operationId"), "operation id"),
        "sessionKey": _bounded_identifier(raw.get("sessionKey"), "session key"),
        "episodeId": episode_id,
        "turnId": turn_id,
        "tasks": safe_tasks,
        "recordedAt": _iso_timestamp(raw.get("recordedAt"), "recordedAt"),
    }


def _safe_assistant_mutations(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    safe: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in raw[-ASSISTANT_MUTATION_LIMIT:]:
        try:
            mutation = _validate_assistant_mutation(value)
        except ValueError:
            continue
        if mutation["operationId"] in seen:
            continue
        seen.add(mutation["operationId"])
        safe.append(mutation)
    return safe


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
        "context_ledger": [],
        "assistant_mutations": [],
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
        state["context_ledger"] = _safe_context_ledger(state.get("context_ledger"))
        state["assistant_mutations"] = _safe_assistant_mutations(
            state.get("assistant_mutations")
        )
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

    def merge_monitor_events(
        self,
        events: list[dict[str, Any]],
        *,
        disposition: str,
        episode_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically merge exact structured monitor events into bounded context.

        Event identity is authoritative: replaying the same object is safe, while
        reusing an id for different evidence is rejected rather than silently
        replacing the evidence the assistant previously saw.
        """

        disposition = _validate_disposition(disposition)
        if episode_id is not None:
            episode_id = _bounded_identifier(episode_id, "episode id")
        validated = validate_monitor_events(events)

        def mutate(state: dict[str, Any]) -> None:
            ledger = _safe_context_ledger(state.get("context_ledger"))
            by_id = {entry["eventId"]: entry for entry in ledger}
            new_events: list[dict[str, Any]] = []
            for event in validated:
                existing = by_id.get(event["id"])
                if existing is not None:
                    if existing["event"] != event:
                        raise ValueError(f"monitor event identity collision: {event['id']}")
                    if existing["disposition"] in _TERMINAL_MONITOR_DISPOSITIONS:
                        if existing["disposition"] != disposition:
                            raise ValueError(
                                f"monitor event is already terminal: {event['id']}"
                            )
                        continue
                    existing["disposition"] = disposition
                    if episode_id is not None:
                        existing["episodeId"] = episode_id
                    existing["updatedAt"] = datetime.now(timezone.utc).isoformat()
                    continue
                new_events.append(event)

            overflow = len(ledger) + len(new_events) - CONTEXT_LEDGER_LIMIT
            if overflow > 0:
                terminal_indexes = [
                    index
                    for index, entry in enumerate(ledger)
                    if entry["disposition"] in _TERMINAL_MONITOR_DISPOSITIONS
                ]
                if len(terminal_indexes) < overflow:
                    raise ValueError(
                        "monitor context ledger is full of active entries"
                    )
                evict = set(terminal_indexes[:overflow])
                ledger = [entry for index, entry in enumerate(ledger) if index not in evict]

            now = datetime.now(timezone.utc).isoformat()
            for event in new_events:
                task_ids, operation_ids = _collect_evidence_ids(event)
                ledger.append(
                    {
                        "eventId": event["id"],
                        "event": copy.deepcopy(event),
                        "taskIds": task_ids,
                        "operationIds": operation_ids,
                        "disposition": disposition,
                        "episodeId": episode_id,
                        "firstSeenAt": now,
                        "updatedAt": now,
                    }
                )
            state["context_ledger"] = ledger

        return self.update(mutate)

    def mark_monitor_events(
        self,
        event_ids: list[str],
        *,
        disposition: str,
        episode_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically update disposition and episode binding for known events."""

        disposition = _validate_disposition(disposition)
        if episode_id is not None:
            episode_id = _bounded_identifier(episode_id, "episode id")
        if not isinstance(event_ids, list) or not event_ids:
            raise ValueError("monitor event ids must be a non-empty list")
        if len(event_ids) > MONITOR_EVENT_BATCH_LIMIT:
            raise ValueError(
                f"monitor event id batch exceeds {MONITOR_EVENT_BATCH_LIMIT} events"
            )
        ids = list(dict.fromkeys(_bounded_identifier(value, "event id") for value in event_ids))

        def mutate(state: dict[str, Any]) -> None:
            ledger = _safe_context_ledger(state.get("context_ledger"))
            by_id = {entry["eventId"]: entry for entry in ledger}
            missing = [event_id for event_id in ids if event_id not in by_id]
            if missing:
                raise ValueError(f"monitor event not found: {missing[0]}")
            for event_id in ids:
                entry = by_id[event_id]
                current = entry["disposition"]
                if current in _TERMINAL_MONITOR_DISPOSITIONS and current != disposition:
                    raise ValueError(f"monitor event is already terminal: {event_id}")
            now = datetime.now(timezone.utc).isoformat()
            for event_id in ids:
                entry = by_id[event_id]
                entry["disposition"] = disposition
                if episode_id is not None:
                    entry["episodeId"] = episode_id
                entry["updatedAt"] = now
            state["context_ledger"] = ledger

        return self.update(mutate)

    def monitor_event_ids(
        self, *, dispositions: set[str] | None = None
    ) -> set[str]:
        if dispositions is not None:
            if not isinstance(dispositions, set):
                raise ValueError("monitor dispositions filter must be a set")
            filters = {_validate_disposition(value) for value in dispositions}
        else:
            filters = None
        return {
            entry["eventId"]
            for entry in self.read().get("context_ledger", [])
            if filters is None or entry["disposition"] in filters
        }

    def has_monitor_event(
        self, event_id: str, *, dispositions: set[str] | None = None
    ) -> bool:
        event_id = _bounded_identifier(event_id, "event id")
        return event_id in self.monitor_event_ids(dispositions=dispositions)

    def record_assistant_mutation(self, proof: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(proof, dict):
            raise ValueError("assistant mutation proof must be an object")
        candidate = _validate_assistant_mutation({
            **copy.deepcopy(proof),
            "recordedAt": datetime.now(timezone.utc).isoformat(),
        })

        def mutate(state: dict[str, Any]) -> None:
            mutations = _safe_assistant_mutations(state.get("assistant_mutations"))
            for existing in mutations:
                if existing["operationId"] != candidate["operationId"]:
                    continue
                stable_fields = {"operationId", "sessionKey", "tasks"}
                comparable_existing = {
                    key: value for key, value in existing.items() if key in stable_fields
                }
                comparable_candidate = {
                    key: value for key, value in candidate.items() if key in stable_fields
                }
                if comparable_existing != comparable_candidate:
                    raise ValueError(
                        f"assistant mutation identity collision: {candidate['operationId']}"
                    )
                state["assistant_mutations"] = mutations
                return
            mutations.append(candidate)
            state["assistant_mutations"] = mutations[-ASSISTANT_MUTATION_LIMIT:]

        return self.update(mutate)

    def classify_monitor_events(
        self, events: list[dict[str, Any]]
    ) -> dict[str, list[dict[str, Any]]]:
        validated = validate_monitor_events(events)
        state = self.read()
        mutations = _safe_assistant_mutations(state.get("assistant_mutations"))
        assistant_operations = {item["operationId"] for item in mutations}
        assistant_revisions = {
            (task["taskId"], task["canonicalRevision"])
            for item in mutations
            for task in item["tasks"]
        }
        active_task_ids = {
            task_id
            for entry in _safe_context_ledger(state.get("context_ledger"))
            if entry["disposition"] in _ACTIVE_CONTEXT_DISPOSITIONS
            for task_id in entry["taskIds"]
        }
        result: dict[str, list[dict[str, Any]]] = {
            "suppressed": [], "merged": [], "remaining": [],
        }
        for event in validated:
            task_ids, operation_ids = _collect_evidence_ids(event)
            task_id_set = set(task_ids)
            operation_id_set = set(operation_ids)
            task_operations = _collect_task_operation_ids(event)
            revision = event["evidence"].get("canonicalRevision")
            exact_revision = (
                not operation_id_set
                and len(task_id_set) == 1
                and isinstance(revision, int)
                and not isinstance(revision, bool)
                and any((task_id, revision) in assistant_revisions for task_id in task_id_set)
            )
            exact_operations = (
                bool(task_id_set)
                and all(
                    task_operations.get(task_id)
                    and task_operations[task_id].issubset(assistant_operations)
                    for task_id in task_id_set
                )
            )
            if exact_operations or exact_revision:
                result["suppressed"].append(event)
            elif task_id_set and task_id_set.issubset(active_task_ids):
                result["merged"].append(event)
            else:
                result["remaining"].append(event)
        return result

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
        "contextLedger": copy.deepcopy(_safe_context_ledger(state.get("context_ledger"))),
        "source": copy.deepcopy(state.get("durableSource") or {"kind": "obsidian", "version": 0, "hash": None}),
    }
