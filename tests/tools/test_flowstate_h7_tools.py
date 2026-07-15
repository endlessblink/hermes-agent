"""Contract tests for Hermes' protected FlowState H7 tool surface."""

import json

import pytest

from tools import flowstate_tool as fst
from tools import flowstate_h7_tool as h7
from tools.flowstate_receipts import canonical_json_hash
from tools.registry import registry


TASK_ID = "11111111-1111-4111-8111-111111111111"
SESSION_ID = "22222222-2222-4222-8222-222222222222"
PROJECT_ID = "33333333-3333-4333-8333-333333333333"
GROUP_ID = "44444444-4444-4444-8444-444444444444"
HISTORY_ID = "55555555-5555-4555-8555-555555555555"
OPERATION_ID = "hermes:h7:stable-operation"
REQUEST_HASH = "a" * 64
PREVIEW_DIGEST = "b" * 64
PREVIEW_EXPIRES_AT = "2026-07-16T12:15:00.000Z"
UPDATED_AT = "2026-07-16T12:01:00.000Z"
COMMITTED_AT = "2026-07-16T12:01:00.010Z"


def _capability(capability_id, *, contract="task-v1", mode="write"):
    return {
        "id": capability_id,
        "mode": mode,
        "approval": "canonical_preview_apply" if mode == "write" else "none",
        "scope": "personal_or_active_workspace",
        "contractVersion": contract,
        "receiptVersion": "canonical-receipt-v1" if mode == "write" else None,
    }


def _manifest(*ids):
    capabilities = []
    for capability_id in sorted(ids):
        contract = (
            "timer-v1"
            if capability_id.startswith("timer.")
            else (
                "organization-inventory-v1"
                if capability_id == "organization.inventory"
                else "task-v1"
            )
        )
        mode = (
            "read"
            if capability_id
            in {
                "recurrence.chain",
                "timer.session",
                "organization.inventory",
            }
            else "write"
        )
        capabilities.append(_capability(capability_id, contract=contract, mode=mode))
    return {
        "manifestVersion": "assistant-capabilities-v1",
        "capabilities": capabilities,
    }


def _request_sequence(monkeypatch, responses):
    seen = []
    queue = list(responses)

    def request(method, path, body=None, **kwargs):
        seen.append((method, path, body, kwargs))
        return queue.pop(0)

    monkeypatch.setattr(fst, "_request", request)
    return seen


def _chain(**overrides):
    value = {
        "ok": True,
        "fresh": True,
        "contractVersion": "task-v1",
        "seriesId": TASK_ID,
        "workspaceId": None,
        "lifecycleStatus": "active",
        "definition": {
            "pattern": "weekly",
            "interval": 1,
            "weekdays": [1, 4],
            "endType": "never",
        },
        "seriesRevision": 7,
        "history": [
            {
                "id": HISTORY_ID,
                "recurrenceCount": 1,
                "dueDate": "2026-07-09",
                "status": "done",
                "completedAt": "2026-07-09T08:00:00.000Z",
                "canonicalRevision": 3,
                "canonicalUpdatedAt": "2026-07-09T08:00:00.000Z",
            }
        ],
        "currentOccurrence": {
            "id": TASK_ID,
            "recurrenceCount": 2,
            "dueDate": "2026-07-16",
            "status": "todo",
            "canonicalRevision": 7,
            "canonicalUpdatedAt": "2026-07-16T08:00:00.000Z",
        },
        "nextOccurrence": {"dueDate": "2026-07-20", "recurrenceCount": 3},
    }
    value.update(overrides)
    if value.get("currentOccurrence"):
        value["currentOccurrence"] = {
            **value["currentOccurrence"],
            "canonicalRevision": value["seriesRevision"],
        }
    value["id"] = value["seriesId"]
    value["canonicalRevision"] = value["seriesRevision"]
    value["canonicalUpdatedAt"] = (
        value["currentOccurrence"]["canonicalUpdatedAt"]
        if value.get("currentOccurrence")
        else UPDATED_AT
    )
    return value


def _task_receipt(*, action, read_back, operation_context, entity_id=TASK_ID):
    entity_read_back = (
        {
            "id": entity_id,
            "canonicalRevision": read_back["canonicalRevision"],
            "canonicalUpdatedAt": read_back["canonicalUpdatedAt"],
        }
        if action.startswith("recurrence_")
        else read_back
    )
    affected = [
        {
            "entityType": "task",
            "entityId": entity_id,
            "action": "update",
            "canonicalRevision": read_back["canonicalRevision"],
            "changeSequence": 71,
            "readBack": entity_read_back,
            "readBackHash": canonical_json_hash(entity_read_back),
        }
    ]
    return {
        "ok": True,
        "result": "committed",
        "action": action,
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "receipt": {
            "ok": True,
            "status": "committed",
            "replayed": False,
            "operationId": OPERATION_ID,
            "requestHash": REQUEST_HASH,
            "contractVersion": "task-v1",
            "source": "local-api",
            "entityType": "task",
            "action": action,
            "entityId": entity_id,
            "canonicalRevision": read_back["canonicalRevision"],
            "canonicalUpdatedAt": read_back["canonicalUpdatedAt"],
            "changeSequence": 71,
            "committedAt": COMMITTED_AT,
            "readBack": read_back,
            "readBackHash": canonical_json_hash(read_back),
            "affected": affected,
            "operationContext": operation_context,
        },
    }


def _timer_state(**overrides):
    state = {
        "id": SESSION_ID,
        "workspaceId": None,
        "taskId": TASK_ID,
        "startTime": "2026-07-16T07:00:00.000Z",
        "duration": 1500,
        "remainingTime": 1500,
        "isActive": True,
        "isPaused": False,
        "isBreak": False,
        "completedAt": None,
        "deviceLeaderId": "hermes-office",
        "canonicalRevision": 1,
        "canonicalUpdatedAt": UPDATED_AT,
    }
    state.update(overrides)
    return state


