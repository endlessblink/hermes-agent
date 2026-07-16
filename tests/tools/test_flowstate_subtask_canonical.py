"""RED contract tests for canonical FlowState subtask mutations (TASK-1963)."""

import io
import json
import urllib.error
from datetime import datetime, timezone

import pytest

from agent.subtask_approval_capabilities import subtask_approval_capabilities
from tools import flowstate_tool as fst
from tools.approval import reset_current_session_key, set_current_session_key
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
    monkeypatch.setattr(
        subtask_approval_capabilities,
        "_clock",
        lambda: datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc),
    )
    subtask_approval_capabilities.revoke_session("test-session")
    token = set_current_session_key("test-session")
    yield
    reset_current_session_key(token)
    subtask_approval_capabilities.revoke_session("test-session")


OPERATION_ID = "subtasks-op-1"
REQUEST_HASH = "c" * 64
PREVIEW_DIGEST = "a" * 64
PREVIEW_EXPIRES_AT = "2026-07-15T20:30:00.000Z"
UPDATED_AT = "2026-07-15T20:20:00.000Z"
COMMITTED_AT = "2026-07-15T20:20:01.000Z"


CANONICAL_SUBTASK = {
    "id": "sub-1",
    "title": "Draft",
    "order": 0,
    "parentTaskId": "task-1",
    "clientId": "draft-client",
    "description": "Create the smallest reviewable draft",
    "isCompleted": False,
    "doneEnough": "One reviewable draft exists",
    "estimateMinutes": 25,
    "completedPomodoros": 1,
    "canvasPosition": {"x": 120.5, "y": -40},
    "createdAt": "2026-07-15T20:00:00.000Z",
    "updatedAt": UPDATED_AT,
}


def _list_payload(subtasks):
    return {
        "ok": True,
        "task": {
            "id": "task-1",
            "title": "Prepare launch",
            "workspaceId": "123e4567-e89b-42d3-a456-426614174000",
            "canonicalRevision": 7,
            "canonicalUpdatedAt": UPDATED_AT,
        },
        "subtasks": subtasks,
    }


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


def _approved_apply_args(**overrides):
    args = _batch_args(
        preview=False,
        previewDigest=PREVIEW_DIGEST,
        previewExpiresAt=PREVIEW_EXPIRES_AT,
        requestHash=REQUEST_HASH,
    )
    proof = {
        **args,
        "proposalId": "proposal-1",
        "proposalRevision": 1,
    }
    proof.pop("preview")
    capability = subtask_approval_capabilities.register("test-session", proof)
    args["approvalCapability"] = capability
    args["proposalId"] = "proposal-1"
    args["proposalRevision"] = 1
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
    assert result["result"]["approvalRequest"] == {
        "action": "subtask_batch",
        "baseRevision": 7,
        "contractVersion": "task-v1",
        "operationId": OPERATION_ID,
        "operations": OPERATIONS,
        "previewDigest": PREVIEW_DIGEST,
        "previewExpiresAt": PREVIEW_EXPIRES_AT,
        "requestHash": REQUEST_HASH,
        "taskId": "task-1",
    }
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/tasks/task-1/subtasks/batch")
    assert seen["body"] == {
        "operationId": OPERATION_ID,
        "baseRevision": 7,
        "preview": True,
        "operations": OPERATIONS,
    }


