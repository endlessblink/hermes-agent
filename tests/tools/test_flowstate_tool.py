"""Tests for the Flow State local API tool module."""

import io
import json
import urllib.error
from unittest.mock import patch

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
        seen["headers"] = dict(req.header_items())
        seen["body"] = None if req.data is None else json.loads(req.data.decode("utf-8"))
        seen["timeout"] = timeout
        return _Response(payload)

    return _urlopen


def _inventory_payload(items=None, **overrides):
    items = items or []
    payload = {
        "source": "flowstate",
        "scope": "all open tasks visible to the authenticated user",
        "scopeKind": "personal",
        "scopeFingerprint": "0123456789abcdef",
        "capturedAt": "2026-07-14T12:00:00.000Z",
        "appVersion": "1.4.260",
        "fresh": True,
        "complete": True,
        "changeSequence": 7,
        "total": len(items),
        "items": items,
        "page": {"limit": 100, "nextCursor": None, "hasMore": False},
    }
    payload.update(overrides)
    return payload


def _inventory_task(**overrides):
    task = {
        "id": "00000000-0000-4000-8000-000000000001",
        "title": "Plan",
        "status": "todo",
        "canonicalRevision": 3,
    }
    task.update(overrides)
    return task


@pytest.fixture(autouse=True)
def flowstate_config(monkeypatch):
    monkeypatch.setattr(fst, "_FLOW_STATE_API_URL", "http://127.0.0.1:5577")
    monkeypatch.setattr(fst, "_FLOW_STATE_API_TOKEN", "token-123")


def test_list_tasks_sends_query_and_bearer_header(monkeypatch):
    seen = {}
    sample = {
        "tasks": [{"id": "t1", "title": "Plan"}],
        "complete": False,
        "scope": "filtered_sample",
        "limit": 5,
        "hasMore": True,
    }
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, sample),
    )

    result = json.loads(fst._handle_list_tasks({"status": "open", "due": "today", "limit": 5}))

    assert result["result"] == sample
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks?status=open&due=today&limit=5"
    assert seen["method"] == "GET"
    assert seen["headers"]["Authorization"] == "Bearer token-123"


def test_list_open_tasks_uses_complete_inventory_boundary(monkeypatch):
    seen = {}
    payload = _inventory_payload()
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen(seen, payload))

    result = json.loads(fst._handle_list_tasks({"status": "open", "limit": 100}))

    assert result["result"] == payload
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/inventory?limit=100"


def test_search_tasks_uses_encoded_query_and_preserves_exact_results(monkeypatch):
    seen = {}
    payload = {
        "ok": True,
        "query": "לשלוח כביסה",
        "tasks": [{"id": "task-1", "title": "לשלוח כביסה", "status": "todo"}],
        "complete": False,
        "scope": "filtered_sample",
        "limit": 25,
        "hasMore": False,
    }
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, payload),
    )

    result = json.loads(fst._handle_search_tasks({"query": "לשלוח כביסה", "limit": 25}))

    assert result["result"] == payload
    assert seen["method"] == "GET"
    assert seen["url"] == (
        "http://127.0.0.1:5577/api/tasks/search?"
        "q=%D7%9C%D7%A9%D7%9C%D7%95%D7%97+%D7%9B%D7%91%D7%99%D7%A1%D7%94&limit=25"
    )
    assert seen["body"] is None


@pytest.mark.parametrize(
    "args,error",
    [
        ({"query": "laundry", "limit": 0}, "limit must be an integer from 1 to 100"),
        ({"query": "laundry", "limit": 101}, "limit must be an integer from 1 to 100"),
        ({"query": "laundry", "limit": "many"}, "limit must be an integer from 1 to 100"),
    ],
)
def test_search_tasks_validates_query_and_limit_without_calling_api(args, error):
    result = json.loads(fst._handle_search_tasks(args))

    assert result["error"] == error
    assert "token-123" not in json.dumps(result)


@pytest.mark.parametrize("query", [None, "", "   ", "*"])
def test_search_tasks_without_exact_query_browses_open_tasks_instead_of_failing(
    monkeypatch,
    query,
):
    seen = {}
    payload = _inventory_payload()
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, payload),
    )

    result = json.loads(fst._handle_search_tasks({"query": query, "limit": 25}))

    assert result["result"] == payload
    assert seen["method"] == "GET"
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/inventory?limit=25"


def test_search_tasks_bounds_broad_model_search_without_failing(monkeypatch):
    seen = {}
    payload = _inventory_payload()
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen(seen, payload))

    result = json.loads(fst._handle_search_tasks({"query": "א", "limit": 50, "status": "open"}))

    assert result["result"] == payload
    assert seen["url"].endswith("/api/tasks/inventory?limit=50")


@pytest.mark.parametrize("code,action", [
    ("signed_out", None),
    ("reauth_required", "sign_in_again"),
    ("sidecar_auth_bridge_failed", "restart_or_sign_in_again"),
])
def test_inventory_preserves_typed_auth_failures(monkeypatch, code, action):
    def _raise(req, timeout):
        body = {"error": code}
        if action: body["action"] = action
        raise urllib.error.HTTPError(
            req.full_url, 503, "Unavailable", {}, io.BytesIO(json.dumps(body).encode())
        )

    monkeypatch.setattr(fst.urllib.request, "urlopen", _raise)
    result = json.loads(fst._handle_list_tasks({}))

    assert result["code"] == code
    assert result.get("action") == action


def test_inventory_rejects_false_complete_receipt(monkeypatch):
    payload = _inventory_payload(total=61, items=[])
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, payload))

    result = json.loads(fst._handle_list_tasks({}))

    assert result["code"] == "invalid_inventory_receipt"
    assert "61" not in json.dumps(result)


def test_inventory_rejects_incomplete_receipt_without_returning_partial_items(monkeypatch):
    payload = _inventory_payload(
        items=[_inventory_task()],
        complete=False,
        page={"limit": 100, "nextCursor": "next", "hasMore": True},
    )
    payload.pop("total")
    payload.pop("changeSequence")
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, payload))

    result = json.loads(fst._handle_list_tasks({}))

    assert result["code"] == "invalid_inventory_receipt"
    assert "00000000-0000-4000-8000-000000000001" not in json.dumps(result)


@pytest.mark.parametrize(
    "task",
    [
        _inventory_task(canonicalRevision=0),
        _inventory_task(canonicalRevision=True),
        {key: value for key, value in _inventory_task().items() if key != "canonicalRevision"},
    ],
)
def test_inventory_requires_positive_canonical_revision_for_every_task(monkeypatch, task):
    payload = _inventory_payload(items=[task])
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, payload))

    result = json.loads(fst._handle_list_tasks({}))

    assert result["code"] == "invalid_inventory_receipt"


def test_inventory_rejects_receipt_not_marked_fresh(monkeypatch):
    payload = _inventory_payload(items=[_inventory_task()], fresh=False)
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, payload))

    result = json.loads(fst._handle_list_tasks({}))

    assert result["code"] == "invalid_inventory_receipt"


def test_create_task_omits_empty_project_id(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"ok": True, "task": {"id": "new-id"}}),
    )

    result = json.loads(fst._handle_create_task({
        "title": "Review budget",
        "description": "Before Friday",
        "priority": "high",
        "dueDate": "2026-07-10",
        "projectId": "",
    }))

    assert result["result"]["task"]["id"] == "new-id"
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks"
    assert seen["body"] == {
        "title": "Review budget",
        "description": "Before Friday",
        "priority": "high",
        "dueDate": "2026-07-10",
        "projectId": None,
    }