def _timer_receipt(state, *, action="start"):
    affected = [
        {
            "entityType": "timer_session",
            "entityId": SESSION_ID,
            "action": "inserted" if action == "start" else "updated",
            "canonicalRevision": state["canonicalRevision"],
            "changeSequence": 81,
            "readBack": state,
            "readBackHash": canonical_json_hash(state),
        }
    ]
    return {
        "ok": True,
        "result": "committed",
        "action": action,
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "receipt": {
            "ok": True,
            "status": "committed",
            "replayed": False,
            "operationId": OPERATION_ID,
            "requestHash": REQUEST_HASH,
            "contractVersion": "timer-v1",
            "source": "local-api",
            "entityType": "timer_session",
            "entityId": SESSION_ID,
            "action": action,
            "canonicalRevision": state["canonicalRevision"],
            "canonicalUpdatedAt": state["canonicalUpdatedAt"],
            "changeSequence": 81,
            "committedAt": COMMITTED_AT,
            "readBack": state,
            "readBackHash": canonical_json_hash(state),
            "affected": affected,
            "operationContext": {"replacedSessionIds": []},
        },
    }


def test_capability_manifest_is_exact_versioned_and_duplicate_free(monkeypatch):
    payload = _manifest("recurrence.chain", "timer.start", "organization.inventory")
    _request_sequence(monkeypatch, [payload])

    result = json.loads(h7._handle_flowstate_capabilities({}))

    assert result["result"] == payload


@pytest.mark.parametrize("mutation", ["duplicate", "malformed", "wrong_contract"])
def test_capability_manifest_fails_closed_when_not_authoritative(monkeypatch, mutation):
    payload = _manifest("timer.start")
    if mutation == "duplicate":
        payload["capabilities"].append(dict(payload["capabilities"][0]))
    elif mutation == "malformed":
        payload["capabilities"][0]["approval"] = "direct_write"
    else:
        payload["capabilities"][0]["contractVersion"] = "task-v1"
    _request_sequence(monkeypatch, [payload])

    result = json.loads(h7._handle_flowstate_capabilities({}))

    assert result["error"] == "FlowState capability manifest could not be verified"


def test_recurrence_chain_reads_one_exact_id_and_validates_history(monkeypatch):
    payload = _chain()
    seen = _request_sequence(monkeypatch, [_manifest("recurrence.chain"), payload])

    result = json.loads(h7._handle_recurrence_chain({"taskId": TASK_ID}))

    assert result["result"] == payload
    assert seen[-1][:2] == ("GET", f"/api/tasks/{TASK_ID}/recurrence")


def test_recurrence_chain_accepts_an_occurrence_id_for_a_distinct_series_root(
    monkeypatch,
):
    payload = _chain()
    _request_sequence(monkeypatch, [_manifest("recurrence.chain"), payload])

    result = json.loads(h7._handle_recurrence_chain({"taskId": HISTORY_ID}))

    assert result["result"]["id"] == TASK_ID
    assert result["result"]["currentOccurrence"]["id"] == TASK_ID


def test_recurrence_chain_rejects_ambiguous_duplicate_history(monkeypatch):
    first = _chain()["history"][0]
    payload = _chain(
        history=[first, {**first, "id": "66666666-6666-4666-8666-666666666666"}]
    )
    _request_sequence(monkeypatch, [_manifest("recurrence.chain"), payload])

    result = json.loads(h7._handle_recurrence_chain({"taskId": TASK_ID}))

    assert result["error"] == "Canonical recurrence chain could not be verified"


@pytest.mark.parametrize("mismatch", ["revision", "updated_at"])
def test_recurrence_chain_rejects_top_state_not_bound_to_current(monkeypatch, mismatch):
    payload = _chain()
    if mismatch == "revision":
        payload["currentOccurrence"]["canonicalRevision"] = 6
    else:
        payload["canonicalUpdatedAt"] = "2026-07-16T08:00:01.000Z"
    _request_sequence(monkeypatch, [_manifest("recurrence.chain"), payload])

    result = json.loads(h7._handle_recurrence_chain({"taskId": TASK_ID}))

    assert result["error"] == "Canonical recurrence chain could not be verified"


def test_recurrence_preview_binds_operation_scope_action_and_definition(monkeypatch):
    rule = {"pattern": "weekly", "interval": 1, "weekdays": [1, 4], "endType": "never"}
    preview = {
        "ok": True,
        "result": "preview",
        "preview": True,
        "contractVersion": "task-v1",
        "action": "recurrence_edit_future",
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "previewDigest": PREVIEW_DIGEST,
        "previewExpiresAt": PREVIEW_EXPIRES_AT,
        "seriesId": TASK_ID,
        "workspaceId": None,
        "baseRevision": 7,
        "normalizedPayload": {
            "action": "edit_future",
            "recurrenceRule": rule,
            "nextDueDate": "2026-07-20",
        },
        "readBack": _chain(
            definition=rule,
            currentOccurrence={
                **_chain()["currentOccurrence"],
                "dueDate": "2026-07-20",
            },
            nextOccurrence={"dueDate": "2026-07-23", "recurrenceCount": 3},
        ),
    }
    seen = _request_sequence(
        monkeypatch, [_manifest("recurrence.edit_future"), preview]
    )
    args = {
        "operationId": OPERATION_ID,
        "taskId": TASK_ID,
        "action": "edit_future",
        "baseRevision": 7,
        "timeZone": "Asia/Jerusalem",
        "recurrenceRule": rule,
        "nextDueDate": "2026-07-20",
    }

    result = json.loads(h7._handle_recurrence_command(args))

    assert result["result"] == preview
    assert seen[-1] == (
        "POST",
        f"/api/tasks/{TASK_ID}/recurrence",
        {**args, "preview": True},
        {"allow_stale_cache": False},
    )