@pytest.mark.parametrize(
    "operation",
    [
        {"kind": "create", "clientId": "x" * 161, "title": "Draft"},
        {"kind": "create", "clientId": "draft", "title": "x" * 501},
        {
            "kind": "create",
            "clientId": "draft",
            "title": "Draft",
            "description": "x" * 10_001,
        },
        {
            "kind": "create",
            "clientId": "draft",
            "title": "Draft",
            "doneEnough": "x" * 2_001,
        },
        {"kind": "update", "subtaskId": "x" * 257, "title": "Draft"},
        {
            "kind": "update",
            "subtaskId": "existing",
            "completedPomodoros": 1_000_001,
        },
        {
            "kind": "update",
            "subtaskId": "existing",
            "canvasPosition": {"x": float("inf"), "y": 1},
        },
    ],
)
def test_subtask_preview_rejects_values_that_cannot_be_read_back_canonically(
    monkeypatch, operation
):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail(
            "non-canonical subtask operation reached Local Task API"
        ),
    )

    result = json.loads(
        fst._handle_subtask_batch(_batch_args(operations=[operation]))
    )

    assert "error" in result


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
    args = _approved_apply_args()
    args.pop(missing)

    result = json.loads(fst._handle_subtask_batch(args))

    assert missing in result["error"]


def test_subtask_apply_requires_trusted_capability_before_io(monkeypatch):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("direct model apply reached Local Task API"),
    )
    args = _approved_apply_args()
    args.pop("approvalCapability")

    result = json.loads(fst._handle_subtask_batch(args))

    assert result == {"error": "approvalCapability is required for apply"}


@pytest.mark.parametrize(
    "patch",
    [
        {"requestHash": "d" * 64},
        {"proposalId": "different-proposal"},
        {"proposalRevision": 2},
    ],
)
def test_subtask_apply_rejects_cross_proof_capability_before_io(monkeypatch, patch):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("mismatched approval reached Local Task API"),
    )
    args = _approved_apply_args()
    args.update(patch)

    result = json.loads(fst._handle_subtask_batch(args))

    assert result == {"error": "approvalCapability is invalid for this exact apply"}


def test_subtask_apply_rejects_expired_capability_before_io(monkeypatch):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("expired approval reached Local Task API"),
    )
    args = _approved_apply_args()
    monkeypatch.setattr(
        subtask_approval_capabilities,
        "_clock",
        lambda: datetime(2026, 7, 15, 20, 31, tzinfo=timezone.utc),
    )

    result = json.loads(fst._handle_subtask_batch(args))

    assert result == {"error": "approvalCapability is invalid for this exact apply"}


def test_subtask_apply_allows_exact_capability_replay_after_response_loss(monkeypatch):
    calls = []

    def _request(_method, _path, body, **_kwargs):
        calls.append(body)
        if len(calls) == 1:
            raise TimeoutError("response lost")
        return _committed_payload()

    monkeypatch.setattr(fst, "_request", _request)
    args = _approved_apply_args()

    first = json.loads(fst._handle_subtask_batch(args))
    second = json.loads(fst._handle_subtask_batch(args))

    assert first == {"error": "response lost"}
    assert second["result"]["result"] == "committed"
    assert len(calls) == 2
    assert all("approvalCapability" not in body for body in calls)


def test_subtask_apply_accepts_only_verified_canonical_receipt(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, _committed_payload()),
    )
    args = _approved_apply_args()

    result = json.loads(fst._handle_subtask_batch(args))

    assert result["result"]["receipt"]["readBack"]["canonicalRevision"] == 8
    assert seen["body"] == {
        key: value
        for key, value in {**args, "preview": False}.items()
        if key
        not in {
            "taskId",
            "approvalCapability",
            "proposalId",
            "proposalRevision",
        }
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

    result = json.loads(fst._handle_subtask_batch(_approved_apply_args()))

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
    args = _approved_apply_args() if is_apply else _batch_args()

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


def test_subtask_batch_preserves_typed_client_identity_conflict(monkeypatch):
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
                            "code": "client_id_conflict",
                            "message": "A subtask already uses this clientId",
                        }
                    }
                ).encode("utf-8")
            ),
        )

    monkeypatch.setattr(fst.urllib.request, "urlopen", _raise)

    result = json.loads(fst._handle_subtask_batch(_batch_args()))

    assert result == {
        "error": "A subtask already uses this clientId",
        "code": "client_id_conflict",
        "status": 409,
    }


