import json

import pytest

from tools import flowstate_tool as fst
from tools.flowstate_receipts import canonical_json_hash


TASK_A = "11111111-1111-4111-8111-111111111111"
TASK_B = "22222222-2222-4222-8222-222222222222"
OPERATION_ID = "assistant:work-block:plan-1"
REQUEST_HASH = "c" * 64
PREVIEW_DIGEST = "a" * 64
EXPIRES_AT = "2026-07-15T21:15:00.000Z"
OPERATIONS = [
    {
        "kind": "move",
        "taskId": TASK_A,
        "baseRevision": 7,
        "workBlockId": "block-a",
        "baseWorkBlockHash": "b" * 64,
        "scheduledDate": "2026-07-16",
        "scheduledTime": "09:30",
    },
    {
        "kind": "create",
        "taskId": TASK_B,
        "baseRevision": 3,
        "clientId": "assistant-block-1",
        "scheduledDate": "2026-07-16",
        "scheduledTime": "10:30",
        "duration": 25,
    },
]


def _read_back(revisions=(7, 3)):
    return [
        {
            "id": TASK_A,
            "workspaceId": None,
            "canonicalRevision": revisions[0],
            "canonicalUpdatedAt": "2026-07-15T20:05:00.000Z",
            "status": "todo",
            "isInInbox": False,
            "instances": [{
                "id": "block-a",
                "taskId": TASK_A,
                "scheduledDate": "2026-07-16",
                "scheduledTime": "09:30",
                "duration": 30,
            }],
        },
        {
            "id": TASK_B,
            "workspaceId": None,
            "canonicalRevision": revisions[1],
            "canonicalUpdatedAt": "2026-07-15T20:05:00.000Z",
            "status": "todo",
            "isInInbox": False,
            "instances": [{
                "id": "generated-block-b",
                "clientId": "assistant-block-1",
                "taskId": TASK_B,
                "scheduledDate": "2026-07-16",
                "scheduledTime": "10:30",
                "duration": 25,
                "timeZone": "Asia/Jerusalem",
            }],
        },
    ]


def _preview(**overrides):
    normalized = [dict(operation) for operation in OPERATIONS]
    normalized[1]["workBlockId"] = "generated-block-b"
    value = {
        "ok": True,
        "result": "preview",
        "contractVersion": "task-v1",
        "action": "work_block_batch",
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "previewDigest": PREVIEW_DIGEST,
        "previewExpiresAt": EXPIRES_AT,
        "timeZone": "Asia/Jerusalem",
        "finishBy": None,
        "normalizedPayload": {
            "timeZone": "Asia/Jerusalem",
            "finishBy": None,
            "operations": normalized,
        },
        "overlapWarnings": [],
        "readBack": _read_back(),
    }
    value.update(overrides)
    return value


def _committed(**receipt_overrides):
    read_back = _read_back((8, 4))
    affected = [
        {
            "entityId": task["id"],
            "entityType": "task",
            "action": "update",
            "canonicalRevision": task["canonicalRevision"],
            "changeSequence": 71 + index,
            "readBack": task,
            "readBackHash": canonical_json_hash(task),
        }
        for index, task in enumerate(read_back)
    ]
    receipt = {
        "ok": True,
        "status": "committed",
        "contractVersion": "task-v1",
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "source": "local-api",
        "entityType": "batch",
        "entityId": OPERATION_ID,
        "action": "work_block_batch",
        "canonicalRevision": 8,
        "changeSequence": 72,
        "replayed": False,
        "committedAt": "2026-07-15T20:05:00.010Z",
        "affected": affected,
        "readBack": read_back,
        "readBackHash": canonical_json_hash(read_back),
    }
    receipt.update(receipt_overrides)
    return {
        "ok": True,
        "result": "committed",
        "action": "work_block_batch",
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "receipt": receipt,
    }


def _args(**overrides):
    value = {
        "operationId": OPERATION_ID,
        "timeZone": "Asia/Jerusalem",
        "operations": OPERATIONS,
    }
    value.update(overrides)
    return value


def test_work_block_command_previews_one_exact_atomic_batch(monkeypatch):
    seen = {}

    def request(method, path, body, **kwargs):
        seen.update(method=method, path=path, body=body, kwargs=kwargs)
        return _preview()

    monkeypatch.setattr(fst, "_request", request)

    result = json.loads(fst._handle_work_block_command(_args()))

    assert result["result"]["previewDigest"] == PREVIEW_DIGEST
    assert seen == {
        "method": "POST",
        "path": "/api/work-blocks/batch",
        "body": {**_args(), "preview": True},
        "kwargs": {"allow_stale_cache": False},
    }