_CANONICAL_DIGEST = "a" * 64
_CANONICAL_REQUEST_HASH = "c" * 64
_CANONICAL_PREVIEW_EXPIRY = "2026-07-13T18:30:00.000Z"
_CANONICAL_COMMITTED_AT = "2026-07-13T18:25:01.000Z"
_CANONICAL_UPDATED_AT = "2026-07-13T18:25:00.000Z"


def _canonical_preview_payload(**overrides):
    payload = {
        "ok": True,
        "result": "preview",
        "contractVersion": "task-v1",
        "operationId": "op-123",
        "baseRevision": 7,
        "previewDigest": _CANONICAL_DIGEST,
        "requestHash": _CANONICAL_REQUEST_HASH,
        "previewExpiresAt": _CANONICAL_PREVIEW_EXPIRY,
        "normalizedPayload": {"title": "Clarified task"},
        "readBack": {
            "id": "task-1",
            "title": "Existing task",
            "canonicalRevision": 7,
        },
    }
    payload.update(overrides)
    return payload


def _canonical_committed_payload(*, replayed=False, **receipt_overrides):
    read_back = {
        "id": "task-1",
        "title": "Clarified task",
        "status": "todo",
        "canonicalRevision": 8,
        "canonicalUpdatedAt": _CANONICAL_UPDATED_AT,
    }
    receipt = {
        "ok": True,
        "status": "replayed" if replayed else "committed",
        "contractVersion": "task-v1",
        "operationId": "op-123",
        "requestHash": _CANONICAL_REQUEST_HASH,
        "source": "local-api",
        "entityType": "task",
        "action": "patch",
        "entityId": "task-1",
        "canonicalRevision": 8,
        "canonicalUpdatedAt": _CANONICAL_UPDATED_AT,
        "changeSequence": 42,
        "replayed": replayed,
        "committedAt": _CANONICAL_COMMITTED_AT,
        "readBack": read_back,
        "readBackHash": canonical_json_hash(read_back),
        "affected": [
            {
                "entityType": "task",
                "entityId": "task-1",
                "action": "update",
                "canonicalRevision": 8,
                "changeSequence": 42,
                "readBack": read_back,
                "readBackHash": canonical_json_hash(read_back),
            }
        ],
    }
    receipt.update(receipt_overrides)
    return {
        "ok": True,
        "result": "committed",
        "requestHash": _CANONICAL_REQUEST_HASH,
        "receipt": receipt,
    }


def _canonical_action_payload(
    *,
    action,
    operation_id,
    entity_id,
    read_back,
    affected,
    status="committed",
    receipt_overrides=None,
    outer_overrides=None,
):
    read_back = dict(read_back)
    read_back.setdefault("status", "todo")
    read_back.setdefault("canonicalUpdatedAt", _CANONICAL_UPDATED_AT)
    canonical_affected = []
    for entry in affected:
        canonical_entry = dict(entry)
        affected_read_back = canonical_entry.get("readBack")
        if affected_read_back is None:
            affected_read_back = (
                read_back
                if canonical_entry["entityId"] == entity_id
                else {
                    "id": canonical_entry["entityId"],
                    "canonicalRevision": canonical_entry["canonicalRevision"],
                }
            )
        canonical_entry["readBack"] = affected_read_back
        canonical_entry["readBackHash"] = canonical_json_hash(affected_read_back)
        canonical_affected.append(canonical_entry)
    receipt = {
        "ok": True,
        "status": status,
        "operationId": operation_id,
        "requestHash": _CANONICAL_REQUEST_HASH,
        "contractVersion": "task-v1",
        "source": "local-api",
        "entityType": "task",
        "action": action,
        "entityId": entity_id,
        "canonicalRevision": read_back["canonicalRevision"],
        "changeSequence": 42,
        "committedAt": _CANONICAL_COMMITTED_AT,
        "readBack": read_back,
        "readBackHash": canonical_json_hash(read_back),
        "affected": canonical_affected,
    }
    receipt.update(receipt_overrides or {})
    payload = {
        "ok": True,
        "result": "committed",
        "requestHash": _CANONICAL_REQUEST_HASH,
        "receipt": receipt,
    }
    payload.update(outer_overrides or {})
    return payload


def _sql_done_payload():
    living_task = {
        "id": "task-1",
        "title": "Recurring task",
        "status": "todo",
        "canonicalRevision": 8,
        "canonicalUpdatedAt": _CANONICAL_UPDATED_AT,
    }
    completed_occurrence = {
        "id": "history-1",
        "taskId": "task-1",
        "status": "done",
        "canonicalRevision": 1,
        "canonicalUpdatedAt": _CANONICAL_UPDATED_AT,
        "changeSequence": 41,
    }
    top_read_back = {
        **living_task,
        "completedOccurrence": completed_occurrence,
        "nextOccurrence": {
            "id": "occ-2",
            "taskId": "task-1",
            "status": "todo",
        },
    }
    return {
        "ok": True,
        "result": "committed",
        "requestHash": _CANONICAL_REQUEST_HASH,
        "receipt": {
            "ok": True,
            "status": "committed",
            "contractVersion": "task-v1",
            "operationId": "apply-1",
            "requestHash": _CANONICAL_REQUEST_HASH,
            "source": "local-api",
            "entityType": "task",
            "action": "done_for_now",
            "entityId": "task-1",
            "canonicalRevision": 8,
            "canonicalUpdatedAt": _CANONICAL_UPDATED_AT,
            "changeSequence": 42,
            "committedAt": _CANONICAL_COMMITTED_AT,
            "readBack": top_read_back,
            "readBackHash": canonical_json_hash(top_read_back),
            "affected": [
                {
                    "entityType": "task",
                    "entityId": "task-1",
                    "action": "update",
                    "canonicalRevision": 8,
                    "changeSequence": 42,
                    "readBack": living_task,
                    "readBackHash": canonical_json_hash(living_task),
                },
                {
                    "entityType": "task",
                    "entityId": "history-1",
                    "action": "create",
                    "canonicalRevision": 1,
                    "changeSequence": 41,
                    "readBack": completed_occurrence,
                    "readBackHash": canonical_json_hash(completed_occurrence),
                },
            ],
        },
    }


def _valid_update_args(**overrides):
    args = {
        "id": "task-1",
        "operationId": "op-123",
        "baseRevision": 7,
        "patch": {"title": "Clarified task"},
        "requestHash": _CANONICAL_REQUEST_HASH,
    }
    args.update(overrides)
    return args


def test_update_task_schema_is_preview_first_and_locks_nested_patch_shape():
    schema = fst.FLOWSTATE_UPDATE_TASK_SCHEMA
    params = schema["parameters"]
    patch_schema = params["properties"]["patch"]

    assert params["required"] == ["id", "operationId", "baseRevision", "patch"]
    assert params["additionalProperties"] is False
    assert set(params["properties"]) == {
        "id",
        "operationId",
        "baseRevision",
        "patch",
        "preview",
        "previewDigest",
        "previewExpiresAt",
        "requestHash",
    }
    assert patch_schema["additionalProperties"] is False
    assert set(patch_schema["properties"]) == {
        "title",
        "description",
        "priority",
        "dueDate",
        "progress",
    }
    assert "status" not in patch_schema["properties"]
    assert "preview" in schema["description"].lower()


@pytest.mark.parametrize("missing", ["id", "operationId", "baseRevision", "patch"])
def test_update_task_requires_canonical_identity_before_io(monkeypatch, missing):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("invalid request reached Local Task API"),
    )
    args = _valid_update_args()
    del args[missing]

    result = json.loads(fst._handle_update_task(args))

    assert missing in result["error"]
    assert "token-123" not in json.dumps(result)


