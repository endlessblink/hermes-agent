"""RED contract tests for canonical FlowState subtask mutations (TASK-1963)."""

import io
import json
import urllib.error

import pytest

from tools import flowstate_tool as fst
from tools.flowstate_receipts import canonical_json_hash


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _capturing_urlopen(seen, payload):
    def _urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["body"] = None if req.data is None else json.loads(req.data.decode("utf-8"))
        return _Response(payload)

    return _urlopen


@pytest.fixture(autouse=True)
def flowstate_config(monkeypatch):
    monkeypatch.setattr(fst, "_FLOW_STATE_API_URL", "http://127.0.0.1:5577")
    monkeypatch.setattr(fst, "_FLOW_STATE_API_TOKEN", "token-123")


OPERATION_ID = "subtasks-op-1"
REQUEST_HASH = "c" * 64
PREVIEW_DIGEST = "a" * 64
PREVIEW_EXPIRES_AT = "2026-07-15T20:30:00.000Z"
UPDATED_AT = "2026-07-15T20:20:00.000Z"
COMMITTED_AT = "2026-07-15T20:20:01.000Z"


OPERATIONS = [
    {
        "kind": "create",
        "clientId": "step-draft",
        "title": "Draft the outline",
        "doneEnough": "An ordered five-point outline exists",
        "estimateMinutes": 20,
        "order": 0,
    },
    {
        "kind": "update",
        "subtaskId": "sub-existing",
        "title": "Review only the critical sections",
        "doneEnough": "Blocking comments are resolved",
        "estimateMinutes": 15,
        "order": 1,
    },
    {"kind": "delete", "subtaskId": "sub-obsolete"},
]


def _preview_payload(**overrides):
    payload = {
        "ok": True,
        "result": "preview",
        "contractVersion": "task-v1",
        "action": "subtask_batch",
        "operationId": OPERATION_ID,
        "taskId": "task-1",
        "baseRevision": 7,
        "previewDigest": PREVIEW_DIGEST,
        "requestHash": REQUEST_HASH,
        "previewExpiresAt": PREVIEW_EXPIRES_AT,
        "normalizedPayload": {"taskId": "task-1", "operations": OPERATIONS},
        "readBack": {
            "id": "task-1",
            "status": "todo",
            "canonicalRevision": 7,
            "canonicalUpdatedAt": UPDATED_AT,
            "subtasks": [
                {
                    "id": "sub-existing",
                    "title": "Old title",
                    "doneEnough": None,
                    "estimateMinutes": None,
                    "order": 0,
                },
                {
                    "id": "sub-obsolete",
                    "title": "Obsolete",
                    "doneEnough": None,
                    "estimateMinutes": None,
                    "order": 1,
                },
            ],
        },
    }
    payload.update(overrides)
    return payload


def _committed_payload(**overrides):
    read_back = {
        "id": "task-1",
        "status": "todo",
        "canonicalRevision": 8,
        "canonicalUpdatedAt": UPDATED_AT,
        "subtasks": [
            {
                "id": "sub-created",
                "clientId": "step-draft",
                "title": "Draft the outline",
                "doneEnough": "An ordered five-point outline exists",
                "estimateMinutes": 20,
                "order": 0,
            },
            {
                "id": "sub-existing",
                "title": "Review only the critical sections",
                "doneEnough": "Blocking comments are resolved",
                "estimateMinutes": 15,
                "order": 1,
            },
        ],
    }
    affected = {
        "entityType": "task",
        "entityId": "task-1",
        "action": "update",
        "canonicalRevision": 8,
        "changeSequence": 44,
        "readBack": read_back,
        "readBackHash": canonical_json_hash(read_back),
    }
    receipt = {
        "ok": True,
        "status": "committed",
        "contractVersion": "task-v1",
        "operationId": OPERATION_ID,
        "requestHash": REQUEST_HASH,
        "source": "local-api",
        "entityType": "task",
        "action": "subtask_batch",
        "entityId": "task-1",
        "canonicalRevision": 8,
        "canonicalUpdatedAt": UPDATED_AT,
        "changeSequence": 44,
        "committedAt": COMMITTED_AT,
        "readBack": read_back,
        "readBackHash": canonical_json_hash(read_back),
        "affected": [affected],
    }
    receipt.update(overrides.pop("receipt", {}))
    payload = {
        "ok": True,
        "result": "committed",
        "operationId": OPERATION_ID,
        "action": "subtask_batch",
        "taskId": "task-1",
        "requestHash": REQUEST_HASH,
        "receipt": receipt,
    }
    payload.update(overrides)
    return payload