def test_recurrence_preview_accepts_history_member_and_canonical_series_root(
    monkeypatch,
):
    preview = {
        "ok": True,
        "result": "preview",
        "preview": True,
        "contractVersion": "task-v1",
        "action": "recurrence_pause",
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "previewDigest": PREVIEW_DIGEST,
        "previewExpiresAt": PREVIEW_EXPIRES_AT,
        "seriesId": TASK_ID,
        "workspaceId": None,
        "baseRevision": 7,
        "normalizedPayload": {
            "action": "pause",
            "recurrenceRule": None,
            "nextDueDate": None,
        },
        "readBack": _chain(lifecycleStatus="paused", nextOccurrence=None),
    }
    _request_sequence(monkeypatch, [_manifest("recurrence.pause"), preview])

    result = json.loads(
        h7._handle_recurrence_command({
            "operationId": OPERATION_ID,
            "taskId": HISTORY_ID,
            "action": "pause",
            "baseRevision": 7,
            "timeZone": "Asia/Jerusalem",
        })
    )

    assert result["result"] == preview


def test_end_series_preview_accepts_ended_projection_with_living_current(
    monkeypatch,
):
    ended = _chain(
        lifecycleStatus="ended",
        definition=None,
        nextOccurrence=None,
    )
    preview = {
        "ok": True,
        "result": "preview",
        "preview": True,
        "contractVersion": "task-v1",
        "action": "recurrence_end_series",
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "previewDigest": PREVIEW_DIGEST,
        "previewExpiresAt": PREVIEW_EXPIRES_AT,
        "seriesId": TASK_ID,
        "workspaceId": None,
        "baseRevision": 7,
        "normalizedPayload": {
            "action": "end_series",
            "recurrenceRule": None,
            "nextDueDate": None,
        },
        "readBack": ended,
    }
    _request_sequence(monkeypatch, [_manifest("recurrence.end_series"), preview])

    result = json.loads(
        h7._handle_recurrence_command({
            "operationId": OPERATION_ID,
            "taskId": TASK_ID,
            "action": "end_series",
            "baseRevision": 7,
            "timeZone": "Asia/Jerusalem",
        })
    )

    assert result["result"] == preview


def test_recurrence_apply_validates_exact_receipt_and_preserved_history(monkeypatch):
    read_back = _chain(
        seriesRevision=8,
        lifecycleStatus="paused",
        nextOccurrence=None,
    )
    payload = _task_receipt(
        action="recurrence_pause",
        read_back=read_back,
        operation_context={
            "action": "recurrence_pause",
            "seriesId": TASK_ID,
            "requestedTaskId": TASK_ID,
            "currentTaskId": TASK_ID,
            "timeZone": "Asia/Jerusalem",
            "recurrenceRule": None,
            "nextDueDate": None,
            "workspaceId": None,
        },
    )
    _request_sequence(monkeypatch, [_manifest("recurrence.pause"), payload])

    result = json.loads(
        h7._handle_recurrence_command({
            "operationId": OPERATION_ID,
            "taskId": TASK_ID,
            "action": "pause",
            "baseRevision": 7,
            "timeZone": "Asia/Jerusalem",
            "preview": False,
            "previewDigest": PREVIEW_DIGEST,
            "previewExpiresAt": PREVIEW_EXPIRES_AT,
            "requestHash": REQUEST_HASH,
        })
    )

    assert result["result"] == payload
    assert result["result"]["receipt"]["readBack"]["history"] == read_back["history"]


def test_recurrence_apply_accepts_history_request_bound_to_living_current(
    monkeypatch,
):
    read_back = _chain(
        seriesRevision=8,
        lifecycleStatus="paused",
        nextOccurrence=None,
    )
    payload = _task_receipt(
        action="recurrence_pause",
        read_back=read_back,
        entity_id=TASK_ID,
        operation_context={
            "action": "recurrence_pause",
            "seriesId": TASK_ID,
            "requestedTaskId": HISTORY_ID,
            "currentTaskId": TASK_ID,
            "timeZone": "Asia/Jerusalem",
            "recurrenceRule": None,
            "nextDueDate": None,
            "workspaceId": None,
        },
    )
    _request_sequence(monkeypatch, [_manifest("recurrence.pause"), payload])

    result = json.loads(
        h7._handle_recurrence_command({
            "operationId": OPERATION_ID,
            "taskId": HISTORY_ID,
            "action": "pause",
            "baseRevision": 7,
            "timeZone": "Asia/Jerusalem",
            "preview": False,
            "previewDigest": PREVIEW_DIGEST,
            "previewExpiresAt": PREVIEW_EXPIRES_AT,
            "requestHash": REQUEST_HASH,
        })
    )

    assert result["result"] == payload


@pytest.mark.parametrize("binding", ["requested", "current", "entity"])
def test_recurrence_apply_rejects_history_request_with_wrong_identity_binding(
    monkeypatch, binding
):
    read_back = _chain(
        seriesRevision=8,
        lifecycleStatus="paused",
        nextOccurrence=None,
    )
    context = {
        "action": "recurrence_pause",
        "seriesId": TASK_ID,
        "requestedTaskId": HISTORY_ID,
        "currentTaskId": TASK_ID,
        "timeZone": "Asia/Jerusalem",
        "recurrenceRule": None,
        "nextDueDate": None,
        "workspaceId": None,
    }
    if binding == "requested":
        context["requestedTaskId"] = TASK_ID
    elif binding == "current":
        context["currentTaskId"] = HISTORY_ID
    payload = _task_receipt(
        action="recurrence_pause",
        read_back=read_back,
        entity_id=HISTORY_ID if binding == "entity" else TASK_ID,
        operation_context=context,
    )
    _request_sequence(monkeypatch, [_manifest("recurrence.pause"), payload])

    result = json.loads(
        h7._handle_recurrence_command({
            "operationId": OPERATION_ID,
            "taskId": HISTORY_ID,
            "action": "pause",
            "baseRevision": 7,
            "timeZone": "Asia/Jerusalem",
            "preview": False,
            "previewDigest": PREVIEW_DIGEST,
            "previewExpiresAt": PREVIEW_EXPIRES_AT,
            "requestHash": REQUEST_HASH,
        })
    )

    assert result["error"] == "Canonical recurrence receipt could not be verified"