def test_subtask_batch_preserves_bounded_current_revision(monkeypatch):
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
                            "currentRevision": 12,
                        }
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
        "currentRevision": 12,
    }


@pytest.mark.parametrize("current_revision", [True, 0, -1, "12", 2**63])
def test_subtask_batch_rejects_malformed_or_unbounded_current_revision(
    monkeypatch, current_revision
):
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
                            "currentRevision": current_revision,
                            "details": {
                                "authorization": "Bearer forged-secret",
                                "currentRevision": 99,
                            },
                        }
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
    assert "forged-secret" not in json.dumps(result)


def test_list_subtasks_returns_fresh_parent_mutation_identity(monkeypatch):
    seen = {}

    def _request(method, path, body=None, *, allow_stale_cache=True):
        seen.update({
            "method": method,
            "path": path,
            "allow_stale_cache": allow_stale_cache,
        })
        return {
            "ok": True,
            "task": {
                "id": "task-1",
                "title": "Prepare launch",
                "workspaceId": "123e4567-e89b-42d3-a456-426614174000",
                "canonicalRevision": 7,
                "canonicalUpdatedAt": UPDATED_AT,
            },
            "subtasks": [
                {"id": "sub-1", "title": "Draft", "order": 0},
                {"id": "sub-2", "title": "Review", "order": 1},
            ],
        }

    monkeypatch.setattr(fst, "_request", _request)

    result = json.loads(fst._handle_list_subtasks({"taskId": "task-1"}))

    assert result["result"]["task"] == {
        "id": "task-1",
        "title": "Prepare launch",
        "workspaceId": "123e4567-e89b-42d3-a456-426614174000",
        "canonicalRevision": 7,
        "canonicalUpdatedAt": UPDATED_AT,
    }
    assert [item["id"] for item in result["result"]["subtasks"]] == [
        "sub-1",
        "sub-2",
    ]
    assert seen == {
        "method": "GET",
        "path": "/api/tasks/task-1/subtasks",
        "allow_stale_cache": False,
    }


def test_list_subtasks_preserves_personal_scope_without_a_workspace_id(monkeypatch):
    monkeypatch.setattr(
        fst,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "task": {
                "id": "task-1",
                "title": "Personal task",
                "workspaceId": None,
                "canonicalRevision": 7,
                "canonicalUpdatedAt": UPDATED_AT,
            },
            "subtasks": [],
        },
    )

    result = json.loads(fst._handle_list_subtasks({"taskId": "task-1"}))

    assert result["result"]["task"]["workspaceId"] is None


def test_list_subtasks_preserves_the_exact_bounded_canonical_row(monkeypatch):
    monkeypatch.setattr(
        fst,
        "_request",
        lambda *_args, **_kwargs: _list_payload([CANONICAL_SUBTASK]),
    )

    result = json.loads(fst._handle_list_subtasks({"taskId": "task-1"}))

    assert result["result"]["subtasks"] == [CANONICAL_SUBTASK]


@pytest.mark.parametrize(
    "count,accepted",
    [(10_001, True), (10_002, False)],
)
def test_list_subtasks_matches_flowstate_canonical_array_bound(
    monkeypatch, count, accepted
):
    rows = [
        {
                **CANONICAL_SUBTASK,
                "id": f"sub-{index}",
                "clientId": f"client-{index}",
                "order": index,
        }
        for index in range(count)
    ]
    monkeypatch.setattr(
        fst,
        "_request",
        lambda *_args, **_kwargs: _list_payload(rows),
    )

    result = json.loads(fst._handle_list_subtasks({"taskId": "task-1"}))

    if accepted:
        assert len(result["result"]["subtasks"]) == count
    else:
        assert result == {
            "error": "Fresh canonical subtask state could not be verified"
        }