def test_work_block_command_applies_only_with_exact_preview_proof(monkeypatch):
    seen = {}

    def request(method, path, body, **kwargs):
        seen["body"] = body
        return _committed()

    monkeypatch.setattr(fst, "_request", request)
    args = _args(
        preview=False,
        previewDigest=PREVIEW_DIGEST,
        previewExpiresAt=EXPIRES_AT,
        requestHash=REQUEST_HASH,
    )

    result = json.loads(fst._handle_work_block_command(args))

    assert result["result"]["receipt"]["status"] == "committed"
    assert seen["body"] == args


def test_work_block_command_accepts_a_consistent_replay_receipt(monkeypatch):
    monkeypatch.setattr(fst, "_request", lambda *args, **kwargs: _committed(
        status="replayed",
        replayed=True,
    ))

    result = json.loads(fst._handle_work_block_command(_args(
        preview=False,
        previewDigest=PREVIEW_DIGEST,
        previewExpiresAt=EXPIRES_AT,
        requestHash=REQUEST_HASH,
    )))

    assert result["result"]["receipt"]["status"] == "replayed"


@pytest.mark.parametrize("missing", ["previewDigest", "previewExpiresAt", "requestHash"])
def test_work_block_apply_requires_every_approval_field(missing):
    args = _args(
        preview=False,
        previewDigest=PREVIEW_DIGEST,
        previewExpiresAt=EXPIRES_AT,
        requestHash=REQUEST_HASH,
    )
    del args[missing]

    result = json.loads(fst._handle_work_block_command(args))

    assert missing in result["error"]


@pytest.mark.parametrize(
    "change",
    [
        {"operationId": "assistant:work-block:other"},
        {"normalizedPayload": {"timeZone": "Asia/Jerusalem", "finishBy": None, "operations": []}},
        {"readBack": []},
    ],
)
def test_work_block_command_rejects_malformed_preview(monkeypatch, change):
    monkeypatch.setattr(fst, "_request", lambda *args, **kwargs: _preview(**change))

    result = json.loads(fst._handle_work_block_command(_args()))

    assert result["error"] == "Canonical work-block preview could not be verified"


def test_work_block_command_binds_finish_boundary_to_the_same_instant(monkeypatch):
    preview = _preview(
        finishBy="2026-07-16T09:00:00.000Z",
        normalizedPayload={
            "timeZone": "Asia/Jerusalem",
            "finishBy": "2026-07-16T09:00:00.000Z",
            "operations": [
                dict(OPERATIONS[0]),
                {**OPERATIONS[1], "workBlockId": "generated-block-b"},
            ],
        },
    )
    monkeypatch.setattr(fst, "_request", lambda *args, **kwargs: preview)

    result = json.loads(fst._handle_work_block_command(_args(
        finishBy="2026-07-16T12:00:00+03:00",
    )))

    assert result["result"]["finishBy"] == "2026-07-16T09:00:00.000Z"


@pytest.mark.parametrize(
    "change",
    [
        {"finishBy": "2026-07-16T10:00:00.000Z"},
        {"normalizedPayload": {
            "timeZone": "Asia/Jerusalem",
            "finishBy": "2026-07-16T10:00:00.000Z",
            "operations": [
                dict(OPERATIONS[0]),
                {**OPERATIONS[1], "workBlockId": "generated-block-b"},
            ],
        }},
    ],
)
def test_work_block_command_rejects_a_different_finish_boundary(monkeypatch, change):
    monkeypatch.setattr(fst, "_request", lambda *args, **kwargs: _preview(**change))

    result = json.loads(fst._handle_work_block_command(_args(
        finishBy="2026-07-16T12:00:00+03:00",
    )))

    assert result["error"] == "Canonical work-block preview could not be verified"


@pytest.mark.parametrize(
    "receipt_change",
    [
        {"entityId": "wrong-operation"},
        {"affected": []},
        {"readBackHash": "0" * 64},
    ],
)
def test_work_block_command_rejects_malformed_receipt(monkeypatch, receipt_change):
    monkeypatch.setattr(fst, "_request", lambda *args, **kwargs: _committed(**receipt_change))

    result = json.loads(fst._handle_work_block_command(_args(
        preview=False,
        previewDigest=PREVIEW_DIGEST,
        previewExpiresAt=EXPIRES_AT,
        requestHash=REQUEST_HASH,
    )))

    assert result["error"] == "Canonical work-block receipt could not be verified"