def test_update_task_defaults_to_preview_and_forwards_exact_contract(monkeypatch):
    seen = {}
    preview = _canonical_preview_payload()
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, preview),
    )

    result = json.loads(fst._handle_update_task(_valid_update_args()))

    assert result["result"] == preview
    assert seen["method"] == "PATCH"
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/task-1"
    assert seen["body"] == {
        "operationId": "op-123",
        "baseRevision": 7,
        "patch": {"title": "Clarified task"},
        "preview": True,
    }


@pytest.mark.parametrize(
    "args,error_fragment",
    [
        (_valid_update_args(title="legacy flat field"), "unsupported"),
        (_valid_update_args(status="done"), "unsupported"),
        (_valid_update_args(unexpected="value"), "unsupported"),
        (_valid_update_args(patch={"status": "done"}), "status"),
        (_valid_update_args(patch={"unknown": "value"}), "unknown"),
        (_valid_update_args(patch={}), "at least one"),
    ],
)
def test_update_task_rejects_legacy_flat_and_unknown_fields_before_io(
    monkeypatch, args, error_fragment
):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("invalid request reached Local Task API"),
    )

    result = json.loads(fst._handle_update_task(args))

    assert error_fragment in result["error"].lower()


@pytest.mark.parametrize("progress", [True, False, 1.5, -1, 101])
def test_update_task_progress_is_integer_zero_to_one_hundred_before_io(
    monkeypatch, progress
):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("invalid request reached Local Task API"),
    )

    result = json.loads(
        fst._handle_update_task(_valid_update_args(patch={"progress": progress}))
    )

    assert result["error"] == "patch.progress must be an integer from 0 to 100"


@pytest.mark.parametrize("progress", [0, 50, 100])
def test_update_task_accepts_integer_progress_boundaries(monkeypatch, progress):
    seen = {}
    preview = _canonical_preview_payload(
        normalizedPayload={"progress": progress}
    )
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, preview),
    )

    result = json.loads(
        fst._handle_update_task(_valid_update_args(patch={"progress": progress}))
    )

    assert result["result"] == preview
    assert seen["body"]["patch"] == {"progress": progress}


@pytest.mark.parametrize("missing", ["previewDigest", "previewExpiresAt", "requestHash"])
def test_update_task_apply_requires_preview_receipt_fields_before_io(monkeypatch, missing):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("invalid apply reached Local Task API"),
    )
    args = _valid_update_args(
        preview=False,
        previewDigest=_CANONICAL_DIGEST,
        previewExpiresAt=_CANONICAL_PREVIEW_EXPIRY,
        requestHash=_CANONICAL_REQUEST_HASH,
    )
    del args[missing]

    result = json.loads(fst._handle_update_task(args))

    assert result["error"] == f"{missing} is required when preview is false"


@pytest.mark.parametrize("replayed", [False, True])
def test_update_task_apply_forwards_preview_receipt_and_validates_commit(
    monkeypatch, replayed
):
    seen = {}
    committed = _canonical_committed_payload(replayed=replayed)
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, committed),
    )
    args = _valid_update_args(
        preview=False,
        previewDigest=_CANONICAL_DIGEST,
        previewExpiresAt=_CANONICAL_PREVIEW_EXPIRY,
    )

    result = json.loads(fst._handle_update_task(args))

    assert result["result"] == committed
    assert seen["body"] == {
        "operationId": "op-123",
        "baseRevision": 7,
        "patch": {"title": "Clarified task"},
        "preview": False,
        "previewDigest": _CANONICAL_DIGEST,
        "previewExpiresAt": _CANONICAL_PREVIEW_EXPIRY,
        "requestHash": _CANONICAL_REQUEST_HASH,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"operationId": "another-operation"},
        {"entityId": "another-task"},
        {"entityType": "project"},
        {"action": "delete"},
        {"contractVersion": "task-v0"},
        {"source": "other-client"},
        {"canonicalRevision": 0},
        {"canonicalRevision": True},
        {"changeSequence": 0},
        {"changeSequence": 2.5},
        {"canonicalUpdatedAt": ""},
        {"committedAt": None},
        {"status": "queued"},
        {"requestHash": "d" * 64},
        {"readBack": None},
        {"readBack": {"id": "task-1", "canonicalRevision": 7}},
        {"readBack": {"id": "wrong", "canonicalRevision": 8}},
        {"readBackHash": "not-a-sha256"},
        {"affected": []},
        {
            "affected": [
                {
                    "entityType": "task",
                    "entityId": "task-1",
                    "action": "archive",
                    "canonicalRevision": 8,
                    "changeSequence": 42,
                    "readBack": {
                        "id": "task-1",
                        "title": "Clarified task",
                        "canonicalRevision": 8,
                    },
                    "readBackHash": canonical_json_hash({
                        "id": "task-1",
                        "title": "Clarified task",
                        "canonicalRevision": 8,
                    }),
                }
            ]
        },
        {
            "affected": [
                {
                    "entityType": "task",
                    "entityId": "task-1",
                    "action": "update",
                    "canonicalRevision": 7,
                    "changeSequence": 42,
                    "readBack": {"id": "task-1", "canonicalRevision": 7},
                    "readBackHash": canonical_json_hash({
                        "id": "task-1", "canonicalRevision": 7
                    }),
                }
            ]
        },
        {
            "affected": [
                {
                    "entityType": "task",
                    "entityId": "task-1",
                    "action": "update",
                    "canonicalRevision": 8,
                    "changeSequence": 41,
                    "readBack": {
                        "id": "task-1",
                        "title": "Clarified task",
                        "canonicalRevision": 8,
                    },
                    "readBackHash": canonical_json_hash({
                        "id": "task-1",
                        "title": "Clarified task",
                        "canonicalRevision": 8,
                    }),
                }
            ]
        },
        {
            "affected": [
                {
                    "entityType": "task",
                    "entityId": "task-1",
                    "action": "update",
                    "canonicalRevision": 8,
                    "changeSequence": 42,
                    "readBack": {
                        "id": "task-1",
                        "title": "Clarified task",
                        "canonicalRevision": 8,
                    },
                    "readBackHash": "b" * 64,
                }
            ]
        },
    ],
)
def test_update_task_rejects_mismatched_or_malformed_commit_receipt(
    monkeypatch, overrides
):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen({}, _canonical_committed_payload(**overrides)),
    )
    args = _valid_update_args(
        preview=False,
        previewDigest=_CANONICAL_DIGEST,
        previewExpiresAt=_CANONICAL_PREVIEW_EXPIRY,
    )

    result = json.loads(fst._handle_update_task(args))

    assert "error" in result
    assert "canonical" in result["error"].lower() or "receipt" in result["error"].lower()


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True},
        {"ok": True, "result": "queued"},
        _canonical_preview_payload(operationId="another-operation"),
        _canonical_preview_payload(baseRevision=8),
        _canonical_preview_payload(previewDigest="short"),
        _canonical_preview_payload(previewExpiresAt=""),
        _canonical_preview_payload(normalizedPayload=None),
        _canonical_preview_payload(readBack=None),
    ],
)
def test_update_task_rejects_mismatched_malformed_or_queued_preview(monkeypatch, payload):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen({}, payload),
    )

    result = json.loads(fst._handle_update_task(_valid_update_args()))

    assert "error" in result
    assert "preview" in result["error"].lower() or "canonical" in result["error"].lower()