def _batch_args(**overrides):
    args = {
        "taskId": "task-1",
        "operationId": OPERATION_ID,
        "baseRevision": 7,
        "operations": OPERATIONS,
    }
    args.update(overrides)
    return args


@pytest.mark.parametrize("missing", ["operationId", "baseRevision"])
def test_subtask_preview_requires_canonical_identity_before_io(monkeypatch, missing):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("invalid preview reached Local Task API"),
    )
    args = _batch_args()
    args.pop(missing)

    result = json.loads(fst._handle_subtask_batch(args))

    assert missing in result["error"]


def test_subtask_batch_preview_preserves_exact_ordered_decomposition(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, _preview_payload()),
    )

    result = json.loads(fst._handle_subtask_batch(_batch_args()))

    assert result["result"]["normalizedPayload"]["operations"] == OPERATIONS
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/tasks/task-1/subtasks/batch")
    assert seen["body"] == {
        "operationId": OPERATION_ID,
        "baseRevision": 7,
        "preview": True,
        "operations": OPERATIONS,
    }


@pytest.mark.parametrize(
    "missing",
    ["operationId", "baseRevision", "previewDigest", "previewExpiresAt", "requestHash"],
)
def test_subtask_apply_requires_all_preview_bindings_before_io(monkeypatch, missing):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("unbound apply reached Local Task API"),
    )
    args = _batch_args(
        preview=False,
        previewDigest=PREVIEW_DIGEST,
        previewExpiresAt=PREVIEW_EXPIRES_AT,
        requestHash=REQUEST_HASH,
    )
    args.pop(missing)

    result = json.loads(fst._handle_subtask_batch(args))

    assert missing in result["error"]


def test_subtask_apply_accepts_only_verified_canonical_receipt(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, _committed_payload()),
    )
    args = _batch_args(
        preview=False,
        previewDigest=PREVIEW_DIGEST,
        previewExpiresAt=PREVIEW_EXPIRES_AT,
        requestHash=REQUEST_HASH,
    )

    result = json.loads(fst._handle_subtask_batch(args))

    assert result["result"]["receipt"]["readBack"]["canonicalRevision"] == 8
    assert seen["body"] == {
        key: value for key, value in {**args, "preview": False}.items() if key != "taskId"
    }


def test_subtask_apply_rejects_self_consistent_receipt_with_wrong_approved_outcome(monkeypatch):
    payload = _committed_payload()
    receipt = payload["receipt"]
    wrong_read_back = {
        **receipt["readBack"],
        "subtasks": [
            {**receipt["readBack"]["subtasks"][0], "title": "A different result"},
            receipt["readBack"]["subtasks"][1],
        ],
    }
    wrong_hash = canonical_json_hash(wrong_read_back)
    receipt["readBack"] = wrong_read_back
    receipt["readBackHash"] = wrong_hash
    receipt["affected"][0]["readBack"] = wrong_read_back
    receipt["affected"][0]["readBackHash"] = wrong_hash
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen({}, payload),
    )

    result = json.loads(fst._handle_subtask_batch(_batch_args(
        preview=False,
        previewDigest=PREVIEW_DIGEST,
        previewExpiresAt=PREVIEW_EXPIRES_AT,
        requestHash=REQUEST_HASH,
    )))

    assert result == {"error": "Canonical subtask receipt could not be verified"}


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True},
        {"ok": True, "result": "queued"},
        _preview_payload(operationId="another-operation"),
        _preview_payload(baseRevision=8),
        _preview_payload(normalizedPayload={"taskId": "task-1", "operations": list(reversed(OPERATIONS))}),
        _committed_payload(receipt={"operationId": "another-operation"}),
        _committed_payload(receipt={"readBackHash": "d" * 64}),
        _committed_payload(receipt={"affected": []}),
    ],
)
def test_subtask_mutations_reject_http_only_or_unverified_success(monkeypatch, payload):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen({}, payload),
    )
    is_apply = payload.get("result") == "committed"
    args = _batch_args(
        preview=not is_apply,
        **(
            {
                "previewDigest": PREVIEW_DIGEST,
                "previewExpiresAt": PREVIEW_EXPIRES_AT,
                "requestHash": REQUEST_HASH,
            }
            if is_apply
            else {}
        ),
    )

    result = json.loads(fst._handle_subtask_batch(args))

    assert "error" in result
    assert "result" not in result