@pytest.mark.parametrize(
    "receipt_change",
    [
        {"canonicalRevision": 0},
        {"changeSequence": 0},
        {"status": "committed", "replayed": True},
        {"status": "replayed", "replayed": False},
    ],
)
def test_work_block_command_rejects_an_inconsistent_receipt_envelope(monkeypatch, receipt_change):
    monkeypatch.setattr(fst, "_request", lambda *args, **kwargs: _committed(**receipt_change))

    result = json.loads(fst._handle_work_block_command(_args(
        preview=False,
        previewDigest=PREVIEW_DIGEST,
        previewExpiresAt=EXPIRES_AT,
        requestHash=REQUEST_HASH,
    )))

    assert result["error"] == "Canonical work-block receipt could not be verified"


@pytest.mark.parametrize(
    "operations,error",
    [
        ([{**OPERATIONS[1], "scheduledDate": "2026-02-30"}], "scheduledDate"),
        ([{**OPERATIONS[1], "scheduledTime": "25:00"}], "scheduledTime"),
        ([{**OPERATIONS[1], "duration": True}], "duration"),
        ([{**OPERATIONS[0], "baseWorkBlockHash": "short"}], "baseWorkBlockHash"),
    ],
)
def test_work_block_command_rejects_bad_shapes_without_network(monkeypatch, operations, error):
    called = []
    monkeypatch.setattr(fst, "_request", lambda *args, **kwargs: called.append(True))

    result = json.loads(fst._handle_work_block_command(_args(operations=operations)))

    assert error in result["error"]
    assert called == []


def test_schedule_adapter_uses_the_same_canonical_batch(monkeypatch):
    monkeypatch.setattr(fst, "_request", lambda *args, **kwargs: _preview(
        normalizedPayload={
            "timeZone": "Asia/Jerusalem",
            "finishBy": None,
            "operations": [{
                "kind": "create", "taskId": TASK_B, "baseRevision": 3,
                "clientId": "assistant-block-1", "workBlockId": "generated-block-b",
                "scheduledDate": "2026-07-16", "scheduledTime": "10:30", "duration": 25,
            }],
        },
        readBack=[_read_back()[1]],
    ))

    result = json.loads(fst._handle_schedule_task_instance({
        "id": TASK_B,
        "operationId": OPERATION_ID,
        "baseRevision": 3,
        "clientId": "assistant-block-1",
        "timeZone": "Asia/Jerusalem",
        "scheduledDate": "2026-07-16",
        "scheduledTime": "10:30",
        "duration": 25,
    }))

    assert result["result"]["action"] == "work_block_batch"


def test_work_block_inventory_is_fresh_and_hash_verified_without_cache_fallback(monkeypatch):
    instance = {
        "id": "block-a", "scheduledDate": "2026-07-16",
        "scheduledTime": "09:30", "duration": 30,
    }
    seen = {}

    def request(method, path, **kwargs):
        seen.update(method=method, path=path, kwargs=kwargs)
        return {
            "ok": True, "fresh": True,
            "task": {"id": TASK_A, "title": "First task", "workspaceId": None, "canonicalRevision": 7},
            "instances": [{**instance, "baseWorkBlockHash": canonical_json_hash(instance)}],
        }

    monkeypatch.setattr(fst, "_request", request)

    result = json.loads(fst._handle_list_task_instances({"id": TASK_A}))

    assert result["result"]["task"]["canonicalRevision"] == 7
    assert result["result"]["instances"][0]["baseWorkBlockHash"] == canonical_json_hash(instance)
    assert seen["kwargs"] == {"allow_stale_cache": False}


def test_work_block_inventory_rejects_a_forged_block_hash(monkeypatch):
    monkeypatch.setattr(fst, "_request", lambda *args, **kwargs: {
        "ok": True, "fresh": True,
        "task": {"id": TASK_A, "title": "First task", "workspaceId": None, "canonicalRevision": 7},
        "instances": [{
            "id": "block-a", "scheduledDate": "2026-07-16", "scheduledTime": "09:30",
            "duration": 30, "baseWorkBlockHash": "0" * 64,
        }],
    })

    result = json.loads(fst._handle_list_task_instances({"id": TASK_A}))

    assert result["error"] == "Canonical work-block inventory could not be verified"