def test_update_task_preserves_typed_stale_revision_and_redacts_details(monkeypatch):
    def _raise(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url,
            409,
            "Conflict",
            {},
            io.BytesIO(json.dumps({
                "error": {
                    "code": "stale_revision",
                    "message": "Task changed since preview.",
                    "authorization": "Bearer secret-from-server",
                },
                "debug": "database secret",
            }).encode("utf-8")),
        )

    monkeypatch.setattr(fst.urllib.request, "urlopen", _raise)

    result = json.loads(fst._handle_update_task(_valid_update_args()))

    assert result == {
        "error": "Task changed since preview.",
        "code": "stale_revision",
        "status": 409,
    }
    assert "token-123" not in json.dumps(result)
    assert "secret" not in json.dumps(result).lower()


def test_update_task_schema_rejects_generic_recurring_completion_guidance():
    description = fst.FLOWSTATE_UPDATE_TASK_SCHEMA["description"]

    assert "recurring" in description.lower()
    assert "Done for now" in description
    assert "not a substitute" in description


def test_delete_task_uses_exact_id(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"ok": True}),
    )

    result = json.loads(fst._handle_delete_task({"id": "task/with/slash"}))

    assert result["result"]["ok"] is True
    assert seen["method"] == "DELETE"
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/task%2Fwith%2Fslash"


def test_unauthorized_error_is_actionable(monkeypatch):
    def _raise(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url,
            401,
            "Unauthorized",
            {},
            None,
        )

    monkeypatch.setattr(fst.urllib.request, "urlopen", _raise)

    result = json.loads(fst._handle_list_tasks({}))

    assert "FLOW_STATE_API_TOKEN" in result["error"]


def test_unavailable_error_mentions_local_api(monkeypatch):
    def _raise(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(fst.urllib.request, "urlopen", _raise)

    result = json.loads(fst._handle_health({}))

    assert "Flow State Local Task API is unavailable" in result["error"]


def test_complete_inventory_never_falls_back_to_a_cached_scope(monkeypatch, tmp_path):
    monkeypatch.setattr(fst, "_flowstate_cache_root", lambda: tmp_path / "profile-a")
    seen = {}
    live_payload = _inventory_payload()
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, live_payload),
    )

    assert json.loads(fst._handle_list_tasks({}))["result"] == live_payload
    assert list((tmp_path / "profile-a").glob("*.json")) == []

    def _offline(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(fst.urllib.request, "urlopen", _offline)
    result = json.loads(fst._handle_list_tasks({}))
    assert "error" in result
    assert "result" not in result


def test_read_through_snapshot_isolated_by_api_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(fst, "_flowstate_cache_root", lambda: tmp_path)
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen({}, {"ok": True, "tasks": [{"id": "private-task"}]}),
    )
    assert "result" in json.loads(fst._handle_list_tasks({"due": "today"}))

    monkeypatch.setattr(fst, "_FLOW_STATE_API_TOKEN", "different-user-token")

    def _offline(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(fst.urllib.request, "urlopen", _offline)
    result = json.loads(fst._handle_list_tasks({"due": "today"}))

    assert "error" in result
    assert "private-task" not in json.dumps(result)


def test_read_through_snapshot_expires_instead_of_becoming_unbounded_authority(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(fst, "_flowstate_cache_root", lambda: tmp_path)
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen({}, {"ok": True, "tasks": [{"id": "stale-task"}]}),
    )
    assert "result" in json.loads(fst._handle_list_tasks({"due": "today"}))

    cache_path = next(tmp_path.glob("*.json"))
    record = json.loads(cache_path.read_text(encoding="utf-8"))
    record["cachedAt"] = "1970-01-01T00:00:00Z"
    cache_path.write_text(json.dumps(record), encoding="utf-8")

    def _offline(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(fst.urllib.request, "urlopen", _offline)
    result = json.loads(fst._handle_list_tasks({"due": "today"}))

    assert "error" in result
    assert "stale-task" not in json.dumps(result)


def test_authoritative_read_can_bypass_an_available_stale_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(fst, "_flowstate_cache_root", lambda: tmp_path)
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen({}, {"ok": True, "taskPressure": {"overdue": 2}}),
    )
    assert fst._request("GET", "/api/assistant/context")["ok"] is True

    def _offline(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(fst.urllib.request, "urlopen", _offline)
    with pytest.raises(RuntimeError, match="unavailable"):
        fst._request("GET", "/api/assistant/context", allow_stale_cache=False)


def test_mutation_never_replays_or_queues_from_read_through_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(fst, "_flowstate_cache_root", lambda: tmp_path)
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen({}, {"ok": True, "tasks": [{"id": "task-1"}]}),
    )
    assert "result" in json.loads(fst._handle_list_tasks({"due": "today"}))
    cache_before = {path.name: path.read_bytes() for path in tmp_path.glob("*.json")}

    def _offline(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(fst.urllib.request, "urlopen", _offline)
    result = json.loads(fst._handle_create_task({"title": "Must not queue"}))

    assert "error" in result
    assert "unavailable" in result["error"].lower()
    assert {path.name: path.read_bytes() for path in tmp_path.glob("*.json")} == cache_before
    assert list(tmp_path.glob("*queue*")) == []


def test_volatile_timer_read_does_not_use_stale_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(fst, "_flowstate_cache_root", lambda: tmp_path)
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen({}, {"ok": True, "timer": {"id": "timer-1"}}),
    )
    assert "result" in json.loads(fst._handle_current_timer({}))
    assert list(tmp_path.glob("*.json")) == []

    def _offline(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(fst.urllib.request, "urlopen", _offline)
    result = json.loads(fst._handle_current_timer({}))

    assert "error" in result
    assert "timer-1" not in json.dumps(result)


def test_availability_allows_running_default_sidecar_without_token(monkeypatch):
    monkeypatch.setattr(fst, "_FLOW_STATE_API_TOKEN", "")
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"ok": True}),
    )

    assert fst._check_flowstate_available() is True
    assert seen["url"] == "http://127.0.0.1:5577/api/health"


def test_availability_hides_missing_default_sidecar_without_token(monkeypatch):
    monkeypatch.setattr(fst, "_FLOW_STATE_API_TOKEN", "")

    def _raise(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(fst.urllib.request, "urlopen", _raise)

    assert fst._check_flowstate_available() is False


def test_health_uses_existing_api_contract(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"ok": True}),
    )

    result = json.loads(fst._handle_health({}))

    assert result["result"] == {"ok": True}
    assert seen["url"] == "http://127.0.0.1:5577/api/health"


def test_get_assistant_context_reads_safe_context(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"ok": True, "taskPressure": {"todayCount": 2}}),
    )

    result = json.loads(fst._handle_assistant_context({}))

    assert result["result"]["taskPressure"]["todayCount"] == 2
    assert seen["method"] == "GET"
    assert seen["url"] == "http://127.0.0.1:5577/api/assistant/context"


def test_list_task_instances_uses_exact_task_id(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"ok": True, "instances": []}),
    )

    result = json.loads(fst._handle_list_task_instances({"id": "task/with/slash"}))

    assert result["result"]["instances"] == []
    assert seen["method"] == "GET"
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/task%2Fwith%2Fslash/instances"


def test_schedule_task_instance_defaults_to_preview(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"ok": True, "preview": True}),
    )

    result = json.loads(fst._handle_schedule_task_instance({
        "id": "task-1",
        "scheduledDate": "2026-07-08",
        "scheduledTime": "10:30",
        "duration": 25,
    }))

    assert result["result"]["preview"] is True
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/task-1/instances"
    assert seen["body"]["preview"] is True