def test_end_series_apply_accepts_ended_projection_and_task_affected_row(monkeypatch):
    read_back = _chain(
        seriesRevision=8,
        lifecycleStatus="ended",
        definition=None,
        nextOccurrence=None,
    )
    payload = _task_receipt(
        action="recurrence_end_series",
        read_back=read_back,
        operation_context={
            "action": "recurrence_end_series",
            "seriesId": TASK_ID,
            "requestedTaskId": TASK_ID,
            "currentTaskId": TASK_ID,
            "timeZone": "Asia/Jerusalem",
            "recurrenceRule": None,
            "nextDueDate": None,
            "workspaceId": None,
        },
    )
    _request_sequence(monkeypatch, [_manifest("recurrence.end_series"), payload])

    result = json.loads(
        h7._handle_recurrence_command({
            "operationId": OPERATION_ID,
            "taskId": TASK_ID,
            "action": "end_series",
            "baseRevision": 7,
            "timeZone": "Asia/Jerusalem",
            "preview": False,
            "previewDigest": PREVIEW_DIGEST,
            "previewExpiresAt": PREVIEW_EXPIRES_AT,
            "requestHash": REQUEST_HASH,
        })
    )

    assert result["result"] == payload


def test_edit_future_apply_binds_requested_date_to_current_not_next(monkeypatch):
    rule = {"pattern": "weekly", "interval": 1, "weekdays": [1, 4], "endType": "never"}
    read_back = _chain(
        seriesRevision=8,
        definition=rule,
        currentOccurrence={
            **_chain()["currentOccurrence"],
            "dueDate": "2026-07-20",
            "canonicalRevision": 8,
            "canonicalUpdatedAt": UPDATED_AT,
        },
        nextOccurrence={"dueDate": "2026-07-23", "recurrenceCount": 3},
    )
    payload = _task_receipt(
        action="recurrence_edit_future",
        read_back=read_back,
        operation_context={
            "action": "recurrence_edit_future",
            "seriesId": TASK_ID,
            "requestedTaskId": TASK_ID,
            "currentTaskId": TASK_ID,
            "timeZone": "Asia/Jerusalem",
            "recurrenceRule": rule,
            "nextDueDate": "2026-07-20",
            "workspaceId": None,
        },
    )
    _request_sequence(monkeypatch, [_manifest("recurrence.edit_future"), payload])

    result = json.loads(
        h7._handle_recurrence_command({
            "operationId": OPERATION_ID,
            "taskId": TASK_ID,
            "action": "edit_future",
            "baseRevision": 7,
            "timeZone": "Asia/Jerusalem",
            "recurrenceRule": rule,
            "nextDueDate": "2026-07-20",
            "preview": False,
            "previewDigest": PREVIEW_DIGEST,
            "previewExpiresAt": PREVIEW_EXPIRES_AT,
            "requestHash": REQUEST_HASH,
        })
    )

    assert result["result"] == payload


def test_edit_future_rejects_requested_date_only_reflected_as_next_occurrence(
    monkeypatch,
):
    rule = {"pattern": "weekly", "interval": 1, "weekdays": [1, 4], "endType": "never"}
    preview = {
        "ok": True,
        "result": "preview",
        "preview": True,
        "contractVersion": "task-v1",
        "action": "recurrence_edit_future",
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "previewDigest": PREVIEW_DIGEST,
        "previewExpiresAt": PREVIEW_EXPIRES_AT,
        "seriesId": TASK_ID,
        "workspaceId": None,
        "baseRevision": 7,
        "normalizedPayload": {
            "action": "edit_future",
            "recurrenceRule": rule,
            "nextDueDate": "2026-07-20",
        },
        "readBack": _chain(definition=rule),
    }
    _request_sequence(monkeypatch, [_manifest("recurrence.edit_future"), preview])

    result = json.loads(
        h7._handle_recurrence_command({
            "operationId": OPERATION_ID,
            "taskId": TASK_ID,
            "action": "edit_future",
            "baseRevision": 7,
            "timeZone": "Asia/Jerusalem",
            "recurrenceRule": rule,
            "nextDueDate": "2026-07-20",
        })
    )

    assert result["error"] == "Canonical recurrence preview could not be verified"


def test_recurrence_apply_rejects_forged_rehashed_history(monkeypatch):
    read_back = _chain(
        seriesRevision=8,
        lifecycleStatus="paused",
        nextOccurrence=None,
    )
    payload = _task_receipt(
        action="recurrence_pause",
        read_back=read_back,
        operation_context={
            "action": "recurrence_pause",
            "seriesId": TASK_ID,
            "requestedTaskId": TASK_ID,
            "currentTaskId": TASK_ID,
            "timeZone": "Asia/Jerusalem",
            "recurrenceRule": None,
            "nextDueDate": None,
            "workspaceId": None,
        },
    )
    duplicate = dict(payload["receipt"]["readBack"]["history"][0])
    duplicate["id"] = "66666666-6666-4666-8666-666666666666"
    payload["receipt"]["readBack"]["history"].append(duplicate)
    forged = payload["receipt"]["readBack"]
    payload["receipt"]["readBackHash"] = canonical_json_hash(forged)
    payload["receipt"]["affected"][0]["readBack"] = forged
    payload["receipt"]["affected"][0]["readBackHash"] = canonical_json_hash(forged)
    _request_sequence(monkeypatch, [_manifest("recurrence.pause"), payload])

    result = json.loads(
        h7._handle_recurrence_command({
            "operationId": OPERATION_ID,
            "taskId": TASK_ID,
            "action": "pause",
            "baseRevision": 7,
            "timeZone": "Asia/Jerusalem",
            "preview": False,
            "previewDigest": PREVIEW_DIGEST,
            "previewExpiresAt": PREVIEW_EXPIRES_AT,
            "requestHash": REQUEST_HASH,
        })
    )

    assert result["error"] == "Canonical recurrence receipt could not be verified"


