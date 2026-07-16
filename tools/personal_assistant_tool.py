"""Primitive tools for the persistent office-work personal assistant state."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent.personal_assistant_state import (
    PersonalAssistantStateStore,
    StateVersionConflict,
    _apply_operation,
    public_state,
)
from tools.registry import registry, tool_error


def _profile_context() -> tuple[str, Path]:
    from hermes_cli.profiles import get_active_profile_name
    from hermes_constants import get_hermes_home

    return (get_active_profile_name() or "default", Path(get_hermes_home()))


def _store() -> PersonalAssistantStateStore:
    profile, profile_home = _profile_context()
    if profile != "office-work":
        raise ValueError("personal assistant state is available only in office-work")
    return PersonalAssistantStateStore(profile_home)


def _check_office_work_profile() -> bool:
    try:
        return _profile_context()[0] == "office-work"
    except Exception:
        return False


def _service(store: PersonalAssistantStateStore):
    """Use Obsidian as durable truth when the active profile configured it."""
    import yaml

    _, profile_home = _profile_context()
    config_path = profile_home / "config.yaml"
    if not config_path.is_file():
        return None
    from agent.personal_assistant_obsidian import PersonalAssistantObsidianAdapter
    from agent.personal_assistant_service import PersonalAssistantStateService

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return PersonalAssistantStateService(store, PersonalAssistantObsidianAdapter(raw))


def _result(payload: dict[str, Any]) -> str:
    return json.dumps({"result": payload}, ensure_ascii=False)


def _error(exc: Exception | str) -> str:
    return tool_error(str(exc))


def _handle_get_state(args: dict, **kwargs) -> str:
    try:
        store = _store()
        service = _service(store)
        state = service.get() if service is not None else store.read()
        return _result({"state": public_state(state)})
    except Exception as exc:
        return _error(exc)


def _proposal_id(section: str, title: str, evidence: str, source: str) -> str:
    canonical = json.dumps(
        [section, title, evidence, source], ensure_ascii=False, separators=(",", ":")
    )
    return "capture-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _handle_propose_capture(args: dict, **kwargs) -> str:
    section = str(args.get("section") or "").strip()
    if section not in {"outcomes", "commitments", "preferences"}:
        return _error("section must be outcomes, commitments, or preferences")
    title = str(args.get("title") or "").strip()[:500]
    if not title:
        return _error("title is required")
    evidence = str(args.get("evidence") or "").strip()[:2000]
    source = str(args.get("sourceSessionId") or "").strip()[:200]
    proposal_id = _proposal_id(section, title, evidence, source)
    proposal = {
        "id": proposal_id,
        "section": section,
        "title": title,
        "evidence": evidence,
        "sourceSessionId": source or None,
        "status": "pending",
    }
    try:
        store = _store()
        existing = next(
            (
                item
                for item in store.read().get("captureProposals", [])
                if isinstance(item, dict) and item.get("id") == proposal_id
            ),
            None,
        )
        if existing is not None:
            return _result(
                {"proposal": existing, "stateVersion": store.read().get("version", 0)}
            )
        captured = proposal

        def mutate(state: dict[str, Any]) -> None:
            nonlocal captured
            proposals = state.setdefault("captureProposals", [])
            existing = next(
                (
                    item
                    for item in proposals
                    if isinstance(item, dict) and item.get("id") == proposal_id
                ),
                None,
            )
            if existing is not None:
                captured = dict(existing)
                return
            proposals.append(proposal)

        state = store.update(mutate)
        return _result({"proposal": captured, "stateVersion": state["version"]})
    except Exception as exc:
        return _error(exc)


def _handle_state_change(args: dict, **kwargs) -> str:
    operations = args.get("operations")
    if not isinstance(operations, list) or not operations:
        return _error("operations must be a non-empty list")
    if len(operations) > 25:
        return _error("operations may contain at most 25 items")
    preview = args.get("preview") is not False
    request_id = str(args.get("requestId") or "").strip()
    if not preview and not request_id:
        return _error("requestId is required when preview is false")
    if len(request_id) > 200:
        return _error("requestId may contain at most 200 characters")
    operations_json = json.dumps(operations, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(operations_json.encode("utf-8")) > 65536:
        return _error("operations payload may contain at most 65536 bytes")
    operations_digest = hashlib.sha256(operations_json.encode("utf-8")).hexdigest()
    try:
        store = _store()
        service = _service(store)
        current = service.get() if service is not None else store.read()
        if preview:
            return _result(
                {
                    "preview": True,
                    "currentVersion": current.get("version", 0),
                    "operations": operations,
                }
            )

        expected = args.get("expectedVersion")
        expected_version = int(expected) if expected is not None else None
        replayed = False

        existing_keys = current.get("idempotency_keys") or []
        for entry in existing_keys:
            if entry == request_id:
                return _result(
                    {"preview": False, "replayed": True, "state": public_state(current)}
                )
            if isinstance(entry, dict) and entry.get("id") == request_id:
                if entry.get("digest") != operations_digest:
                    return _error("requestId was already used for different operations")
                return _result(
                    {"preview": False, "replayed": True, "state": public_state(current)}
                )

        def mutate(state: dict[str, Any]) -> None:
            nonlocal replayed
            keys = state.setdefault("idempotency_keys", [])
            for entry in keys:
                if entry == request_id:
                    replayed = True
                    return
                if isinstance(entry, dict) and entry.get("id") == request_id:
                    if entry.get("digest") != operations_digest:
                        raise ValueError(
                            "requestId was already used for different operations"
                        )
                    replayed = True
                    return
            if expected_version is not None and int(state.get("version") or 0) != expected_version:
                raise StateVersionConflict(int(state.get("version") or 0))
            editable = {
                "outcomes", "commitments", "blockers", "deferred", "preferences",
                "capacity", "focus", "pendingApprovals", "captureProposals", "sync",
                "unreadCount",
            }
            for operation in operations:
                if not isinstance(operation, dict):
                    raise ValueError("each operation must be an object")
                _apply_operation(state, operation, editable)
            keys.append({"id": request_id, "digest": operations_digest})
            del keys[:-128]

        if service is not None:
            state = service.patch(
                expected_version if expected_version is not None else int(current.get("version") or 0),
                operations,
            )

            def remember(value: dict[str, Any]) -> None:
                keys = value.setdefault("idempotency_keys", [])
                if not any(
                    entry == request_id
                    or (isinstance(entry, dict) and entry.get("id") == request_id)
                    for entry in keys
                ):
                    keys.append({"id": request_id, "digest": operations_digest})
                    del keys[:-128]

            state = store.update(remember)
        else:
            state = store.update(mutate)
        return _result(
            {"preview": False, "replayed": replayed, "state": public_state(state)}
        )
    except Exception as exc:
        return _error(exc)


def _handle_reconcile_inventory(args: dict, **kwargs) -> str:
    """Reconcile cross-system task counts without promoting partial evidence."""
    question = str(args.get("inventoryQuestion") or "").strip()[:1000]
    sources = args.get("sources")
    if not question:
        return _error("inventoryQuestion is required")
    if not isinstance(sources, list) or not sources:
        return _error("sources must be a non-empty list")
    if len(sources) > 20:
        return _error("sources may contain at most 20 items")
    if len(json.dumps(sources, ensure_ascii=False).encode("utf-8")) > 524288:
        return _error("sources payload may contain at most 524288 bytes")

    source_rows: list[dict[str, Any]] = []
    reconciled: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    blocking_reasons: list[str] = []
    unknown_count = 0

    for raw_source in sources:
        if not isinstance(raw_source, dict):
            return _error("each source must be an object")
        source_id = str(raw_source.get("sourceId") or "").strip()[:300]
        scope = str(raw_source.get("scope") or "").strip()[:1000]
        captured_at = str(raw_source.get("capturedAt") or "").strip()[:100]
        complete = raw_source.get("complete")
        items = raw_source.get("items")
        if not source_id or not scope or not captured_at:
            return _error("each source requires sourceId, scope, and capturedAt")
        if not isinstance(complete, bool):
            return _error(f"source {source_id} complete must be boolean")
        if not isinstance(items, list):
            return _error(f"source {source_id} items must be a list")
        if len(items) > 500:
            return _error(f"source {source_id} may contain at most 500 items")

        source_unknown = 0
        source_uncharacterized = 0
        seen_source_ids: set[str] = set()
        for raw_item in items:
            if not isinstance(raw_item, dict):
                return _error(f"source {source_id} items must be objects")
            item_id = str(raw_item.get("id") or "").strip()[:500]
            title = str(raw_item.get("title") or "").strip()[:1000]
            classification = str(raw_item.get("classification") or "").strip()
            evidence = str(raw_item.get("evidence") or "").strip()[:2000]
            canonical_id = str(raw_item.get("canonicalId") or "").strip()[:500]
            if not item_id or not title or not evidence:
                return _error(
                    f"source {source_id} items require id, title, and evidence"
                )
            if item_id in seen_source_ids:
                return _error(f"source {source_id} contains duplicate item id {item_id}")
            seen_source_ids.add(item_id)
            if classification not in {
                "characterized", "uncharacterized", "unknown"
            }:
                return _error(
                    "classification must be characterized, uncharacterized, or unknown"
                )
            if classification == "unknown":
                source_unknown += 1
                unknown_count += 1
            elif classification == "uncharacterized":
                source_uncharacterized += 1

            key = canonical_id or f"{source_id}:{item_id}"
            candidate = {
                "canonicalId": key,
                "classification": classification,
                "sourceId": source_id,
                "itemId": item_id,
                "title": title,
            }
            existing = reconciled.get(key)
            if existing is None:
                reconciled[key] = candidate
            elif existing["classification"] != classification:
                conflicts.append(
                    {
                        "canonicalId": key,
                        "classifications": [
                            existing["classification"],
                            classification,
                        ],
                        "sources": [existing["sourceId"], source_id],
                    }
                )

        source_rows.append(
            {
                "sourceId": source_id,
                "scope": scope,
                "capturedAt": captured_at,
                "complete": complete,
                "observedTotal": len(items),
                "observedUncharacterized": source_uncharacterized,
                "unknown": source_unknown,
            }
        )
        if not complete:
            blocking_reasons.append(f"source {source_id} is partial")

    if unknown_count:
        noun = "item has" if unknown_count == 1 else "items have"
        blocking_reasons.append(
            f"{unknown_count} {noun} unknown characterization"
        )
    if conflicts:
        blocking_reasons.append(
            f"{len(conflicts)} canonical item has conflicting classifications"
            if len(conflicts) == 1
            else f"{len(conflicts)} canonical items have conflicting classifications"
        )

    verified = not blocking_reasons
    exact_uncharacterized = sum(
        1
        for item in reconciled.values()
        if item["classification"] == "uncharacterized"
    )
    return _result(
        {
            "inventoryQuestion": question,
            "verified": verified,
            "exactTotal": len(reconciled) if verified else None,
            "exactUncharacterized": exact_uncharacterized if verified else None,
            "observedTotal": len(reconciled),
            "observedUncharacterized": exact_uncharacterized,
            "sources": source_rows,
            "conflicts": conflicts,
            "blockingReasons": blocking_reasons,
        }
    )


GET_STATE_SCHEMA = {
    "name": "personal_assistant_get_state",
    "description": "Read the persistent office-work assistant's current working picture and pending decisions.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

RECONCILE_INVENTORY_SCHEMA = {
    "name": "personal_assistant_reconcile_inventory",
    "description": (
        "Reconcile task inventory evidence from FlowState, Notion, Obsidian, or other sources. "
        "This is the required proof gate before stating a cross-source task count as exact. "
        "Partial sources, unknown characterization, or conflicting canonical IDs return no exact count."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "inventoryQuestion": {"type": "string", "minLength": 1, "maxLength": 1000},
            "sources": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "sourceId": {"type": "string", "minLength": 1, "maxLength": 300},
                        "scope": {"type": "string", "minLength": 1, "maxLength": 1000},
                        "capturedAt": {"type": "string", "minLength": 1, "maxLength": 100},
                        "complete": {"type": "boolean"},
                        "items": {
                            "type": "array",
                            "maxItems": 500,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string", "minLength": 1, "maxLength": 500},
                                    "canonicalId": {"type": "string", "maxLength": 500},
                                    "title": {"type": "string", "minLength": 1, "maxLength": 1000},
                                    "classification": {
                                        "type": "string",
                                        "enum": ["characterized", "uncharacterized", "unknown"],
                                    },
                                    "evidence": {"type": "string", "minLength": 1, "maxLength": 2000},
                                },
                                "required": ["id", "title", "classification", "evidence"],
                            },
                        },
                    },
                    "required": ["sourceId", "scope", "capturedAt", "complete", "items"],
                },
            },
        },
        "required": ["inventoryQuestion", "sources"],
    },
}

PROPOSE_CAPTURE_SCHEMA = {
    "name": "personal_assistant_propose_capture",
    "description": (
        "Queue a proposed outcome, commitment, or preference found in an office-work conversation. "
        "This does not accept or persist the proposal as truth; the user reviews it in the assistant home."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "enum": ["outcomes", "commitments", "preferences"],
                "description": "outcomes, commitments, or preferences",
            },
            "title": {"type": "string", "minLength": 1, "maxLength": 500},
            "evidence": {"type": "string", "maxLength": 2000, "description": "Exact user-supported reason for the proposal."},
            "sourceSessionId": {"type": "string", "maxLength": 200},
        },
        "required": ["section", "title", "evidence"],
    },
}

STATE_CHANGE_SCHEMA = {
    "name": "personal_assistant_state_change",
    "description": (
        "Preview or apply explicitly approved edits to the personal assistant working picture. "
        "Defaults to preview. Apply requires requestId and should follow scoped user approval."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "expectedVersion": {"type": "integer"},
            "operations": {
                "type": "array",
                "minItems": 1,
                "maxItems": 25,
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": ["set", "edit", "upsert", "archive", "forget"]},
                        "section": {
                            "type": "string",
                            "enum": [
                                "outcomes", "commitments", "blockers", "deferred",
                                "preferences", "capacity", "focus", "pendingApprovals",
                                "captureProposals", "sync", "unreadCount",
                            ],
                        },
                        "id": {"type": "string", "maxLength": 500},
                        "value": {},
                    },
                    "required": ["op", "section"],
                },
            },
            "preview": {"type": "boolean"},
            "requestId": {"type": "string", "maxLength": 200},
        },
        "required": ["operations"],
    },
}


SUGGESTION_RULE_SAVE_SCHEMA = {
    "name": "suggestion_rule_save",
    "description": (
        "Record that the user brushed off a proactive suggestion so its whole class is never "
        "re-suggested. Call this the moment the user rejects a suggestion, then append the "
        "returned one-line acknowledgment to your reply. Use mood_flavored=true when the "
        "rejection is about today's energy ('not tonight', 'I don't feel well') — that mutes "
        "suggestions for today only instead of creating a rule."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "rule_class": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
                "description": "Short kebab-case slug for the IDEA being rejected, not the wording (e.g. evening-appliance-check).",
            },
            "reason": {
                "type": "string",
                "maxLength": 160,
                "description": "The user's generalizable reason, if they gave one (e.g. 'laundry closes before evening'). A reasoned rule is permanent immediately.",
            },
            "mood_flavored": {
                "type": "boolean",
                "description": "True when the rejection is about today's mood/energy, not the suggestion class.",
            },
        },
        "required": ["rule_class"],
    },
}


def _handle_suggestion_rule_save(args: dict[str, Any], **_kwargs: Any) -> str:
    try:
        from agent.suggestion_gate import active_profile_state_dir, save_rejection

        state_dir = active_profile_state_dir()
        if state_dir is None:
            return _error("profile state directory unavailable")
        out = save_rejection(
            state_dir,
            str(args.get("rule_class") or ""),
            reason=str(args.get("reason") or ""),
            mood_flavored=bool(args.get("mood_flavored")),
        )
        return _result(out)
    except Exception as exc:  # pragma: no cover - defensive tool boundary
        return _error(exc)


for _name, _schema, _handler in (
    ("personal_assistant_get_state", GET_STATE_SCHEMA, _handle_get_state),
    (
        "personal_assistant_reconcile_inventory",
        RECONCILE_INVENTORY_SCHEMA,
        _handle_reconcile_inventory,
    ),
    ("personal_assistant_propose_capture", PROPOSE_CAPTURE_SCHEMA, _handle_propose_capture),
    ("personal_assistant_state_change", STATE_CHANGE_SCHEMA, _handle_state_change),
    ("suggestion_rule_save", SUGGESTION_RULE_SAVE_SCHEMA, _handle_suggestion_rule_save),
):
    registry.register(
        name=_name,
        toolset="personal_assistant",
        schema=_schema,
        handler=_handler,
        check_fn=_check_office_work_profile,
    )
