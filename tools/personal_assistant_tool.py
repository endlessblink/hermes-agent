"""Primitive tools for the persistent office-work personal assistant state."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agent.personal_assistant_state import (
    PersonalAssistantStateStore,
    StateVersionConflict,
    _apply_operation,
    _interview_planning_date,
    interview_question_order,
    public_state,
)
from agent.personal_assistant_calendar_gate import build_calendar_preflight_receipt
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
    projector = None
    if (raw.get("memory") or {}).get("reliable_memory_enabled", False):
        from agent.personal_assistant_memory import PersonalAssistantMemoryProjector
        from agent.reliable_memory import ReliableMemoryRepository

        projector = PersonalAssistantMemoryProjector(
            ReliableMemoryRepository.from_profile(raw)
        )
    return PersonalAssistantStateService(
        store,
        PersonalAssistantObsidianAdapter(raw),
        projector,
    )


def _result(payload: dict[str, Any]) -> str:
    return json.dumps({"result": payload}, ensure_ascii=False)


def _error(exc: Exception | str) -> str:
    return tool_error(str(exc))


def _handle_get_state(args: dict, **kwargs) -> str:
    try:
        store = _store()
        service = _service(store)
        state = service.get() if service is not None else store.read()
        public = public_state(state)
        if args.get("mode") != "full":
            public = {
                key: public.get(key)
                for key in (
                    "version", "focus", "capacity", "outcomes", "commitments",
                    "blockers", "pendingApprovals", "planningInterview",
                    "taskSourceManifest",
                )
                if key in public
            }
        return _result({"state": public, "mode": args.get("mode") or "compact"})
    except Exception as exc:
        return _error(exc)


def _handle_calendar_preflight(args: dict, **kwargs) -> str:
    """Read complete Calendar coverage and persist its typed freshness receipt."""

    try:
        receipt = build_calendar_preflight_receipt(
            start_date=str(args.get("startDate") or ""),
            end_date=str(args.get("endDate") or ""),
            timezone_name=str(args.get("timezone") or "Asia/Jerusalem"),
        )
        store = _store()

        def remember(state: dict[str, Any]) -> None:
            state["calendar_preflight_receipt"] = receipt

        store.update(remember)
        return _result({"receipt": receipt})
    except Exception as exc:
        return _error(exc)


def _snapshot_without_capture_metadata(snapshot: Any) -> Any:
    """Drop timestamps that change on every re-read but carry no planning meaning."""
    if not isinstance(snapshot, dict):
        return snapshot
    stripped = {
        key: _snapshot_without_capture_metadata(value)
        for key, value in snapshot.items()
        if key not in {"capturedAt", "captured_at", "fetchedAt", "generatedAt"}
    }
    return stripped


def _snapshot_materially_changed(current: Any, incoming: Any) -> bool:
    """Only a real content change is worth a new interview revision.

    Re-running the calendar preflight always produces a fresh capture timestamp.
    Persisting that bumps the interview version underneath a card the user is
    still answering, which turns their next answer into a version conflict.
    """
    return _snapshot_without_capture_metadata(
        current
    ) != _snapshot_without_capture_metadata(incoming)


def _handle_interview_start(args: dict, **kwargs) -> str:
    """Start the durable cross-client planning interview after source discovery."""
    try:
        store = _store()
        requested_interview_id = str(args.get("interviewId") or "").strip()
        active = store.get_planning_interview()
        request_id = str(args.get("requestId") or "").strip()
        planning_date = str(args.get("planningDate") or "").strip()
        mode = str(args.get("mode") or "daily-grounding").strip()
        local_today = datetime.now(ZoneInfo("Asia/Jerusalem")).date()
        try:
            parsed_planning_date = date.fromisoformat(planning_date) if planning_date else local_today
        except ValueError:
            parsed_planning_date = local_today
        future_day = mode == "daily-grounding" and parsed_planning_date > local_today
        is_exact_replay = bool(
            active
            and active.get("interviewId") == requested_interview_id
            and any(
                receipt.get("requestId") == request_id
                for receipt in active.get("requestReceipts") or []
            )
        )
        if (
            active is not None
            and not is_exact_replay
            and (
                (not planning_date or _interview_planning_date(active) == planning_date)
                and str(active.get("mode") or "task-review") == mode
                and (not future_day or interview_question_order(active) == ("availability",))
            )
        ):
            incoming_snapshot = args.get("sourceSnapshot")
            snapshot_refreshed = False
            if (
                isinstance(incoming_snapshot, dict)
                and incoming_snapshot
                and _snapshot_materially_changed(
                    active.get("sourceSnapshot"), incoming_snapshot
                )
            ):
                refreshed = store.patch_planning_interview(
                    interview_id=str(active.get("interviewId") or ""),
                    expected_revision=int(active.get("interviewRevision") or 0),
                    request_id=request_id,
                    operations=[
                        {
                            "op": "refresh-source-snapshot",
                            "sourceSnapshot": incoming_snapshot,
                        }
                    ],
                )
                active = refreshed["interview"]
                snapshot_refreshed = True
            return _result(
                {
                    "interview": active,
                    "resumed": True,
                    "sourceSnapshotRefreshed": snapshot_refreshed,
                    "requestedInterviewId": requested_interview_id,
                }
            )
        result = store.patch_planning_interview(
            interview_id=requested_interview_id,
            expected_revision=0,
            request_id=request_id,
            operations=[
                {
                    "op": "start",
                    "sourceSnapshot": args.get("sourceSnapshot") or {},
                    "tasks": (
                        [
                            {
                                "taskId": "day-context",
                                "title": "תכנון מחר" if future_day else "תכנון שאר היום",
                            }
                        ]
                        if mode == "daily-grounding"
                        else args.get("tasks")
                    ),
                    "planningDate": planning_date or None,
                    **({"questionOrder": ["availability"]} if future_day else {}),
                    "mode": mode,
                }
            ],
        )
        return _result(result)
    except Exception as exc:
        return _error(exc)


def _proposal_id(section: str, title: str, evidence: str, source: str) -> str:
    canonical = json.dumps(
        [section, title, evidence, source], ensure_ascii=False, separators=(",", ":")
    )
    return "capture-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


_CAPTURE_TITLE_STOPWORDS = {
    "a", "as", "block", "is", "task", "the",
    "המשימה", "מתוכננת", "כבלוק", "של",
}


def _capture_title_tokens(title: object) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w+]+", str(title or "").casefold())
        if token not in _CAPTURE_TITLE_STOPWORDS
    }


def _equivalent_capture_proposal(existing: object, proposed: dict[str, Any]) -> bool:
    if not isinstance(existing, dict) or existing.get("section") != proposed["section"]:
        return False
    existing_title = str(existing.get("title") or "")
    proposed_title = proposed["title"]
    if re.findall(r"\d+(?:[.:]\d+)?", existing_title) != re.findall(
        r"\d+(?:[.:]\d+)?", proposed_title
    ):
        return False
    existing_tokens = _capture_title_tokens(existing_title)
    proposed_tokens = _capture_title_tokens(proposed_title)
    union = existing_tokens | proposed_tokens
    return bool(union) and len(existing_tokens & proposed_tokens) / len(union) >= 0.7


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
        current_state = store.read()
        equivalent_proposals = [
            item
            for item in current_state.get("captureProposals", [])
            if isinstance(item, dict)
            and (
                item.get("id") == proposal_id
                or _equivalent_capture_proposal(item, proposal)
            )
        ]
        if len(equivalent_proposals) == 1:
            return _result(
                {
                    "proposal": equivalent_proposals[0],
                    "stateVersion": current_state.get("version", 0),
                }
            )
        captured = proposal

        def mutate(state: dict[str, Any]) -> None:
            nonlocal captured
            proposals = state.setdefault("captureProposals", [])
            existing = next(
                (
                    item
                    for item in proposals
                    if isinstance(item, dict)
                    and (
                        item.get("id") == proposal_id
                        or _equivalent_capture_proposal(item, proposal)
                    )
                ),
                None,
            )
            if existing is not None:
                captured = dict(existing)
                kept_id = existing.get("id")
                proposals[:] = [
                    item
                    for item in proposals
                    if item is existing
                    or not (
                        isinstance(item, dict)
                        and item.get("id") != kept_id
                        and _equivalent_capture_proposal(item, proposal)
                    )
                ]
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


def _stable_safety_scope_fingerprint(value: Any) -> str:
    fingerprint = str(value or "")
    return re.sub(
        r"(?P<prefix>(?:^|\|)calendar:)(?P<date>\d{4}-\d{2}-\d{2})T[^|]+",
        r"\g<prefix>\g<date>",
        fingerprint,
    )


def _handle_safety_review(args: dict, **kwargs) -> str:
    """Persist the protected scope and proof that the current review covered it."""

    try:
        store = _store()
        state = store.read()
        configured_source_names = {
            str(source.get("id") or "").strip()
            for source in state.get("task_source_manifest") or []
            if isinstance(source, dict) and str(source.get("id") or "").strip()
        }
        submitted_source_names = {
            str(source.get("id") or "").strip()
            for source in args.get("sources") or []
            if isinstance(source, dict) and str(source.get("id") or "").strip()
        }
        missing_source_names = sorted(configured_source_names - submitted_source_names)
        if missing_source_names:
            raise ValueError(
                "coverage sources must use the exact configured source names: "
                f"{', '.join(missing_source_names)}; do not invent aliases"
            )
        protected_items = [
            dict(item) if isinstance(item, dict) else item
            for item in args.get("protectedItems") or []
        ]
        default_next_review = (
            datetime.now(ZoneInfo("Asia/Jerusalem")).date() + timedelta(days=1)
        ).isoformat()
        context_item_ids = []
        for item in protected_items:
            if (
                isinstance(item, dict)
                and item.get("disposition") == "needs_context"
                and item.get("missingFields")
            ):
                item.setdefault("nextReviewAt", default_next_review)
                item_id = str(item.get("id") or "").strip()
                if item_id:
                    context_item_ids.append(item_id)
        reviewed_item_ids = list(args.get("reviewedItemIds") or [])
        risk_item_ids = list(args.get("riskItemIds") or [])
        unresolved_item_ids = list(
            dict.fromkeys([*(args.get("unresolvedItemIds") or []), *context_item_ids])
        )
        reused_prior_review = False
        if args.get("reusePriorReview") is True:
            current_sources = sorted(
                (
                    str(source.get("id") or "").strip(),
                    str(source.get("status") or "").strip(),
                    source.get("revision"),
                )
                for source in args.get("sources") or []
                if isinstance(source, dict)
            )
            for prior in reversed(state.get("coverage_receipts") or []):
                if not isinstance(prior, dict) or prior.get("complete") is not True:
                    continue
                prior_sources = sorted(
                    (
                        str(source.get("id") or "").strip(),
                        str(source.get("status") or "").strip(),
                        source.get("revision"),
                    )
                    for source in prior.get("sources") or []
                    if isinstance(source, dict)
                )
                if (
                    str(prior.get("cadence") or "") == str(args.get("cadence") or "")
                    and _stable_safety_scope_fingerprint(prior.get("scopeFingerprint"))
                    == _stable_safety_scope_fingerprint(args.get("scopeFingerprint"))
                    and prior_sources == current_sources
                    and all(status == "fresh" for _, status, _ in current_sources)
                ):
                    reviewed_item_ids = list(
                        dict.fromkeys(
                            [*(prior.get("reviewedItemIds") or []), *reviewed_item_ids]
                        )
                    )
                    risk_item_ids = list(
                        dict.fromkeys([*(prior.get("riskItemIds") or []), *risk_item_ids])
                    )
                    unresolved_item_ids = list(
                        dict.fromkeys(
                            [*(prior.get("unresolvedItemIds") or []), *unresolved_item_ids]
                        )
                    )
                    reused_prior_review = True
                    break
        state, receipt = store.record_safety_review(
            protected_items=protected_items,
            cadence=str(args.get("cadence") or ""),
            scope_fingerprint=str(args.get("scopeFingerprint") or ""),
            sources=args.get("sources"),
            reviewed_item_ids=reviewed_item_ids,
            risk_item_ids=risk_item_ids,
            unresolved_item_ids=unresolved_item_ids,
        )
        return _result(
            {
                "receipt": receipt,
                "reusedPriorReview": reused_prior_review,
                "stateVersion": state["version"],
            }
        )
    except Exception as exc:
        return _error(exc)


GET_STATE_SCHEMA = {
    "name": "personal_assistant_get_state",
    "description": "Read the persistent office-work assistant's current working picture and pending decisions.",
    "parameters": {
        "type": "object",
        "properties": {"mode": {"type": "string", "enum": ["compact", "full"]}},
        "required": [],
    },
}

CALENDAR_PREFLIGHT_SCHEMA = {
    "name": "personal_assistant_calendar_preflight",
    "description": (
        "Mandatory first step before Personal Assistant planning. Read every accessible "
        "Google Calendar, including paginated calendars and events, for an exact local-date range."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "startDate": {
                "type": "string",
                "format": "date",
                "description": "First local date to check, inclusive (YYYY-MM-DD).",
            },
            "endDate": {
                "type": "string",
                "format": "date",
                "description": (
                    "First local date after the range, exclusive (YYYY-MM-DD). "
                    "For one day, use the following date; an equal date is normalized to one day."
                ),
            },
            "timezone": {
                "type": "string",
                "default": "Asia/Jerusalem",
                "description": "IANA timezone for the exact planning range.",
            },
        },
        "required": ["startDate", "endDate"],
        "additionalProperties": False,
    },
}

INTERVIEW_START_SCHEMA = {
    "name": "personal_assistant_interview_start",
    "description": (
        "Start one durable same-day grounding interview before source discovery, or an explicit task review. "
        "The default daily-grounding mode asks only about today's energy, stopping time, commitments, and location. "
        "Call this before presenting the first task-profile-review card. If a matching interview "
        "is already active, this resumes it instead of starting a competing workflow. "
        "Desktop and Telegram then commit every answer to this same versioned interview."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "interviewId": {"type": "string", "minLength": 1, "maxLength": 300},
            "requestId": {"type": "string", "minLength": 1, "maxLength": 300},
            "planningDate": {"type": "string", "format": "date"},
            "mode": {
                "type": "string",
                "enum": ["daily-grounding", "task-review"],
                "default": "daily-grounding",
            },
            "sourceSnapshot": {"type": "object"},
            "tasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": 100,
                "items": {
                    "type": "object",
                    "properties": {
                        "taskId": {"type": "string", "minLength": 1, "maxLength": 300},
                        "title": {"type": "string", "minLength": 1, "maxLength": 1000},
                    },
                    "required": ["taskId", "title"],
                },
            },
        },
        "required": ["interviewId", "requestId", "planningDate", "sourceSnapshot", "tasks"],
    },
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
                "description": (
                    "Coverage sources. For every configured task source, copy its exact id from "
                    "taskSourceManifest in personal_assistant_get_state; never invent an alias."
                ),
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

SAFETY_REVIEW_SCHEMA = {
    "name": "personal_assistant_safety_review",
    "description": (
        "Atomically persist the protected active-project and commitment scope plus a coverage "
        "receipt. Use this after fresh source reads and before giving a daily or weekly plan. "
        "The result can be all-clear only when every protected item was reviewed and every "
        "source was fresh. Existing active protected items omitted from the review remain visible."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "cadence": {"type": "string", "enum": ["daily", "weekly"]},
            "scopeFingerprint": {"type": "string", "minLength": 1, "maxLength": 500},
            "reusePriorReview": {
                "type": "boolean",
                "description": (
                    "Set true on repeated planning turns. Hermes reuses the prior exact protected-item "
                    "review only when cadence, scope fingerprint, every source revision, and fresh status "
                    "match exactly; any source change fails closed and requires current exact item reads."
                ),
            },
            "sources": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "description": (
                    "Include every configured source and copy each id exactly from "
                    "taskSourceManifest returned by personal_assistant_get_state. "
                    "Never invent, shorten, or alias a source id."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "minLength": 1, "maxLength": 200},
                        "status": {
                            "type": "string",
                            "enum": ["fresh", "partial", "stale", "unavailable"],
                        },
                        "revision": {"type": ["string", "null"], "maxLength": 300},
                    },
                    "required": ["id", "status"],
                },
            },
            "protectedItems": {
                "type": "array",
                "maxItems": 500,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "minLength": 1, "maxLength": 300},
                        "source": {"type": "string", "minLength": 1, "maxLength": 100},
                        "sourceId": {"type": "string", "minLength": 1, "maxLength": 300},
                        "kind": {"type": "string", "enum": ["project", "commitment"]},
                        "title": {"type": "string", "minLength": 1, "maxLength": 1000},
                        "consequence": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "disposition": {
                            "type": "string",
                            "enum": [
                                "actionable", "waiting", "deferred", "needs_context",
                                "completed", "cancelled",
                            ],
                        },
                        "nextAction": {"type": ["string", "null"], "maxLength": 2000},
                        "dependencyIds": {
                            "type": "array", "maxItems": 64, "items": {"type": "string"}
                        },
                        "missingFields": {
                            "type": "array", "maxItems": 64, "items": {"type": "string"}
                        },
                        "deferralReason": {"type": ["string", "null"], "maxLength": 2000},
                        "deadline": {
                            "type": ["string", "null"],
                            "description": (
                                "ISO timestamp with timezone, or YYYY-MM-DD task date. Never send a datetime "
                                "without Z or a numeric UTC offset. Prefer YYYY-MM-DD when no time is needed."
                            ),
                        },
                        "nextReviewAt": {
                            "type": ["string", "null"],
                            "description": (
                                "ISO timestamp with timezone, or YYYY-MM-DD local review date. Never send a datetime "
                                "without Z or a numeric UTC offset. Prefer YYYY-MM-DD when no time is needed."
                            ),
                        },
                        "sourceRevision": {"type": ["string", "null"], "maxLength": 300},
                        "verifiedAt": {"type": ["string", "null"]},
                    },
                    "required": [
                        "id", "source", "sourceId", "kind", "title", "consequence",
                        "disposition",
                    ],
                },
            },
            "reviewedItemIds": {
                "type": "array", "maxItems": 500, "items": {"type": "string"}
            },
            "riskItemIds": {
                "type": "array", "maxItems": 500, "items": {"type": "string"}
            },
            "unresolvedItemIds": {
                "type": "array", "maxItems": 500, "items": {"type": "string"}
            },
        },
        "required": [
            "cadence", "scopeFingerprint", "sources", "protectedItems",
            "reviewedItemIds", "riskItemIds", "unresolvedItemIds",
        ],
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
        "personal_assistant_calendar_preflight",
        CALENDAR_PREFLIGHT_SCHEMA,
        _handle_calendar_preflight,
    ),
    (
        "personal_assistant_interview_start",
        INTERVIEW_START_SCHEMA,
        _handle_interview_start,
    ),
    (
        "personal_assistant_reconcile_inventory",
        RECONCILE_INVENTORY_SCHEMA,
        _handle_reconcile_inventory,
    ),
    (
        "personal_assistant_safety_review",
        SAFETY_REVIEW_SCHEMA,
        _handle_safety_review,
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