@pytest.mark.parametrize(
    "forgery", ["affected_timestamp", "affected_action", "extra_affected"]
)
def test_recurrence_apply_rejects_forged_affected_task_proof(monkeypatch, forgery):
    read_back = _chain(
        seriesRevision=8,
        lifecycleStatus="paused",
        nextOccurrence=None,
    )
    payload = _task_receipt(
        action="recurrence_pause",
        read_back=read_back,
        operation_context={
            "action": "recurrence_pause",
            "seriesId": TASK_ID,
            "requestedTaskId": TASK_ID,
            "currentTaskId": TASK_ID,
            "timeZone": "Asia/Jerusalem",
            "recurrenceRule": None,
            "nextDueDate": None,
            "workspaceId": None,
        },
    )
    affected = payload["receipt"]["affected"][0]
    if forgery == "affected_timestamp":
        affected["readBack"]["canonicalUpdatedAt"] = "not-a-timestamp"
        affected["readBackHash"] = canonical_json_hash(affected["readBack"])
    elif forgery == "affected_action":
        affected["action"] = "create"
    else:
        extra_read_back = {
            "id": "66666666-6666-4666-8666-666666666666",
            "canonicalRevision": 1,
            "canonicalUpdatedAt": UPDATED_AT,
        }
        payload["receipt"]["affected"].append({
            "entityType": "task",
            "entityId": extra_read_back["id"],
            "action": "update",
            "canonicalRevision": 1,
            "changeSequence": 72,
            "readBack": extra_read_back,
            "readBackHash": canonical_json_hash(extra_read_back),
        })
    _request_sequence(monkeypatch, [_manifest("recurrence.pause"), payload])

    result = json.loads(
        h7._handle_recurrence_command({
            "operationId": OPERATION_ID,
            "taskId": TASK_ID,
            "action": "pause",
            "baseRevision": 7,
            "timeZone": "Asia/Jerusalem",
            "preview": False,
            "previewDigest": PREVIEW_DIGEST,
            "previewExpiresAt": PREVIEW_EXPIRES_AT,
            "requestHash": REQUEST_HASH,
        })
    )

    assert result["error"] == "Canonical recurrence receipt could not be verified"


def test_unsupported_recurrence_capability_stops_before_post(monkeypatch):
    seen = _request_sequence(monkeypatch, [_manifest("recurrence.chain")])

    result = json.loads(
        h7._handle_recurrence_command({
            "operationId": OPERATION_ID,
            "taskId": TASK_ID,
            "action": "pause",
            "baseRevision": 7,
            "timeZone": "Asia/Jerusalem",
        })
    )

    assert result["error"] == "FlowState capability recurrence.pause is not available"
    assert len(seen) == 1


def test_timer_session_reads_and_validates_one_exact_session(monkeypatch):
    session = _timer_state(canonicalRevision=4)
    seen = _request_sequence(
        monkeypatch,
        [_manifest("timer.session"), {"ok": True, "fresh": True, "session": session}],
    )

    result = json.loads(h7._handle_timer_session({"sessionId": SESSION_ID}))

    assert result["result"]["session"]["id"] == SESSION_ID
    assert seen[-1][:2] == ("GET", f"/api/timer/sessions/{SESSION_ID}")


def test_timer_start_preview_binds_identity_scope_and_explicit_state(monkeypatch):
    state = _timer_state()
    normalized = {
        "action": "start",
        "sessionId": SESSION_ID,
        "baseRevision": 0,
        "deviceId": "hermes-office",
        "workspaceId": None,
        "taskId": TASK_ID,
        "startedAt": "2026-07-16T07:00:00.000Z",
        "durationSeconds": 1500,
        "isBreak": False,
        "remainingSeconds": None,
        "extensionSeconds": None,
    }
    preview = {
        "ok": True,
        "result": "preview",
        "contractVersion": "timer-v1",
        "action": "start",
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "previewDigest": PREVIEW_DIGEST,
        "previewExpiresAt": PREVIEW_EXPIRES_AT,
        "normalizedPayload": normalized,
        "readBack": state,
        "replacedSessions": [],
    }
    _request_sequence(monkeypatch, [_manifest("timer.start"), preview])

    result = json.loads(
        h7._handle_timer_command({
            "operationId": OPERATION_ID,
            "action": "start",
            "sessionId": SESSION_ID,
            "baseRevision": 0,
            "deviceId": "hermes-office",
            "taskId": TASK_ID,
            "startedAt": "2026-07-16T07:00:00.000Z",
            "durationSeconds": 1500,
            "isBreak": False,
        })
    )

    assert result["result"] == preview


def test_timer_switch_task_preview_binds_target_remaining_time_and_paused_state(
    monkeypatch,
):
    state = _timer_state(
        taskId="general",
        remainingTime=900,
        isPaused=True,
        canonicalRevision=3,
    )
    normalized = {
        "action": "switch_task",
        "sessionId": SESSION_ID,
        "baseRevision": 2,
        "deviceId": "hermes-office",
        "workspaceId": None,
        "taskId": "general",
        "startedAt": None,
        "durationSeconds": None,
        "isBreak": None,
        "remainingSeconds": 900,
        "extensionSeconds": None,
    }
    preview = {
        "ok": True,
        "result": "preview",
        "contractVersion": "timer-v1",
        "action": "switch_task",
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "previewDigest": PREVIEW_DIGEST,
        "previewExpiresAt": PREVIEW_EXPIRES_AT,
        "normalizedPayload": normalized,
        "readBack": state,
        "replacedSessions": [],
    }
    _request_sequence(monkeypatch, [_manifest("timer.switch_task"), preview])

    result = json.loads(
        h7._handle_timer_command({
            "operationId": OPERATION_ID,
            "action": "switch_task",
            "sessionId": SESSION_ID,
            "baseRevision": 2,
            "deviceId": "hermes-office",
            "taskId": "general",
            "remainingSeconds": 900,
        })
    )

    assert result["result"] == preview