def test_schedule_task_instance_apply_requires_explicit_false(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"ok": True, "preview": False}),
    )

    result = json.loads(fst._handle_schedule_task_instance({
        "id": "task-1",
        "scheduledDate": "2026-07-08",
        "scheduledTime": "10:30",
        "duration": 25,
        "preview": False,
    }))

    assert result["result"]["preview"] is False
    assert seen["body"] == {
        "duration": 25,
        "preview": False,
        "scheduledDate": "2026-07-08",
        "scheduledTime": "10:30",
    }


def test_schedule_task_instance_validation_returns_safe_error():
    result = json.loads(fst._handle_schedule_task_instance({
        "id": "task-1",
        "scheduledDate": "07/08/2026",
        "scheduledTime": "25:99",
        "duration": 0,
    }))

    assert result["error"] == "scheduledDate must be YYYY-MM-DD"
    assert "token-123" not in json.dumps(result)


def test_done_for_now_defaults_to_non_mutating_preview_and_uses_exact_task_id(monkeypatch):
    seen = {}
    payload = {
        "ok": True,
        "preview": True,
        "requestId": "preview-1",
        "previewVersion": "version-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
        "verification": {"taskId": "task/one", "nextDueDate": "2026-07-16"},
    }
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, payload),
    )

    result = json.loads(fst._handle_done_for_now({
        "taskId": "task/one",
        "nextDueDate": "2026-07-16",
    }))

    assert result["result"] == payload
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/task%2Fone/done-for-now"
    assert seen["body"] == {"nextDueDate": "2026-07-16", "preview": True}


def test_get_task_reads_one_exact_encoded_id(monkeypatch):
    seen = {}
    payload = {"ok": True, "task": {"id": "task/one", "title": "Exact task"}}
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen(seen, payload))

    result = json.loads(fst._handle_get_task({"taskId": "task/one"}))

    assert result["result"] == payload
    assert seen["method"] == "GET"
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/task%2Fone"


def test_get_task_requires_exact_id_without_leaking_auth():
    result = json.loads(fst._handle_get_task({}))
    assert result["error"] == "taskId is required"
    assert "token-123" not in json.dumps(result)


@pytest.mark.parametrize(
    "args,error",
    [
        ({}, "taskId is required"),
        ({"taskId": "task-1", "nextDueDate": "07/16/2026"}, "nextDueDate must be YYYY-MM-DD"),
        ({"taskId": "task-1", "preview": False, "previewVersion": "version-1"}, "requestId is required when preview is false"),
        ({"taskId": "task-1", "preview": False, "requestId": "apply-1"}, "previewVersion is required when preview is false"),
        (
            {
                "taskId": "task-1",
                "preview": False,
                "requestId": "apply-1",
                "previewVersion": "version-1",
            },
            "requestHash is required when preview is false",
        ),
    ],
)
def test_done_for_now_validates_exact_preview_apply_contract(args, error):
    result = json.loads(fst._handle_done_for_now(args))

    assert result["error"] == error
    assert "token-123" not in json.dumps(result)


@pytest.mark.parametrize("status", ["committed", "replayed"])
def test_done_for_now_apply_forwards_preview_receipt_and_returns_readback(
    monkeypatch, status
):
    seen = {}
    read_back = {
        "id": "task-1",
        "canonicalRevision": 8,
        "completedOccurrence": {
            "id": "history-1",
            "status": "done",
            "canonicalRevision": 1,
            "changeSequence": 41,
        },
        "nextOccurrence": {
            "id": "occ-2",
            "taskId": "task-1",
            "status": "todo",
            "dueDate": "2026-07-16",
        },
    }
    payload = _canonical_action_payload(
        action="done_for_now",
        operation_id="apply-1",
        entity_id="task-1",
        read_back=read_back,
        affected=[
            {
                "entityType": "task",
                "entityId": "task-1",
                "action": "update",
                "canonicalRevision": 8,
                "changeSequence": 42,
            },
            {
                "entityType": "task",
                "entityId": "history-1",
                "action": "create",
                "canonicalRevision": 1,
                "changeSequence": 41,
            },
        ],
        status=status,
    )
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, payload),
    )

    result = json.loads(fst._handle_done_for_now({
        "taskId": "task-1",
        "nextDueDate": "2026-07-16",
        "preview": False,
        "requestId": "apply-1",
        "previewVersion": "version-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
    }))

    assert result["result"] == payload
    assert seen["body"] == {
        "nextDueDate": "2026-07-16",
        "preview": False,
        "previewVersion": "version-1",
        "requestId": "apply-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
    }


def test_done_for_now_accepts_sql_receipt_with_enriched_top_read_back(monkeypatch):
    payload = _sql_done_payload()
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, payload))

    result = json.loads(fst._handle_done_for_now({
        "taskId": "task-1",
        "preview": False,
        "requestId": "apply-1",
        "previewVersion": "version-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
    }))

    assert result["result"] == payload


@pytest.mark.parametrize(("field", "value"), [("title", "Forged"), ("status", "done")])
def test_done_for_now_rejects_forged_primary_subset_with_valid_hash(
    monkeypatch, field, value
):
    payload = _sql_done_payload()
    primary = payload["receipt"]["affected"][0]
    primary["readBack"][field] = value
    primary["readBackHash"] = canonical_json_hash(primary["readBack"])
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, payload))

    result = json.loads(fst._handle_done_for_now({
        "taskId": "task-1",
        "preview": False,
        "requestId": "apply-1",
        "previewVersion": "version-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
    }))

    assert result["error"] == "Canonical Done for now receipt could not be verified"


@pytest.mark.parametrize(
    ("field", "value"),
    [("canonicalRevision", 2), ("changeSequence", 40)],
)
def test_done_for_now_rejects_completed_occurrence_proof_mismatch(
    monkeypatch, field, value
):
    payload = _sql_done_payload()
    payload["receipt"]["readBack"]["completedOccurrence"][field] = value
    payload["receipt"]["readBackHash"] = canonical_json_hash(
        payload["receipt"]["readBack"]
    )
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, payload))

    result = json.loads(fst._handle_done_for_now({
        "taskId": "task-1",
        "preview": False,
        "requestId": "apply-1",
        "previewVersion": "version-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
    }))

    assert result["error"] == "Canonical Done for now receipt could not be verified"


def test_done_for_now_rejects_reordered_affected_rows(monkeypatch):
    read_back = {
        "id": "task-1",
        "canonicalRevision": 8,
        "completedOccurrence": {"id": "history-1"},
        "nextOccurrence": {"id": "occ-2", "taskId": "task-1"},
    }
    payload = _canonical_action_payload(
        action="done_for_now",
        operation_id="apply-1",
        entity_id="task-1",
        read_back=read_back,
        affected=[
            {
                "entityType": "task",
                "entityId": "task-1",
                "action": "update",
                "canonicalRevision": 8,
                "changeSequence": 42,
            },
            {
                "entityType": "task",
                "entityId": "history-1",
                "action": "create",
                "canonicalRevision": 1,
                "changeSequence": 41,
            },
        ],
    )
    payload["receipt"]["affected"].reverse()
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, payload))

    result = json.loads(fst._handle_done_for_now({
        "taskId": "task-1",
        "preview": False,
        "requestId": "apply-1",
        "previewVersion": "version-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
    }))

    assert result["error"] == "Canonical Done for now receipt could not be verified"