def test_subtask_batch_preserves_typed_stale_revision_without_secret_details(monkeypatch):
    def _raise(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url,
            409,
            "Conflict",
            {},
            io.BytesIO(
                json.dumps(
                    {
                        "error": {
                            "code": "stale_revision",
                            "message": "Task changed since preview.",
                            "authorization": "Bearer server-secret",
                        },
                        "debug": "database secret",
                    }
                ).encode("utf-8")
            ),
        )

    monkeypatch.setattr(fst.urllib.request, "urlopen", _raise)

    result = json.loads(fst._handle_subtask_batch(_batch_args()))

    assert result == {
        "error": "Task changed since preview.",
        "code": "stale_revision",
        "status": 409,
    }
    assert "secret" not in json.dumps(result).lower()


@pytest.mark.parametrize(
    "handler,args,expected_operation",
    [
        (
            fst._handle_create_subtask,
            {
                "taskId": "task-1",
                "operationId": "create-subtask-1",
                "baseRevision": 7,
                "clientId": "draft-step",
                "title": "Draft outline",
                "doneEnough": "Five bullets exist",
                "estimateMinutes": 20,
                "order": 0,
            },
            {
                "kind": "create",
                "clientId": "draft-step",
                "title": "Draft outline",
                "doneEnough": "Five bullets exist",
                "estimateMinutes": 20,
                "order": 0,
            },
        ),
        (
            fst._handle_update_subtask,
            {
                "taskId": "task-1",
                "operationId": "update-subtask-1",
                "baseRevision": 7,
                "subtaskId": "sub-1",
                "doneEnough": "Review notes captured",
                "estimateMinutes": 10,
            },
            {
                "kind": "update",
                "subtaskId": "sub-1",
                "doneEnough": "Review notes captured",
                "estimateMinutes": 10,
            },
        ),
        (
            fst._handle_delete_subtask,
            {
                "taskId": "task-1",
                "operationId": "delete-subtask-1",
                "baseRevision": 7,
                "subtaskId": "sub-1",
            },
            {"kind": "delete", "subtaskId": "sub-1"},
        ),
    ],
)
def test_singular_subtask_tools_are_exact_one_operation_batch_adapters(
    monkeypatch, handler, args, expected_operation
):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(
            seen,
            _preview_payload(
                operationId=args["operationId"],
                normalizedPayload={"taskId": "task-1", "operations": [expected_operation]},
            ),
        ),
    )

    result = json.loads(handler(args))

    assert result["result"]["normalizedPayload"]["operations"] == [expected_operation]
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/tasks/task-1/subtasks/batch")
    assert seen["body"] == {
        "operationId": args["operationId"],
        "baseRevision": 7,
        "preview": True,
        "operations": [expected_operation],
    }


def test_subtask_batch_schema_exposes_only_canonical_operation_vocabulary():
    parameters = fst.FLOWSTATE_SUBTASK_BATCH_SCHEMA["parameters"]
    operation_schema = parameters["properties"]["operations"]["items"]
    operation_properties = operation_schema["properties"]

    assert {"operationId", "baseRevision", "operations"}.issubset(parameters["required"])
    assert operation_properties["kind"]["enum"] == ["create", "update", "delete"]
    assert "action" not in operation_properties
    assert {
        "clientId", "doneEnough", "estimateMinutes",
        "completedPomodoros", "canvasPosition",
    }.issubset(operation_properties)


def test_subtask_batch_preserves_canvas_position_progress_and_nullable_planning_fields(monkeypatch):
    seen = {}
    operations = [{
        "kind": "update",
        "subtaskId": "step-1",
        "doneEnough": None,
        "estimateMinutes": None,
        "completedPomodoros": 2,
        "canvasPosition": {"x": 420, "y": 260},
    }]
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(
            seen,
            _preview_payload(normalizedPayload={"taskId": "task-1", "operations": operations}),
        ),
    )

    result = json.loads(fst._handle_subtask_batch({
        **_batch_args(),
        "operations": operations,
    }))

    assert result["result"]["normalizedPayload"]["operations"] == operations
    assert seen["body"]["operations"] == operations