def test_timer_extend_preview_binds_extension_and_reactivated_state(monkeypatch):
    state = _timer_state(
        duration=1800,
        remainingTime=300,
        canonicalRevision=5,
    )
    normalized = {
        "action": "extend",
        "sessionId": SESSION_ID,
        "baseRevision": 4,
        "deviceId": "hermes-office",
        "workspaceId": None,
        "taskId": None,
        "startedAt": None,
        "durationSeconds": None,
        "isBreak": None,
        "remainingSeconds": None,
        "extensionSeconds": 300,
    }
    preview = {
        "ok": True,
        "result": "preview",
        "contractVersion": "timer-v1",
        "action": "extend",
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "previewDigest": PREVIEW_DIGEST,
        "previewExpiresAt": PREVIEW_EXPIRES_AT,
        "normalizedPayload": normalized,
        "readBack": state,
        "replacedSessions": [],
    }
    _request_sequence(monkeypatch, [_manifest("timer.extend"), preview])

    result = json.loads(
        h7._handle_timer_command({
            "operationId": OPERATION_ID,
            "action": "extend",
            "sessionId": SESSION_ID,
            "baseRevision": 4,
            "deviceId": "hermes-office",
            "extensionSeconds": 300,
        })
    )

    assert result["result"] == preview


def test_timer_pause_preview_binds_exact_remaining_seconds(monkeypatch):
    state = _timer_state(
        remainingTime=899,
        isPaused=True,
        canonicalRevision=3,
    )
    preview = {
        "ok": True,
        "result": "preview",
        "contractVersion": "timer-v1",
        "action": "pause",
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "previewDigest": PREVIEW_DIGEST,
        "previewExpiresAt": PREVIEW_EXPIRES_AT,
        "normalizedPayload": {
            "action": "pause",
            "sessionId": SESSION_ID,
            "baseRevision": 2,
            "deviceId": "hermes-office",
            "workspaceId": None,
            "taskId": None,
            "startedAt": None,
            "durationSeconds": None,
            "isBreak": None,
            "remainingSeconds": 899,
            "extensionSeconds": None,
        },
        "readBack": state,
        "replacedSessions": [],
    }
    _request_sequence(monkeypatch, [_manifest("timer.pause"), preview])

    result = json.loads(
        h7._handle_timer_command({
            "operationId": OPERATION_ID,
            "action": "pause",
            "sessionId": SESSION_ID,
            "baseRevision": 2,
            "deviceId": "hermes-office",
            "remainingSeconds": 899,
        })
    )

    assert result["result"] == preview


def test_timer_apply_validates_exact_receipt_and_state(monkeypatch):
    state = _timer_state()
    payload = _timer_receipt(state)
    _request_sequence(monkeypatch, [_manifest("timer.start"), payload])

    result = json.loads(
        h7._handle_timer_command({
            "operationId": OPERATION_ID,
            "action": "start",
            "sessionId": SESSION_ID,
            "baseRevision": 0,
            "deviceId": "hermes-office",
            "taskId": TASK_ID,
            "startedAt": "2026-07-16T07:00:00.000Z",
            "durationSeconds": 1500,
            "isBreak": False,
            "preview": False,
            "previewDigest": PREVIEW_DIGEST,
            "previewExpiresAt": PREVIEW_EXPIRES_AT,
            "requestHash": REQUEST_HASH,
        })
    )

    assert result["result"] == payload


@pytest.mark.parametrize(
    "action,state,args",
    [
        (
            "switch_task",
            {
                "taskId": "general",
                "remainingTime": 900,
                "isPaused": True,
                "canonicalRevision": 3,
            },
            {"baseRevision": 2, "taskId": "general", "remainingSeconds": 900},
        ),
        (
            "extend",
            {"duration": 1800, "remainingTime": 300, "canonicalRevision": 5},
            {"baseRevision": 4, "extensionSeconds": 300},
        ),
    ],
)
def test_timer_switch_and_extend_apply_validate_exact_receipt(
    monkeypatch, action, state, args
):
    read_back = _timer_state(**state)
    payload = _timer_receipt(read_back, action=action)
    _request_sequence(monkeypatch, [_manifest(f"timer.{action}"), payload])

    result = json.loads(
        h7._handle_timer_command({
            "operationId": OPERATION_ID,
            "action": action,
            "sessionId": SESSION_ID,
            "deviceId": "hermes-office",
            **args,
            "preview": False,
            "previewDigest": PREVIEW_DIGEST,
            "previewExpiresAt": PREVIEW_EXPIRES_AT,
            "requestHash": REQUEST_HASH,
        })
    )

    assert result["result"] == payload


@pytest.mark.parametrize(
    "action,state,args",
    [
        (
            "switch_task",
            {"taskId": TASK_ID, "remainingTime": 900, "canonicalRevision": 3},
            {"baseRevision": 2, "taskId": "general", "remainingSeconds": 900},
        ),
        (
            "switch_task",
            {"taskId": "general", "remainingTime": 901, "canonicalRevision": 3},
            {"baseRevision": 2, "taskId": "general", "remainingSeconds": 900},
        ),
        (
            "extend",
            {"duration": 1800, "remainingTime": 301, "canonicalRevision": 5},
            {"baseRevision": 4, "extensionSeconds": 300},
        ),
    ],
)
def test_timer_switch_and_extend_reject_forged_rehashed_outcome(
    monkeypatch, action, state, args
):
    read_back = _timer_state(**state)
    payload = _timer_receipt(read_back, action=action)
    _request_sequence(monkeypatch, [_manifest(f"timer.{action}"), payload])

    result = json.loads(
        h7._handle_timer_command({
            "operationId": OPERATION_ID,
            "action": action,
            "sessionId": SESSION_ID,
            "deviceId": "hermes-office",
            **args,
            "preview": False,
            "previewDigest": PREVIEW_DIGEST,
            "previewExpiresAt": PREVIEW_EXPIRES_AT,
            "requestHash": REQUEST_HASH,
        })
    )

    assert result["error"] == "Canonical timer receipt could not be verified"