@pytest.mark.parametrize(
    "payload_overrides",
    [
        {"outer_overrides": {"requestHash": "d" * 64}},
        {"receipt_overrides": {"operationId": "other-operation"}},
        {"receipt_overrides": {"readBackHash": "e" * 64}},
        {"receipt_overrides": {"affected": []}},
        {
            "receipt_overrides": {
                "affected": [
                    {
                        "entityType": "task",
                        "entityId": "history-1",
                        "action": "update",
                        "canonicalRevision": 1,
                        "changeSequence": 41,
                    },
                    {
                        "entityType": "task",
                        "entityId": "task-1",
                        "action": "create",
                        "canonicalRevision": 8,
                        "changeSequence": 42,
                    },
                ]
            }
        },
        {
            "receipt_overrides": {
                "affected": [
                    {
                        "entityType": "task",
                        "entityId": "unrelated-task",
                        "action": "update",
                        "canonicalRevision": 8,
                        "changeSequence": 42,
                    },
                    {
                        "entityType": "task",
                        "entityId": "history-1",
                        "action": "create",
                        "canonicalRevision": 1,
                        "changeSequence": 43,
                    },
                ]
            }
        },
    ],
)
def test_done_for_now_rejects_unverified_success_receipts(monkeypatch, payload_overrides):
    read_back = {
        "id": "task-1",
        "canonicalRevision": 8,
        "completedOccurrence": {"id": "history-1"},
        "nextOccurrence": {"id": "occ-2", "taskId": "task-1"},
    }
    payload = _canonical_action_payload(
        action="done_for_now",
        operation_id="apply-1",
        entity_id="task-1",
        read_back=read_back,
        affected=[
            {
                "entityType": "task",
                "entityId": "task-1",
                "action": "update",
                "canonicalRevision": 8,
                "changeSequence": 42,
            },
            {
                "entityType": "task",
                "entityId": "history-1",
                "action": "create",
                "canonicalRevision": 1,
                "changeSequence": 41,
            },
        ],
        **payload_overrides,
    )
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, payload))

    result = json.loads(
        fst._handle_done_for_now(
            {
                "taskId": "task-1",
                "preview": False,
                "requestId": "apply-1",
                "previewVersion": "version-1",
                "requestHash": _CANONICAL_REQUEST_HASH,
            }
        )
    )

    assert result["error"] == "Canonical Done for now receipt could not be verified"


def test_done_for_now_rejects_same_living_and_completion_identity(monkeypatch):
    read_back = {
        "id": "task-1",
        "canonicalRevision": 8,
        "completedOccurrence": {"id": "task-1"},
        "nextOccurrence": {"id": "occ-2", "taskId": "task-1"},
    }
    payload = _canonical_action_payload(
        action="done_for_now",
        operation_id="apply-1",
        entity_id="task-1",
        read_back=read_back,
        affected=[
            {
                "entityType": "task",
                "entityId": "task-1",
                "action": "create",
                "canonicalRevision": 1,
                "changeSequence": 41,
            },
            {
                "entityType": "task",
                "entityId": "task-1",
                "action": "update",
                "canonicalRevision": 8,
                "changeSequence": 42,
            },
        ],
    )
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, payload))

    result = json.loads(fst._handle_done_for_now({
        "taskId": "task-1",
        "preview": False,
        "requestId": "apply-1",
        "previewVersion": "version-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
    }))

    assert result["error"] == "Canonical Done for now receipt could not be verified"


@pytest.mark.parametrize(
    "field,value",
    [
        ("requestId", " apply-1 "),
        ("requestHash", f" {_CANONICAL_REQUEST_HASH} "),
    ],
)
def test_done_for_now_rejects_padded_approval_bindings_before_io(
    monkeypatch, field, value
):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("padded binding reached Local Task API"),
    )
    args = {
        "taskId": "task-1",
        "preview": False,
        "requestId": "apply-1",
        "previewVersion": "version-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
        field: value,
    }

    result = json.loads(fst._handle_done_for_now(args))

    assert "error" in result


def test_done_for_now_preserves_typed_api_conflict_without_exposing_secrets(monkeypatch):
    def _raise(req, timeout):
        body = json.dumps({
            "error": {"code": "stale_preview", "message": "Preview no longer matches current state"},
            "debug": "Bearer secret-that-must-not-leak",
        }).encode("utf-8")
        raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, io.BytesIO(body))

    monkeypatch.setattr(fst.urllib.request, "urlopen", _raise)

    result = json.loads(fst._handle_done_for_now({
        "taskId": "task-1",
        "preview": False,
        "requestId": "apply-1",
        "previewVersion": "version-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
    }))

    assert result == {
        "error": "Preview no longer matches current state",
        "code": "stale_preview",
        "status": 409,
    }
    assert "secret-that-must-not-leak" not in json.dumps(result)


def test_merge_tasks_defaults_to_non_mutating_preview_with_exact_ids(monkeypatch):
    seen = {}
    payload = {
        "ok": True,
        "preview": True,
        "requestId": "merge-preview-1",
        "previewVersion": "merge-version-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
        "survivor": {"id": "survivor/1", "title": "Keep me"},
        "duplicate": {"id": "duplicate/1", "title": "Merge me"},
        "transfers": ["subtasks", "instances"],
    }
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, payload),
    )

    result = json.loads(fst._handle_merge_tasks({
        "survivorTaskId": "survivor/1",
        "duplicateTaskId": "duplicate/1",
    }))

    assert result["result"] == payload
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/survivor%2F1/merge"
    assert seen["body"] == {"duplicateTaskId": "duplicate/1", "preview": True}


def test_merge_tasks_forwards_explicit_recurrence_resolution(monkeypatch):
    seen = {}
    payload = {
        "ok": True,
        "preview": True,
        "requestId": "recurrence-merge-preview-1",
        "previewVersion": "recurrence-merge-v1",
        "requestHash": _CANONICAL_REQUEST_HASH,
        "recurrenceResolution": {"pattern": "daily", "interval": 3, "endType": "never"},
    }
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, payload),
    )

    result = json.loads(fst._handle_merge_tasks({
        "survivorTaskId": "survivor-1",
        "duplicateTaskId": "duplicate-1",
        "recurrenceResolution": {"pattern": "daily", "interval": 3, "endType": "never"},
    }))

    assert result["result"] == payload
    assert seen["body"] == {
        "duplicateTaskId": "duplicate-1",
        "preview": True,
        "recurrenceResolution": {"pattern": "daily", "interval": 3, "endType": "never"},
    }


def test_merge_tasks_preserves_stop_action_for_unresolved_recurrence(monkeypatch):
    def _raise(req, timeout):
        body = json.dumps({
            "ok": False,
            "error": {
                "code": "incompatible_recurrence",
                "message": "Recurring definitions or chain identities are incompatible",
            },
            "action": "stop_mutations_and_request_recurrence_resolution",
        }).encode("utf-8")
        raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, io.BytesIO(body))

    monkeypatch.setattr(fst.urllib.request, "urlopen", _raise)

    result = json.loads(fst._handle_merge_tasks({
        "survivorTaskId": "survivor-1",
        "duplicateTaskId": "duplicate-1",
    }))

    assert result == {
        "error": "Recurring definitions or chain identities are incompatible",
        "code": "incompatible_recurrence",
        "status": 409,
        "action": "stop_mutations_and_request_recurrence_resolution",
    }