@pytest.mark.parametrize(
    "row_patch",
    [
        {"unknown": "reject-me"},
        {"id": ""},
        {"id": " padded"},
        {"id": "x" * 257},
        {"id": 7},
        {"title": ""},
        {"title": "padded "},
        {"title": "x" * 501},
        {"title": 7},
        {"order": True},
        {"order": -1},
        {"order": 1},
        {"order": "0"},
        {"parentTaskId": ""},
        {"parentTaskId": " padded"},
        {"parentTaskId": "x" * 257},
        {"parentTaskId": 7},
        {"parentTaskId": "different-task"},
        {"clientId": ""},
        {"clientId": "padded "},
        {"clientId": "x" * 161},
        {"clientId": 7},
        {"description": "x" * 10_001},
        {"description": 7},
        {"isCompleted": 1},
        {"doneEnough": "x" * 2_001},
        {"doneEnough": 7},
        {"estimateMinutes": True},
        {"estimateMinutes": 0},
        {"estimateMinutes": 1_441},
        {"estimateMinutes": "25"},
        {"completedPomodoros": True},
        {"completedPomodoros": -1},
        {"completedPomodoros": 1_000_001},
        {"canvasPosition": {"x": 1}},
        {"canvasPosition": {"x": 1, "y": 2, "z": 3}},
        {"canvasPosition": {"x": True, "y": 2}},
        {"canvasPosition": {"x": float("inf"), "y": 2}},
        {"canvasPosition": [1, 2]},
        {"createdAt": "yesterday"},
        {"createdAt": "2026-07-15T20:00:00.1234567890Z"},
        {"createdAt": "2026-07-15T20:00:00." + "0" * 45 + "+00:00"},
        {"createdAt": 7},
        {"updatedAt": "yesterday"},
        {"updatedAt": "2026-07-15T20:00:00.1234567890Z"},
        {"updatedAt": "2026-07-15T20:00:00." + "0" * 45 + "+00:00"},
        {"updatedAt": 7},
    ],
)
def test_list_subtasks_rejects_malformed_or_unbounded_canonical_rows(
    monkeypatch, row_patch
):
    row = {**CANONICAL_SUBTASK, **row_patch}
    monkeypatch.setattr(
        fst,
        "_request",
        lambda *_args, **_kwargs: _list_payload([row]),
    )

    result = json.loads(fst._handle_list_subtasks({"taskId": "task-1"}))

    assert result == {"error": "Fresh canonical subtask state could not be verified"}


@pytest.mark.parametrize("nullable_field", ["doneEnough", "estimateMinutes", "canvasPosition"])
def test_list_subtasks_accepts_canonical_nullable_fields(monkeypatch, nullable_field):
    row = {**CANONICAL_SUBTASK, nullable_field: None}
    monkeypatch.setattr(
        fst,
        "_request",
        lambda *_args, **_kwargs: _list_payload([row]),
    )

    result = json.loads(fst._handle_list_subtasks({"taskId": "task-1"}))

    assert result["result"]["subtasks"] == [row]


def test_list_subtasks_rejects_duplicate_ids_and_noncanonical_array_order(monkeypatch):
    second = {**CANONICAL_SUBTASK, "id": "sub-2", "order": 0}
    duplicate = {**CANONICAL_SUBTASK, "order": 1}
    duplicate_client = {
        **CANONICAL_SUBTASK,
        "id": "sub-2",
        "order": 1,
    }

    for rows in (
        [CANONICAL_SUBTASK, second],
        [CANONICAL_SUBTASK, duplicate],
        [CANONICAL_SUBTASK, duplicate_client],
    ):
        monkeypatch.setattr(
            fst,
            "_request",
            lambda *_args, _rows=rows, **_kwargs: _list_payload(_rows),
        )

        result = json.loads(fst._handle_list_subtasks({"taskId": "task-1"}))

        assert result == {"error": "Fresh canonical subtask state could not be verified"}