def test_timer_apply_rejects_forged_rehashed_outcome(monkeypatch):
    state = _timer_state(isPaused=True)
    payload = _timer_receipt(state)
    _request_sequence(monkeypatch, [_manifest("timer.start"), payload])

    result = json.loads(
        h7._handle_timer_command({
            "operationId": OPERATION_ID,
            "action": "start",
            "sessionId": SESSION_ID,
            "baseRevision": 0,
            "deviceId": "hermes-office",
            "taskId": TASK_ID,
            "startedAt": "2026-07-16T07:00:00.000Z",
            "durationSeconds": 1500,
            "isBreak": False,
            "preview": False,
            "previewDigest": PREVIEW_DIGEST,
            "previewExpiresAt": PREVIEW_EXPIRES_AT,
            "requestHash": REQUEST_HASH,
        })
    )

    assert result["error"] == "Canonical timer receipt could not be verified"


@pytest.mark.parametrize(
    "args,expected",
    [
        (
            {"action": "switch_task", "baseRevision": 2, "taskId": "general"},
            "switch_task requires taskId and remainingSeconds only",
        ),
        (
            {
                "action": "switch_task",
                "baseRevision": 2,
                "taskId": "general",
                "remainingSeconds": True,
            },
            "switch_task requires taskId and remainingSeconds only",
        ),
        (
            {
                "action": "switch_task",
                "baseRevision": 2,
                "taskId": "general",
                "remainingSeconds": 300,
                "extensionSeconds": 30,
            },
            "switch_task requires taskId and remainingSeconds only",
        ),
        (
            {"action": "extend", "baseRevision": 2},
            "extend requires extensionSeconds only",
        ),
        (
            {"action": "extend", "baseRevision": 2, "extensionSeconds": 0},
            "extend requires extensionSeconds only",
        ),
        (
            {"action": "extend", "baseRevision": 2, "extensionSeconds": True},
            "extend requires extensionSeconds only",
        ),
        (
            {
                "action": "extend",
                "baseRevision": 2,
                "extensionSeconds": 300,
                "remainingSeconds": 300,
            },
            "extend requires extensionSeconds only",
        ),
        (
            {"action": "pause", "baseRevision": 2},
            "pause|resume|stop require remainingSeconds only",
        ),
        (
            {
                "action": "resume",
                "baseRevision": 2,
                "remainingSeconds": -1,
            },
            "pause|resume|stop require remainingSeconds only",
        ),
        (
            {
                "action": "stop",
                "baseRevision": 2,
                "remainingSeconds": 0,
                "taskId": TASK_ID,
            },
            "pause|resume|stop require remainingSeconds only",
        ),
    ],
)
def test_timer_actions_reject_wrong_transition_fields(args, expected):
    result = json.loads(
        h7._handle_timer_command({
            "operationId": OPERATION_ID,
            "sessionId": SESSION_ID,
            "deviceId": "hermes-office",
            **args,
        })
    )

    assert result["error"] == expected


def test_unsupported_timer_switch_capability_stops_before_command(monkeypatch):
    seen = _request_sequence(monkeypatch, [_manifest("timer.session")])

    result = json.loads(
        h7._handle_timer_command({
            "operationId": OPERATION_ID,
            "action": "switch_task",
            "sessionId": SESSION_ID,
            "baseRevision": 2,
            "deviceId": "hermes-office",
            "taskId": "general",
            "remainingSeconds": 300,
        })
    )

    assert result["error"] == "FlowState capability timer.switch_task is not available"
    assert len(seen) == 1


@pytest.mark.parametrize("action", ["toggle", "complete", "replace"])
def test_timer_never_exposes_ambiguous_or_fallback_actions(action):
    result = json.loads(
        h7._handle_timer_command({
            "operationId": OPERATION_ID,
            "action": action,
            "sessionId": SESSION_ID,
            "baseRevision": 1,
            "deviceId": "hermes-office",
        })
    )

    assert (
        result["error"] == "action must be start|pause|resume|stop|switch_task|extend"
    )


def test_organization_inventory_validates_exact_targets(monkeypatch):
    inventory = {
        "ok": True,
        "fresh": True,
        "contractVersion": "organization-inventory-v1",
        "scopeKind": "personal",
        "workspaceId": None,
        "projects": [
            {
                "id": PROJECT_ID,
                "name": "Launch",
                "parentId": None,
                "color": "#abcdef",
                "colorType": "hex",
                "workspaceId": None,
                "updatedAt": UPDATED_AT,
            }
        ],
        "groups": [
            {
                "id": GROUP_ID,
                "name": "Writing",
                "type": "custom",
                "parentGroupId": None,
                "workspaceId": None,
                "assignmentMode": "plain",
                "updatedAt": UPDATED_AT,
            }
        ],
    }
    _request_sequence(monkeypatch, [_manifest("organization.inventory"), inventory])

    result = json.loads(h7._handle_organization_inventory({}))

    assert result["result"] == inventory


@pytest.mark.parametrize(
    "action,target_key,target_id",
    [
        ("assign_project", "projectId", PROJECT_ID),
        ("set_canvas_group", "groupId", GROUP_ID),
    ],
)
def test_organization_preview_binds_exact_target_and_placement(
    monkeypatch, action, target_key, target_id
):
    read_back = {
        "id": TASK_ID,
        "workspaceId": None,
        "canonicalRevision": 7,
        "canonicalUpdatedAt": UPDATED_AT,
        "projectId": PROJECT_ID if action == "assign_project" else None,
        "position": {
            "x": 12,
            "y": 24,
            "custom": {"locked": True},
            "parentId": GROUP_ID,
        },
        "isInInbox": False,
    }
    preview = {
        "ok": True,
        "result": "preview",
        "preview": True,
        "contractVersion": "task-v1",
        "operationId": OPERATION_ID,
        "action": action,
        "taskId": TASK_ID,
        "baseRevision": 7,
        "requestHash": REQUEST_HASH,
        "previewDigest": PREVIEW_DIGEST,
        "previewExpiresAt": PREVIEW_EXPIRES_AT,
        "normalizedPayload": {"taskId": TASK_ID, target_key: target_id},
        "readBack": read_back,
    }
    _request_sequence(monkeypatch, [_manifest(f"organization.{action}"), preview])

    result = json.loads(
        h7._handle_organization_command({
            "operationId": OPERATION_ID,
            "action": action,
            "taskId": TASK_ID,
            "baseRevision": 7,
            target_key: target_id,
        })
    )

    assert result["result"] == preview