@pytest.mark.parametrize("rule", [
    {"pattern": "daily", "interval": 0, "endType": "never"},
    {"pattern": "weekly", "interval": 1, "endType": "never"},
    {"pattern": "daily", "interval": 3},
    {"pattern": "daily", "interval": 3, "endType": "never", "guess": True},
])
def test_merge_tasks_rejects_noncanonical_recurrence_resolution(rule):
    result = json.loads(fst._handle_merge_tasks({
        "survivorTaskId": "survivor-1",
        "duplicateTaskId": "duplicate-1",
        "recurrenceResolution": rule,
    }))

    assert result["error"] == "recurrenceResolution must be a canonical recurrence rule"


@pytest.mark.parametrize(
    "args,error",
    [
        ({"duplicateTaskId": "duplicate-1"}, "survivorTaskId is required"),
        ({"survivorTaskId": "survivor-1"}, "duplicateTaskId is required"),
        (
            {"survivorTaskId": "same", "duplicateTaskId": "same"},
            "survivorTaskId and duplicateTaskId must be different",
        ),
        (
            {"survivorTaskId": "survivor-1", "duplicateTaskId": "duplicate-1", "preview": False, "previewVersion": "v1"},
            "requestId is required when preview is false",
        ),
        (
            {"survivorTaskId": "survivor-1", "duplicateTaskId": "duplicate-1", "preview": False, "requestId": "r1"},
            "previewVersion is required when preview is false",
        ),
        (
            {
                "survivorTaskId": "survivor-1",
                "duplicateTaskId": "duplicate-1",
                "preview": False,
                "requestId": "r1",
                "previewVersion": "v1",
            },
            "requestHash is required when preview is false",
        ),
    ],
)
def test_merge_tasks_validates_exact_preview_apply_contract(args, error):
    result = json.loads(fst._handle_merge_tasks(args))

    assert result["error"] == error
    assert "token-123" not in json.dumps(result)


@pytest.mark.parametrize("status", ["committed", "replayed"])
def test_merge_tasks_apply_forwards_preview_binding_and_receipt(monkeypatch, status):
    seen = {}
    read_back = {
        "id": "survivor-1",
        "canonicalRevision": 8,
        "survivorTaskId": "survivor-1",
        "duplicateTaskId": "duplicate-1",
        "duplicateArchived": True,
    }
    payload = _canonical_action_payload(
        action="merge",
        operation_id="merge-apply-1",
        entity_id="survivor-1",
        read_back=read_back,
        affected=[
            {
                "entityType": "task",
                "entityId": "survivor-1",
                "action": "update",
                "canonicalRevision": 8,
                "changeSequence": 42,
            },
            {
                "entityType": "task",
                "entityId": "duplicate-1",
                "action": "archive",
                "canonicalRevision": 5,
                "changeSequence": 43,
            },
        ],
        status=status,
    )
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, payload),
    )

    result = json.loads(fst._handle_merge_tasks({
        "survivorTaskId": "survivor-1",
        "duplicateTaskId": "duplicate-1",
        "preview": False,
        "requestId": "merge-apply-1",
        "previewVersion": "merge-version-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
    }))

    assert result["result"] == payload
    assert seen["body"] == {
        "duplicateTaskId": "duplicate-1",
        "preview": False,
        "previewVersion": "merge-version-1",
        "requestId": "merge-apply-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
    }


def test_merge_tasks_rejects_reordered_affected_rows(monkeypatch):
    read_back = {
        "id": "survivor-1",
        "canonicalRevision": 8,
        "survivorTaskId": "survivor-1",
        "duplicateTaskId": "duplicate-1",
        "duplicateArchived": True,
    }
    payload = _canonical_action_payload(
        action="merge",
        operation_id="merge-apply-1",
        entity_id="survivor-1",
        read_back=read_back,
        affected=[
            {
                "entityType": "task",
                "entityId": "survivor-1",
                "action": "update",
                "canonicalRevision": 8,
                "changeSequence": 42,
            },
            {
                "entityType": "task",
                "entityId": "duplicate-1",
                "action": "archive",
                "canonicalRevision": 5,
                "changeSequence": 43,
            },
        ],
    )
    payload["receipt"]["affected"].reverse()
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, payload))

    result = json.loads(fst._handle_merge_tasks({
        "survivorTaskId": "survivor-1",
        "duplicateTaskId": "duplicate-1",
        "preview": False,
        "requestId": "merge-apply-1",
        "previewVersion": "merge-version-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
    }))

    assert result["error"] == "Canonical merge receipt could not be verified"


@pytest.mark.parametrize(
    "payload_overrides",
    [
        {"outer_overrides": {"requestHash": "d" * 64}},
        {"receipt_overrides": {"operationId": "other-operation"}},
        {"receipt_overrides": {"readBackHash": "e" * 64}},
        {"receipt_overrides": {"affected": []}},
        {
            "receipt_overrides": {
                "affected": [
                    {
                        "entityType": "task",
                        "entityId": "survivor-1",
                        "action": "archive",
                        "canonicalRevision": 8,
                        "changeSequence": 42,
                    },
                    {
                        "entityType": "task",
                        "entityId": "duplicate-1",
                        "action": "update",
                        "canonicalRevision": 5,
                        "changeSequence": 43,
                    },
                ]
            }
        },
    ],
)
def test_merge_tasks_rejects_unverified_success_receipts(monkeypatch, payload_overrides):
    read_back = {
        "id": "survivor-1",
        "canonicalRevision": 8,
        "survivorTaskId": "survivor-1",
        "duplicateTaskId": "duplicate-1",
        "duplicateArchived": True,
    }
    payload = _canonical_action_payload(
        action="merge",
        operation_id="merge-apply-1",
        entity_id="survivor-1",
        read_back=read_back,
        affected=[
            {
                "entityType": "task",
                "entityId": "survivor-1",
                "action": "update",
                "canonicalRevision": 8,
                "changeSequence": 42,
            },
            {
                "entityType": "task",
                "entityId": "duplicate-1",
                "action": "archive",
                "canonicalRevision": 5,
                "changeSequence": 43,
            },
        ],
        **payload_overrides,
    )
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, payload))

    result = json.loads(
        fst._handle_merge_tasks(
            {
                "survivorTaskId": "survivor-1",
                "duplicateTaskId": "duplicate-1",
                "preview": False,
                "requestId": "merge-apply-1",
                "previewVersion": "merge-version-1",
                "requestHash": _CANONICAL_REQUEST_HASH,
            }
        )
    )

    assert result["error"] == "Canonical merge receipt could not be verified"


@pytest.mark.parametrize(
    "field,value",
    [
        ("requestId", " merge-apply-1 "),
        ("requestHash", f" {_CANONICAL_REQUEST_HASH} "),
    ],
)
def test_merge_tasks_rejects_padded_approval_bindings_before_io(
    monkeypatch, field, value
):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("padded binding reached Local Task API"),
    )
    args = {
        "survivorTaskId": "survivor-1",
        "duplicateTaskId": "duplicate-1",
        "preview": False,
        "requestId": "merge-apply-1",
        "previewVersion": "merge-version-1",
        "requestHash": _CANONICAL_REQUEST_HASH,
        field: value,
    }

    result = json.loads(fst._handle_merge_tasks(args))

    assert "error" in result


def test_timer_diagnostics_reads_safe_leader_and_sync_state(monkeypatch):
    seen = {}
    payload = {
        "appVersion": "1.2.3",
        "mode": "token",
        "hasAuthContext": True,
        "currentTimerBranch": "local-snapshot-active",
        "localSnapshotActive": True,
        "supabaseLookupOk": True,
        "supabaseActiveSessionFound": True,
    }
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, payload),
    )

    result = json.loads(fst._handle_timer_diagnostics({}))

    assert result["result"] == payload
    assert seen["method"] == "GET"
    assert seen["url"] == "http://127.0.0.1:5577/api/timer/diagnostics"
    assert seen["body"] is None