def test_list_subtasks_accepts_bounded_legacy_row_without_parent_task_id(monkeypatch):
    row = {key: value for key, value in CANONICAL_SUBTASK.items() if key != "parentTaskId"}
    monkeypatch.setattr(
        fst,
        "_request",
        lambda *_args, **_kwargs: _list_payload([row]),
    )

    result = json.loads(fst._handle_list_subtasks({"taskId": "task-1"}))

    assert result["result"]["subtasks"] == [row]


@pytest.mark.parametrize(
    "status,code,expected_message",
    [
        (500, "read_failed", "subtasks could not be read"),
        (404, "not_found", "task not found"),
    ],
)
def test_list_subtasks_preserves_typed_stable_redacted_read_errors(
    monkeypatch, status, code, expected_message
):
    def _raise(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url,
            status,
            "unsafe reason Bearer forged-secret",
            {},
            io.BytesIO(
                json.dumps(
                    {
                        "error": {
                            "code": code,
                            "message": "Bearer forged-secret",
                            "authorization": "Bearer forged-secret",
                        }
                    }
                ).encode("utf-8")
            ),
        )

    monkeypatch.setattr(fst.urllib.request, "urlopen", _raise)

    result = json.loads(fst._handle_list_subtasks({"taskId": "task-1"}))

    assert result == {
        "error": expected_message,
        "code": code,
        "status": status,
    }
    assert "forged-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "task_patch",
    [
        {"workspaceId": "not-a-workspace-id"},
        {"canonicalRevision": True},
        {"canonicalRevision": 0},
        {"canonicalUpdatedAt": "yesterday"},
    ],
)
def test_list_subtasks_rejects_malformed_parent_mutation_identity(
    monkeypatch, task_patch
):
    task = {
        "id": "task-1",
        "title": "Prepare launch",
        "workspaceId": "123e4567-e89b-42d3-a456-426614174000",
        "canonicalRevision": 7,
        "canonicalUpdatedAt": UPDATED_AT,
        **task_patch,
    }
    monkeypatch.setattr(
        fst,
        "_request",
        lambda *_args, **_kwargs: {"ok": True, "task": task, "subtasks": []},
    )

    result = json.loads(fst._handle_list_subtasks({"taskId": "task-1"}))

    assert result == {"error": "Fresh canonical subtask state could not be verified"}


def test_list_subtasks_rejects_forged_parent_or_subtask_identity(monkeypatch):
    valid_task = {
        "id": "different-task",
        "title": "Prepare launch",
        "workspaceId": "123e4567-e89b-42d3-a456-426614174000",
        "canonicalRevision": 7,
        "canonicalUpdatedAt": UPDATED_AT,
    }
    monkeypatch.setattr(
        fst,
        "_request",
        lambda *_args, **_kwargs: {
            "ok": True,
            "task": valid_task,
            "subtasks": [{"id": "same"}, {"id": "same"}],
        },
    )

    result = json.loads(fst._handle_list_subtasks({"taskId": "task-1"}))

    assert result == {"error": "Fresh canonical subtask state could not be verified"}


def test_list_subtasks_never_falls_back_to_a_stale_profile_snapshot(
    monkeypatch, tmp_path
):
    payload = {
        "ok": True,
        "task": {
            "id": "task-1",
            "title": "Prepare launch",
            "workspaceId": "123e4567-e89b-42d3-a456-426614174000",
            "canonicalRevision": 7,
            "canonicalUpdatedAt": UPDATED_AT,
        },
        "subtasks": [{"id": "stale-step", "title": "Old plan", "order": 0}],
    }
    monkeypatch.setattr(fst, "_flowstate_cache_root", lambda: tmp_path)
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen({}, payload),
    )
    assert "result" in json.loads(
        fst._handle_list_subtasks({"taskId": "task-1"})
    )

    def _offline(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(fst.urllib.request, "urlopen", _offline)

    result = json.loads(fst._handle_list_subtasks({"taskId": "task-1"}))

    assert "error" in result
    assert "stale-step" not in json.dumps(result)


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