def test_canvas_group_preview_requires_finite_coordinates_and_inbox_exit(monkeypatch):
    read_back = {
        "id": TASK_ID,
        "workspaceId": None,
        "canonicalRevision": 7,
        "canonicalUpdatedAt": UPDATED_AT,
        "projectId": None,
        "position": {"x": 12, "parentId": GROUP_ID},
        "isInInbox": True,
    }
    preview = {
        "ok": True,
        "result": "preview",
        "preview": True,
        "contractVersion": "task-v1",
        "operationId": OPERATION_ID,
        "action": "set_canvas_group",
        "taskId": TASK_ID,
        "baseRevision": 7,
        "requestHash": REQUEST_HASH,
        "previewDigest": PREVIEW_DIGEST,
        "previewExpiresAt": PREVIEW_EXPIRES_AT,
        "normalizedPayload": {"taskId": TASK_ID, "groupId": GROUP_ID},
        "readBack": read_back,
    }
    _request_sequence(
        monkeypatch, [_manifest("organization.set_canvas_group"), preview]
    )

    result = json.loads(
        h7._handle_organization_command({
            "operationId": OPERATION_ID,
            "action": "set_canvas_group",
            "taskId": TASK_ID,
            "baseRevision": 7,
            "groupId": GROUP_ID,
        })
    )

    assert result["error"] == "Canonical organization preview could not be verified"


def test_organization_apply_rejects_receipt_with_wrong_placement(monkeypatch):
    read_back = {
        "id": TASK_ID,
        "workspaceId": None,
        "canonicalRevision": 8,
        "canonicalUpdatedAt": UPDATED_AT,
        "projectId": "99999999-9999-4999-8999-999999999999",
        "position": {"x": 12, "parentId": None},
    }
    payload = _task_receipt(
        action="assign_project",
        read_back=read_back,
        operation_context={
            "action": "assign_project",
            "taskId": TASK_ID,
            "baseRevision": 7,
            "projectId": PROJECT_ID,
            "workspaceId": None,
        },
    )
    _request_sequence(monkeypatch, [_manifest("organization.assign_project"), payload])

    result = json.loads(
        h7._handle_organization_command({
            "operationId": OPERATION_ID,
            "action": "assign_project",
            "taskId": TASK_ID,
            "baseRevision": 7,
            "projectId": PROJECT_ID,
            "preview": False,
            "previewDigest": PREVIEW_DIGEST,
            "previewExpiresAt": PREVIEW_EXPIRES_AT,
            "requestHash": REQUEST_HASH,
        })
    )

    assert result["error"] == "Canonical organization receipt could not be verified"


def test_canvas_group_apply_validates_coordinates_inbox_exit_and_receipt(monkeypatch):
    read_back = {
        "id": TASK_ID,
        "workspaceId": None,
        "canonicalRevision": 8,
        "canonicalUpdatedAt": UPDATED_AT,
        "projectId": None,
        "position": {
            "x": 12,
            "y": 24,
            "custom": {"locked": True},
            "parentId": GROUP_ID,
        },
        "isInInbox": False,
    }
    payload = _task_receipt(
        action="set_canvas_group",
        read_back=read_back,
        operation_context={
            "action": "set_canvas_group",
            "taskId": TASK_ID,
            "baseRevision": 7,
            "groupId": GROUP_ID,
            "workspaceId": None,
        },
    )
    _request_sequence(
        monkeypatch, [_manifest("organization.set_canvas_group"), payload]
    )

    result = json.loads(
        h7._handle_organization_command({
            "operationId": OPERATION_ID,
            "action": "set_canvas_group",
            "taskId": TASK_ID,
            "baseRevision": 7,
            "groupId": GROUP_ID,
            "preview": False,
            "previewDigest": PREVIEW_DIGEST,
            "previewExpiresAt": PREVIEW_EXPIRES_AT,
            "requestHash": REQUEST_HASH,
        })
    )

    assert result["result"] == payload


def test_h7_schemas_are_compact_preview_first_and_registered():
    schemas = [
        h7.FLOWSTATE_CAPABILITIES_SCHEMA,
        h7.FLOWSTATE_RECURRENCE_CHAIN_SCHEMA,
        h7.FLOWSTATE_RECURRENCE_COMMAND_SCHEMA,
        h7.FLOWSTATE_TIMER_SESSION_SCHEMA,
        h7.FLOWSTATE_TIMER_COMMAND_SCHEMA,
        h7.FLOWSTATE_ORGANIZATION_INVENTORY_SCHEMA,
        h7.FLOWSTATE_ORGANIZATION_COMMAND_SCHEMA,
    ]
    assert all(schema["name"].startswith("flowstate_") for schema in schemas)
    assert h7.FLOWSTATE_RECURRENCE_COMMAND_SCHEMA["parameters"]["required"] == [
        "operationId",
        "taskId",
        "action",
        "baseRevision",
        "timeZone",
    ]
    assert h7.FLOWSTATE_TIMER_COMMAND_SCHEMA["parameters"]["properties"]["action"][
        "enum"
    ] == [
        "start",
        "pause",
        "resume",
        "stop",
        "switch_task",
        "extend",
    ]
    assert set(h7.FLOWSTATE_TIMER_COMMAND_SCHEMA["parameters"]["properties"]) >= {
        "remainingSeconds",
        "extensionSeconds",
    }
    assert all(
        registry.get_toolset_for_tool(schema["name"]) == "flowstate"
        for schema in schemas
    )
    assert h7.FLOWSTATE_ORGANIZATION_COMMAND_SCHEMA["parameters"]["properties"][
        "action"
    ]["enum"] == [
        "assign_project",
        "set_canvas_group",
    ]