def test_list_subtasks_uses_parent_task_route(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"ok": True, "subtasks": []}),
    )

    result = json.loads(fst._handle_list_subtasks({"taskId": "task/one"}))

    assert result["result"]["subtasks"] == []
    assert seen["method"] == "GET"
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/task%2Fone/subtasks"


def test_create_subtask_defaults_to_non_mutating_preview(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"ok": True, "preview": True, "receipt": {"requestId": "r-1"}}),
    )

    result = json.loads(fst._handle_create_subtask({
        "taskId": "task-1",
        "title": "Draft outline",
        "order": 2,
        "requestId": "r-1",
    }))

    assert result["result"]["preview"] is True
    assert seen["body"] == {
        "order": 2,
        "preview": True,
        "requestId": "r-1",
        "title": "Draft outline",
    }


def test_update_subtask_apply_sends_explicit_request_metadata(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"ok": True, "preview": False, "receipt": {"requestId": "r-2"}}),
    )

    result = json.loads(fst._handle_update_subtask({
        "taskId": "task-1",
        "subtaskId": "sub/1",
        "title": "Revised",
        "completed": True,
        "order": 1,
        "preview": False,
        "requestId": "r-2",
    }))

    assert result["result"]["receipt"]["requestId"] == "r-2"
    assert seen["method"] == "PATCH"
    assert seen["url"].endswith("/api/tasks/task-1/subtasks/sub%2F1")
    assert seen["body"] == {
        "completed": True,
        "order": 1,
        "preview": False,
        "requestId": "r-2",
        "title": "Revised",
    }


def test_delete_subtask_defaults_to_preview_and_uses_post_preview_route(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"ok": True, "preview": True}),
    )

    result = json.loads(fst._handle_delete_subtask({
        "taskId": "task-1",
        "subtaskId": "sub-1",
        "requestId": "delete-1",
    }))

    assert result["result"]["preview"] is True
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/tasks/task-1/subtasks/sub-1/delete")
    assert seen["body"] == {"preview": True, "requestId": "delete-1"}


def test_subtask_batch_defaults_to_preview_and_preserves_operations(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"ok": True, "preview": True, "receipt": {"operationCount": 2}}),
    )
    operations = [
        {"action": "create", "title": "First", "order": 0},
        {"action": "update", "subtaskId": "sub-2", "completed": True},
    ]

    result = json.loads(fst._handle_subtask_batch({"taskId": "task-1", "operations": operations}))

    assert result["result"]["receipt"]["operationCount"] == 2
    assert seen["url"].endswith("/api/tasks/task-1/subtasks/batch")
    assert seen["body"] == {"operations": operations, "preview": True}


def test_subtask_batch_apply_requires_request_id():
    result = json.loads(fst._handle_subtask_batch({
        "taskId": "task-1",
        "operations": [{"action": "delete", "subtaskId": "sub-1"}],
        "preview": False,
    }))

    assert result["error"] == "requestId is required when preview is false"


@pytest.mark.parametrize("handler,args,error", [
    (fst._handle_create_subtask, {"taskId": "t"}, "title is required"),
    (fst._handle_update_subtask, {"taskId": "t", "subtaskId": "s"}, "provide at least one field"),
    (fst._handle_delete_subtask, {"taskId": "t"}, "subtaskId is required"),
])
def test_subtask_validation_is_local_and_safe(handler, args, error):
    result = json.loads(handler(args))

    assert error in result["error"]
    assert "token-123" not in json.dumps(result)


def test_toolset_registration_maps_all_flowstate_tools():
    from tools.registry import registry

    expected = {
        "flowstate_get_assistant_context",
        "flowstate_health",
        "flowstate_list_tasks",
        "flowstate_search_tasks",
        "flowstate_create_task",
        "flowstate_update_task",
        "flowstate_delete_task",
        "flowstate_get_current_timer",
        "flowstate_get_timer_diagnostics",
        "flowstate_list_task_instances",
        "flowstate_schedule_task_instance",
        "flowstate_done_for_now",
        "flowstate_merge_tasks",
        "flowstate_list_subtasks",
        "flowstate_create_subtask",
        "flowstate_update_subtask",
        "flowstate_delete_subtask",
        "flowstate_subtask_batch",
    }

    for tool in expected:
        assert registry.get_toolset_for_tool(tool) == "flowstate"


def test_flowstate_module_is_discovered_as_builtin_tool_module():
    from pathlib import Path
    from tools.registry import _module_registers_tools

    assert _module_registers_tools(Path(fst.__file__)) is True


def test_flowstate_schemas_require_real_tool_use_for_task_requests():
    create_description = fst.FLOWSTATE_CREATE_TASK_SCHEMA["description"]
    list_description = fst.FLOWSTATE_LIST_TASKS_SCHEMA["description"]

    assert "call this tool" in create_description
    assert "hermes-ui/task-triage" in create_description
    assert "instead of this tool" in list_description
    assert "complete=false" in list_description
    assert "cannot prove a total" in list_description


def test_done_for_now_schema_is_preview_first_and_apply_is_receipt_bound():
    schema = fst.FLOWSTATE_DONE_FOR_NOW_SCHEMA

    assert schema["name"] == "flowstate_done_for_now"
    assert schema["parameters"]["required"] == ["taskId"]
    assert "Defaults to preview" in schema["description"]
    assert "generic" in schema["description"].lower()
    assert set(schema["parameters"]["properties"]) == {
        "taskId",
        "nextDueDate",
        "preview",
        "requestId",
        "previewVersion",
        "requestHash",
    }


def test_timer_diagnostics_schema_is_read_only_and_verification_focused():
    schema = fst.FLOWSTATE_TIMER_DIAGNOSTICS_SCHEMA

    assert schema["name"] == "flowstate_get_timer_diagnostics"
    assert "read-only" in schema["description"].lower()
    assert "leader" in schema["description"].lower()
    assert schema["parameters"] == {"type": "object", "properties": {}, "required": []}


def test_search_tasks_schema_is_read_only_and_supports_safe_browsing():
    schema = fst.FLOWSTATE_SEARCH_TASKS_SCHEMA

    assert schema["name"] == "flowstate_search_tasks"
    assert "read-only" in schema["description"].lower()
    assert schema["parameters"]["required"] == []
    assert "omit" in schema["description"].lower()
    assert "open tasks" in schema["description"].lower()
    assert "not" in schema["description"].lower()
    assert "pagination" in schema["description"].lower()
    assert "complete=false" in schema["description"]
    assert "cannot prove a total" in schema["description"]
    assert schema["parameters"]["properties"]["limit"]["maximum"] == 25
    assert set(schema["parameters"]["properties"]) == {"query", "limit"}


def test_merge_tasks_schema_is_preview_first_and_exact_id_bound():
    schema = fst.FLOWSTATE_MERGE_TASKS_SCHEMA

    assert schema["name"] == "flowstate_merge_tasks"
    assert schema["parameters"]["required"] == ["survivorTaskId", "duplicateTaskId"]
    assert "recurrenceResolution" in schema["parameters"]["properties"]
    assert "stop all further Flow State mutations" in schema["description"]
    assert "Defaults to preview" in schema["description"]
    assert "title similarity" in schema["description"].lower()
    assert set(schema["parameters"]["properties"]) == {
        "survivorTaskId",
        "duplicateTaskId",
        "recurrenceResolution",
        "preview",
        "requestId",
        "previewVersion",
        "requestHash",
    }
