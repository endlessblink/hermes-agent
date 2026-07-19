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
_TERMINAL_MONITOR_DISPOSITIONS = {"suppressed", "handled"}
_SENSITIVE_KEY_PARTS = ("authorization", "cookie", "password", "secret", "token")
_TASK_EVENT_KINDS = {"blocker", "changed_high_priority"}
_PROTECTED_ITEM_KINDS = {"project", "commitment"}
_PROTECTED_DISPOSITIONS = {
    "actionable", "waiting", "deferred", "needs_context", "completed", "cancelled",
}
_SOURCE_COVERAGE_STATUSES = {"fresh", "partial", "stale", "unavailable"}
COVERAGE_RECEIPT_LIMIT = 32


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


def _optional_bounded_text(value: Any, label: str, *, limit: int = 2_000) -> str | None:
    if value is None:
        return None
    return _bounded_identifier(value, label, limit=limit)


def _bounded_string_list(value: Any, label: str, *, limit: int = 64) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{label} must be a list of at most {limit} identifiers")
    return list(dict.fromkeys(_bounded_identifier(item, label) for item in value))


def _validate_protected_item(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("protected item must be an object")
    item = {
        "id": _bounded_identifier(raw.get("id"), "protected item id", limit=300),
        "source": _bounded_identifier(raw.get("source"), "protected item source", limit=100),
        "sourceId": _bounded_identifier(raw.get("sourceId"), "protected item source id", limit=300),
        "kind": _bounded_identifier(raw.get("kind"), "protected item kind", limit=40),
        "title": _bounded_identifier(raw.get("title"), "protected item title", limit=1_000),
        "consequence": _bounded_identifier(
            raw.get("consequence"), "protected item consequence", limit=2_000
        ),
        "disposition": _bounded_identifier(
            raw.get("disposition"), "protected item disposition", limit=40
        ),
        "nextAction": _optional_bounded_text(raw.get("nextAction"), "protected item next action"),
        "dependencyIds": _bounded_string_list(
            raw.get("dependencyIds"), "protected item dependency id"
        ),
        "missingFields": _bounded_string_list(
            raw.get("missingFields"), "protected item missing field"
        ),
        "deferralReason": _optional_bounded_text(
            raw.get("deferralReason"), "protected item deferral reason"
        ),
        "deadline": raw.get("deadline"),
        "nextReviewAt": raw.get("nextReviewAt"),
        "sourceRevision": _optional_bounded_text(
            raw.get("sourceRevision"), "protected item source revision", limit=300
        ),
        "verifiedAt": raw.get("verifiedAt"),
    }
    if item["kind"] not in _PROTECTED_ITEM_KINDS:
        raise ValueError("protected item kind must be project or commitment")
    disposition = item["disposition"]
    if disposition not in _PROTECTED_DISPOSITIONS:
        raise ValueError("protected item disposition is invalid")
    for field in ("deadline", "nextReviewAt", "verifiedAt"):
        if item[field] is not None:
            item[field] = _iso_timestamp(item[field], f"protected item {field}")
    if disposition == "actionable" and not item["nextAction"]:
        raise ValueError("actionable protected item requires a next action")
    if disposition == "waiting" and (
        not item["dependencyIds"] or not item["nextReviewAt"]
    ):
        raise ValueError("waiting protected item requires dependencies and a next review")
    if disposition == "deferred" and (
        not item["deferralReason"] or not item["nextReviewAt"]
    ):
        raise ValueError("deferred protected item requires a reason and a next review")
    if disposition == "needs_context" and (
        not item["missingFields"] or not item["nextReviewAt"]
    ):
        raise ValueError("protected item needing context requires missing fields and a next review")
    if disposition in {"completed", "cancelled"} and not item["verifiedAt"]:
        raise ValueError("completed or cancelled protected item requires verification")
    return item


def _safe_protected_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    safe: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in raw:
        try:
            item = _validate_protected_item(value)
        except ValueError:
            continue
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        safe.append(item)
    return safe


def _build_coverage_receipt(
    *,
    cadence: str,
    scope_fingerprint: str,
    sources: list[dict[str, Any]],
    expected_item_ids: list[str],
    reviewed_item_ids: list[str],
    risk_item_ids: list[str],
    unresolved_item_ids: list[str],
) -> tuple[dict[str, Any], set[str]]:
    if cadence not in {"daily", "weekly"}:
        raise ValueError("coverage cadence must be daily or weekly")
    fingerprint = _bounded_identifier(
        scope_fingerprint, "coverage scope fingerprint", limit=500
    )
    if not isinstance(sources, list) or not sources or len(sources) > 20:
        raise ValueError("coverage sources must be a non-empty list of at most 20 items")
    safe_sources: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    blocking_reasons: list[str] = []
    for raw in sources:
        if not isinstance(raw, dict):
            raise ValueError("coverage source must be an object")
        source_id = _bounded_identifier(raw.get("id"), "coverage source id", limit=200)
        if source_id in source_ids:
            raise ValueError(f"duplicate coverage source: {source_id}")
        source_ids.add(source_id)
        status = _bounded_identifier(raw.get("status"), "coverage source status", limit=40)
        if status not in _SOURCE_COVERAGE_STATUSES:
            raise ValueError("coverage source status is invalid")
        revision = _optional_bounded_text(
            raw.get("revision"), "coverage source revision", limit=300
        )
        safe_sources.append({"id": source_id, "status": status, "revision": revision})
        if status != "fresh":
            blocking_reasons.append(f"source {source_id} is {status}")

    expected = _bounded_string_list(expected_item_ids, "expected protected item id", limit=500)
    reviewed = _bounded_string_list(reviewed_item_ids, "reviewed protected item id", limit=500)
    risks = _bounded_string_list(risk_item_ids, "risk protected item id", limit=500)
    unresolved = _bounded_string_list(
        unresolved_item_ids, "unresolved protected item id", limit=500
    )
    expected_set = set(expected)
    for label, values in (
        ("reviewed", reviewed), ("risk", risks), ("unresolved", unresolved)
    ):
        outside = sorted(set(values) - expected_set)
        if outside:
            raise ValueError(f"{label} item is outside the protected review scope: {outside[0]}")
    missing = sorted(expected_set - set(reviewed))
    if missing:
        noun = "item was" if len(missing) == 1 else "items were"
        blocking_reasons.append(f"{len(missing)} protected {noun} not reviewed")
    if unresolved:
        noun = "item has" if len(unresolved) == 1 else "items have"
        blocking_reasons.append(f"{len(unresolved)} protected {noun} unresolved context")

    receipt = {
        "id": uuid4().hex,
        "cadence": cadence,
        "scopeFingerprint": fingerprint,
        "sources": safe_sources,
        "expectedItemIds": expected,
        "reviewedItemIds": reviewed,
        "missingItemIds": missing,
        "riskItemIds": risks,
        "unresolvedItemIds": unresolved,
        "blockingReasons": blocking_reasons,
        "complete": not blocking_reasons,
        "allClear": not blocking_reasons and not risks,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    return receipt, expected_set


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
        "protected_items": [],
        "coverage_receipts": [],
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
        state["protected_items"] = _safe_protected_items(state.get("protected_items"))
        if not isinstance(state.get("coverage_receipts"), list):
            state["coverage_receipts"] = []
        state["coverage_receipts"] = state["coverage_receipts"][-COVERAGE_RECEIPT_LIMIT:]
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

    def upsert_protected_item(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Persist a protected project or commitment only with a safe disposition."""

        item = _validate_protected_item(raw)

        def mutate(state: dict[str, Any]) -> None:
            items = _safe_protected_items(state.get("protected_items"))
            index = next(
                (i for i, existing in enumerate(items) if existing["id"] == item["id"]),
                None,
            )
            if index is None:
                items.append(copy.deepcopy(item))
            else:
                items[index] = copy.deepcopy(item)
            state["protected_items"] = items

        return self.update(mutate)

    def record_coverage_receipt(
        self,
        *,
        cadence: str,
        scope_fingerprint: str,
        sources: list[dict[str, Any]],
        expected_item_ids: list[str],
        reviewed_item_ids: list[str],
        risk_item_ids: list[str],
        unresolved_item_ids: list[str],
    ) -> dict[str, Any]:
        """Record deterministic proof of what a daily or weekly safety sweep covered."""
        receipt, expected_set = _build_coverage_receipt(
            cadence=cadence,
            scope_fingerprint=scope_fingerprint,
            sources=sources,
            expected_item_ids=expected_item_ids,
            reviewed_item_ids=reviewed_item_ids,
            risk_item_ids=risk_item_ids,
            unresolved_item_ids=unresolved_item_ids,
        )

        def mutate(state: dict[str, Any]) -> None:
            registered = {item["id"] for item in _safe_protected_items(state.get("protected_items"))}
            unknown = sorted(expected_set - registered)
            if unknown:
                raise ValueError(f"protected item is not registered: {unknown[0]}")
            receipts = state.setdefault("coverage_receipts", [])
            receipts.append(copy.deepcopy(receipt))
            del receipts[:-COVERAGE_RECEIPT_LIMIT]

        self.update(mutate)
        return copy.deepcopy(receipt)

    def record_safety_review(
        self,
        *,
        protected_items: list[dict[str, Any]],
        cadence: str,
        scope_fingerprint: str,
        sources: list[dict[str, Any]],
        reviewed_item_ids: list[str],
        risk_item_ids: list[str],
        unresolved_item_ids: list[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Atomically merge protected scope and persist its deterministic receipt."""

        if not isinstance(protected_items, list) or len(protected_items) > 500:
            raise ValueError("protectedItems must be a list of at most 500 items")
        validated = [_validate_protected_item(item) for item in protected_items]
        submitted_ids = [item["id"] for item in validated]
        if len(submitted_ids) != len(set(submitted_ids)):
            raise ValueError("protectedItems contains duplicate ids")
        result: dict[str, Any] = {}

        def mutate(state: dict[str, Any]) -> None:
            existing = _safe_protected_items(state.get("protected_items"))
            by_id = {item["id"]: item for item in existing}
            for item in validated:
                by_id[item["id"]] = copy.deepcopy(item)
            merged = list(by_id.values())
            active_ids = [
                item["id"]
                for item in merged
                if item["disposition"] not in {"completed", "cancelled"}
            ]
            receipt, _ = _build_coverage_receipt(
                cadence=cadence,
                scope_fingerprint=scope_fingerprint,
                sources=sources,
                expected_item_ids=active_ids,
                reviewed_item_ids=reviewed_item_ids,
                risk_item_ids=risk_item_ids,
                unresolved_item_ids=unresolved_item_ids,
            )
            state["protected_items"] = merged
            receipts = state.setdefault("coverage_receipts", [])
            receipts.append(copy.deepcopy(receipt))
            del receipts[:-COVERAGE_RECEIPT_LIMIT]
            result.update(receipt)

        state = self.update(mutate)
        return state, copy.deepcopy(result)

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
        "protectedItems": copy.deepcopy(_safe_protected_items(state.get("protected_items"))),
        "latestCoverageReceipt": copy.deepcopy(
            (state.get("coverage_receipts") or [None])[-1]
        ),
        "source": copy.deepcopy(state.get("durableSource") or {"kind": "obsidian", "version": 0, "hash": None}),
    }
