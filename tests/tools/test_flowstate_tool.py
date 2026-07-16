"""Tests for the Flow State local API tool module."""

import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tools import flowstate_tool as fst
from tools.flowstate_receipts import canonical_json_sha256, postgres_jsonb_sha256


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
    items = [
        {"canonicalRevision": index + 1, **item}
        for index, item in enumerate(items)
    ]
    payload = {
        "source": "flowstate",
        "scope": "all open tasks visible to the authenticated user",
        "scopeKind": "personal",
        "scopeFingerprint": "0123456789abcdef",
        "capturedAt": datetime.now(timezone.utc).isoformat(),
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


@pytest.fixture(autouse=True)
def flowstate_config(monkeypatch):
    monkeypatch.setattr(fst, "_FLOW_STATE_API_URL", "http://127.0.0.1:5577")
    monkeypatch.setattr(fst, "_FLOW_STATE_API_TOKEN", "token-123")


def test_list_tasks_sends_query_and_bearer_header(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"tasks": [{"id": "t1", "title": "Plan"}]}),
    )

    result = json.loads(fst._handle_list_tasks({"status": "open", "due": "today", "limit": 5}))

    assert result["result"]["tasks"][0]["id"] == "t1"
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


def test_inventory_rejects_stale_evidence_even_when_server_marks_it_fresh(monkeypatch):
    payload = _inventory_payload(capturedAt="2020-01-01T00:00:00Z")
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, payload))

    result = json.loads(fst._handle_list_tasks({}))

    assert result["code"] == "invalid_inventory_receipt"


def test_inventory_rejects_rows_without_canonical_revision(monkeypatch):
    payload = _inventory_payload(items=[{"id": "123e4567-e89b-42d3-a456-426614174000"}])
    del payload["items"][0]["canonicalRevision"]
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, payload))

    result = json.loads(fst._handle_list_tasks({}))

    assert result["code"] == "invalid_inventory_receipt"


_LIFECYCLE_TASK_ID = "1cf40000-0000-4000-8000-000000000101"
_LIFECYCLE_OPERATION_ID = "lifecycle-op-123"
_LIFECYCLE_EXPIRY = "2099-07-15T18:30:00.000Z"


def _lifecycle_preview(*, action="create", base_revision=0, payload=None, **overrides):
    payload = payload if payload is not None else {
        "title": "Review budget",
        "description": "Before Friday",
        "status": "planned",
        "priority": "high",
        "dueDate": "2026-07-18",
        "projectId": "2cf40000-0000-4000-8000-000000000001",
    }
    normalized_payload = {
        "contractVersion": "task-lifecycle-v1",
        "source": "local-api",
        "action": action,
        "taskId": _LIFECYCLE_TASK_ID,
        "baseRevision": base_revision,
        "workspaceId": None,
        "payload": payload,
    }
    response = {
        "ok": True,
        "result": "preview",
        "contractVersion": "task-lifecycle-v1",
        "operationId": _LIFECYCLE_OPERATION_ID,
        "action": action,
        "taskId": _LIFECYCLE_TASK_ID,
        "baseRevision": base_revision,
        "requestHash": canonical_json_sha256(normalized_payload),
        "previewDigest": "a" * 64,
        "previewExpiresAt": _LIFECYCLE_EXPIRY,
        "normalizedPayload": normalized_payload,
    }
    response.update(overrides)
    return response


def _lifecycle_commit(
    *, action="create", base_revision=0, payload=None, replayed=False, hash_format="postgres", **overrides
):
    payload = payload if payload is not None else {
        "title": "Review budget",
        "description": "Before Friday",
        "status": "planned",
        "priority": "high",
        "dueDate": "2026-07-18",
        "projectId": "2cf40000-0000-4000-8000-000000000001",
    }
    request_hash = canonical_json_sha256({
        "contractVersion": "task-lifecycle-v1",
        "source": "local-api",
        "action": action,
        "taskId": _LIFECYCLE_TASK_ID,
        "baseRevision": base_revision,
        "workspaceId": None,
        "payload": payload,
    })
    canonical_revision = base_revision + 1
    read_back = {
        "id": _LIFECYCLE_TASK_ID,
        "title": payload.get("title", "Review budget"),
        "description": payload.get("description", ""),
        "status": payload.get("status", "planned"),
        "priority": payload.get("priority"),
        "dueDate": payload.get("dueDate"),
        "projectId": payload.get("projectId"),
        "isDeleted": action == "soft_delete",
        "deletedAt": "2026-07-15T18:25:00.000Z" if action == "soft_delete" else None,
        "tombstone": action == "soft_delete",
        "workspaceId": None,
        "canonicalRevision": canonical_revision,
        "canonicalUpdatedAt": "2026-07-15T18:25:00.000Z",
    }
    receipt = {
        "contractVersion": "task-lifecycle-v1",
        "operationId": _LIFECYCLE_OPERATION_ID,
        "source": "local-api",
        "status": "committed",
        "requestHash": request_hash,
        "entityType": "task",
        "action": action,
        "entityId": _LIFECYCLE_TASK_ID,
        "canonicalRevision": canonical_revision,
        "canonicalUpdatedAt": "2026-07-15T18:25:00.000Z",
        "changeSequence": 42,
        "replayed": replayed,
        "committedAt": "2026-07-15T18:25:01.000Z",
        "readBack": read_back,
        "readBackHash": (
            canonical_json_sha256(read_back)
            if hash_format == "canonical"
            else postgres_jsonb_sha256(read_back)
        ),
    }
    receipt.update(overrides)
    return {
        "ok": True,
        "status": "committed",
        "result": "committed",
        "requestHash": request_hash,
        "receipt": receipt,
    }


def test_create_task_defaults_to_preview_with_stable_ids_and_exact_payload(monkeypatch):
    seen = {}
    preview = _lifecycle_preview()
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen(seen, preview))

    result = json.loads(fst._handle_create_task({
        "taskId": _LIFECYCLE_TASK_ID,
        "operationId": _LIFECYCLE_OPERATION_ID,
        "title": "  Review budget  ",
        "description": "Before Friday",
        "status": "planned",
        "priority": "high",
        "dueDate": "2026-07-18",
        "projectId": "2cf40000-0000-4000-8000-000000000001",
    }))

    assert result["result"] == preview
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/lifecycle"
    assert seen["body"] == {
        "operationId": _LIFECYCLE_OPERATION_ID,
        "action": "create",
        "taskId": _LIFECYCLE_TASK_ID,
        "baseRevision": 0,
        "payload": {
            "title": "Review budget",
            "description": "Before Friday",
            "status": "planned",
            "priority": "high",
            "dueDate": "2026-07-18",
            "projectId": "2cf40000-0000-4000-8000-000000000001",
        },
        "preview": True,
    }


def test_lifecycle_preview_rejects_a_forged_request_hash(monkeypatch):
    forged = _lifecycle_preview(requestHash="f" * 64)
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, forged))

    result = json.loads(fst._handle_create_task({
        "taskId": _LIFECYCLE_TASK_ID,
        "operationId": _LIFECYCLE_OPERATION_ID,
        "title": "Review budget",
        "description": "Before Friday",
        "status": "planned",
        "priority": "high",
        "dueDate": "2026-07-18",
        "projectId": "2cf40000-0000-4000-8000-000000000001",
    }))

    assert result["error"] == "Canonical task lifecycle preview could not be verified"


@pytest.mark.parametrize("missing", ["taskId", "operationId", "title"])
def test_create_task_requires_stable_identity_before_io(monkeypatch, missing):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("invalid create reached Local Task API"),
    )
    args = {
        "taskId": _LIFECYCLE_TASK_ID,
        "operationId": _LIFECYCLE_OPERATION_ID,
        "title": "Review budget",
    }
    del args[missing]

    result = json.loads(fst._handle_create_task(args))

    assert missing in result["error"]


@pytest.mark.parametrize("hash_format", ["canonical", "postgres"])
@pytest.mark.parametrize("replayed", [False, True])
def test_create_task_apply_binds_approved_digest_and_validates_receipt(
    monkeypatch, hash_format, replayed
):
    seen = {}
    committed = _lifecycle_commit(hash_format=hash_format, replayed=replayed)
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen(seen, committed))

    result = json.loads(fst._handle_create_task({
        "taskId": _LIFECYCLE_TASK_ID,
        "operationId": _LIFECYCLE_OPERATION_ID,
        "title": "Review budget",
        "description": "Before Friday",
        "priority": "high",
        "dueDate": "2026-07-18",
        "projectId": "2cf40000-0000-4000-8000-000000000001",
        "status": "planned",
        "preview": False,
        "previewDigest": "a" * 64,
        "previewExpiresAt": _LIFECYCLE_EXPIRY,
        "requestHash": committed["requestHash"],
    }))

    assert result["result"] == committed
    assert seen["body"]["preview"] is False
    assert seen["body"]["previewDigest"] == "a" * 64
    assert seen["body"]["previewExpiresAt"] == _LIFECYCLE_EXPIRY


@pytest.mark.parametrize(
    "handler_name,args,action,base_revision,payload",
    [
        (
            "_handle_delete_task",
            {"taskId": _LIFECYCLE_TASK_ID, "operationId": _LIFECYCLE_OPERATION_ID, "baseRevision": 7},
            "soft_delete",
            7,
            {},
        ),
        (
            "_handle_restore_task",
            {"taskId": _LIFECYCLE_TASK_ID, "operationId": _LIFECYCLE_OPERATION_ID, "baseRevision": 7},
            "restore",
            7,
            {},
        ),
        (
            "_handle_set_task_status",
            {
                "taskId": _LIFECYCLE_TASK_ID,
                "operationId": _LIFECYCLE_OPERATION_ID,
                "baseRevision": 7,
                "status": "in_progress",
            },
            "set_status",
            7,
            {"status": "in_progress"},
        ),
    ],
)
def test_lifecycle_tools_send_exact_action_and_payload(
    monkeypatch, handler_name, args, action, base_revision, payload
):
    seen = {}
    preview = _lifecycle_preview(action=action, base_revision=base_revision, payload=payload)
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen(seen, preview))

    result = json.loads(getattr(fst, handler_name)(args))

    assert result["result"] == preview
    assert seen["body"] == {
        "operationId": _LIFECYCLE_OPERATION_ID,
        "action": action,
        "taskId": _LIFECYCLE_TASK_ID,
        "baseRevision": base_revision,
        "payload": payload,
        "preview": True,
    }


@pytest.mark.parametrize("handler_name", ["_handle_delete_task", "_handle_restore_task"])
def test_delete_and_restore_require_base_revision_before_io(monkeypatch, handler_name):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("invalid lifecycle request reached Local Task API"),
    )

    result = json.loads(getattr(fst, handler_name)({
        "taskId": _LIFECYCLE_TASK_ID,
        "operationId": _LIFECYCLE_OPERATION_ID,
    }))

    assert result["error"] == "baseRevision is required and must be a positive integer"


@pytest.mark.parametrize("missing", ["previewDigest", "previewExpiresAt", "requestHash"])
def test_lifecycle_apply_requires_exact_preview_binding_before_io(monkeypatch, missing):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("unapproved lifecycle apply reached Local Task API"),
    )
    args = {
        "taskId": _LIFECYCLE_TASK_ID,
        "operationId": _LIFECYCLE_OPERATION_ID,
        "baseRevision": 7,
        "preview": False,
        "previewDigest": "a" * 64,
        "previewExpiresAt": _LIFECYCLE_EXPIRY,
        "requestHash": "b" * 64,
    }
    del args[missing]

    result = json.loads(fst._handle_delete_task(args))

    assert result["error"] == f"{missing} is required when preview is false"


def test_lifecycle_apply_rejects_mismatched_readback_hash(monkeypatch):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen({}, _lifecycle_commit(readBackHash="f" * 64)),
    )

    result = json.loads(fst._handle_create_task({
        "taskId": _LIFECYCLE_TASK_ID,
        "operationId": _LIFECYCLE_OPERATION_ID,
        "title": "Review budget",
        "description": "Before Friday",
        "priority": "high",
        "dueDate": "2026-07-18",
        "projectId": "2cf40000-0000-4000-8000-000000000001",
        "preview": False,
        "previewDigest": "a" * 64,
        "previewExpiresAt": _LIFECYCLE_EXPIRY,
        "requestHash": _lifecycle_commit()["requestHash"],
    }))

    assert result["error"] == "Canonical task lifecycle receipt could not be verified"


def test_set_status_preserves_recurring_done_conflict_without_fallback_write(monkeypatch):
    seen = {"calls": 0}

    def _raise(req, timeout):
        seen["calls"] += 1
        seen["body"] = json.loads(req.data.decode("utf-8"))
        body = json.dumps({
            "error": {
                "code": "recurrence_requires_done_for_now",
                "message": "Recurring tasks must use Done for now",
            },
            "action": "use_flowstate_done_for_now",
        }).encode("utf-8")
        raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, io.BytesIO(body))

    monkeypatch.setattr(fst.urllib.request, "urlopen", _raise)

    result = json.loads(fst._handle_set_task_status({
        "taskId": _LIFECYCLE_TASK_ID,
        "operationId": _LIFECYCLE_OPERATION_ID,
        "baseRevision": 7,
        "status": "done",
    }))

    assert result == {
        "error": "Recurring tasks must use Done for now",
        "code": "recurrence_requires_done_for_now",
        "status": 409,
        "action": "use_flowstate_done_for_now",
    }
    assert seen["calls"] == 1
    assert seen["body"]["action"] == "set_status"


_CANONICAL_DIGEST = "a" * 64
_CANONICAL_REQUEST_HASH = "b" * 64
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
        "canonicalRevision": 8,
        "canonicalUpdatedAt": _CANONICAL_UPDATED_AT,
    }
    receipt = {
        "contractVersion": "task-v1",
        "operationId": "op-123",
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
        "readBackHash": postgres_jsonb_sha256(read_back),
        "requestHash": _CANONICAL_REQUEST_HASH,
    }
    receipt.update(receipt_overrides)
    return {
        "ok": True,
        "result": "committed",
        "requestHash": _CANONICAL_REQUEST_HASH,
        "receipt": receipt,
    }


def _valid_update_args(**overrides):
    args = {
        "id": "task-1",
        "operationId": "op-123",
        "baseRevision": 7,
        "patch": {"title": "Clarified task"},
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
        requestHash=_CANONICAL_REQUEST_HASH,
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
        {"replayed": "false"},
        {"readBack": None},
        {"readBack": {"id": "task-1", "canonicalRevision": 7}},
        {"readBack": {"id": "wrong", "canonicalRevision": 8}},
        {"readBackHash": "not-a-sha256"},
        {"readBackHash": "f" * 64},
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
        requestHash=_CANONICAL_REQUEST_HASH,
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


def test_delete_task_rejects_legacy_unrevisioned_delete_before_io(monkeypatch):
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("legacy delete reached Local Task API"),
    )

    result = json.loads(fst._handle_delete_task({"id": "task/with/slash"}))

    assert "unsupported" in result["error"]


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
    result = json.loads(fst._handle_create_task({
        "taskId": _LIFECYCLE_TASK_ID,
        "operationId": "offline-create",
        "title": "Must not queue",
    }))

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


def test_route_preflight_fails_closed_on_missing_unavailable_or_mismatched_contract():
    matching = {
        ("POST", "/api/tasks/lifecycle"): {
            "available": True,
            "contractVersion": "task-lifecycle-v1",
        },
        ("POST", "/api/tasks/:id/work-blocks"): {
            "available": False,
            "contractVersion": "work-block-v1",
        },
        ("POST", "/api/tasks/:id/subtasks/batch"): {
            "available": True,
            "contractVersion": "legacy-subtask-batch-v0",
        },
    }

    assert fst._route_requirements_met(
        matching,
        (("POST", "/api/tasks/lifecycle", "task-lifecycle-v1"),),
    ) is True
    assert fst._route_requirements_met(
        matching,
        (("POST", "/api/tasks/:id/work-blocks", "work-block-v1"),),
    ) is False
    assert fst._route_requirements_met(
        matching,
        (("POST", "/api/tasks/:id/subtasks/batch", "subtask-batch-v1"),),
    ) is False
    assert fst._route_requirements_met(
        matching,
        (("GET", "/api/unknown", "unknown-v1"),),
    ) is False


def test_registered_route_requirements_match_live_route_manifest_contracts():
    requirements = {(method, path): version for method, path, version in fst._REGISTERED_ROUTE_REQUIREMENTS}

    assert requirements[("POST", "/api/tasks/:id/done-for-now")] == "task-v1"
    assert requirements[("POST", "/api/tasks/:id/merge")] == "task-v1"


def test_capability_report_names_every_blocked_tool_before_use(monkeypatch):
    routes = []
    for method, path, contract_version in fst._REGISTERED_ROUTE_REQUIREMENTS:
        routes.append({
            "method": method,
            "path": path,
            "contractVersion": contract_version,
            "available": True,
        })
    for route in routes:
        if route["path"] == "/api/tasks/:id/work-blocks":
            route["available"] = False
        if route["path"] == "/api/tasks/:id/subtasks/batch":
            route["contractVersion"] = "legacy-subtask-batch-v0"
    monkeypatch.setattr(
        fst,
        "_load_flowstate_capabilities",
        lambda: {"schemaVersion": "flowstate-hermes-capabilities-v1", "routes": routes},
    )

    report = fst._flowstate_compatibility_report()

    assert report["compatible"] is False
    assert report["routeCount"] == 16
    blocked = {item["tool"]: item["reason"] for item in report["blockedTools"]}
    assert blocked == {
        "flowstate_create_work_block": "route_unavailable",
        "flowstate_move_work_block": "route_unavailable",
        "flowstate_remove_work_block": "route_unavailable",
        "flowstate_resize_work_block": "route_unavailable",
        "flowstate_subtask_batch": "contract_mismatch",
    }


def test_health_includes_route_compatibility_preflight(monkeypatch):
    monkeypatch.setattr(fst, "_request", lambda method, path: {"ok": True})
    monkeypatch.setattr(
        fst,
        "_flowstate_compatibility_report",
        lambda: {"compatible": False, "blockedTools": [{"tool": "flowstate_subtask_batch"}]},
    )

    result = json.loads(fst._handle_health({}))

    assert result["result"] == {
        "ok": True,
        "compatibility": {
            "compatible": False,
            "blockedTools": [{"tool": "flowstate_subtask_batch"}],
        },
    }


def test_health_uses_existing_api_contract(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst,
        "_flowstate_compatibility_report",
        lambda: {"compatible": True, "blockedTools": []},
    )
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, {"ok": True}),
    )

    result = json.loads(fst._handle_health({}))

    assert result["result"] == {
        "ok": True,
        "compatibility": {"compatible": True, "blockedTools": []},
    }
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


_WORK_BLOCK_TASK_ID = "71111111-1111-4111-8111-111111111111"
_WORK_BLOCK_ID = "72222222-2222-4222-8222-222222222222"
_WORK_BLOCK_OPERATION_ID = "work-block-operation-1"


def _work_block_args(action="create"):
    common = {
        "taskId": _WORK_BLOCK_TASK_ID,
        "workBlockId": _WORK_BLOCK_ID,
        "operationId": _WORK_BLOCK_OPERATION_ID,
        "baseRevision": 7,
        "workBlockRevision": 0 if action == "create" else 3,
    }
    if action == "create":
        return {**common, "scheduledDate": "2026-07-16", "scheduledTime": "10:00", "duration": 60,
                "timezone": "Asia/Jerusalem", "finishBy": "2026-07-16T12:00"}
    if action == "move":
        return {**common, "scheduledDate": "2026-07-17", "scheduledTime": "09:15",
                "timezone": "UTC", "finishBy": "2026-07-17T11:00"}
    if action == "resize":
        return {**common, "duration": 90, "finishBy": "2026-07-16T12:00"}
    return common


def _work_block_command(action="create"):
    return fst._normalize_work_block_command(_work_block_args(action), action)


def _work_block_after(action):
    old = {
        "id": _WORK_BLOCK_ID, "taskId": _WORK_BLOCK_TASK_ID,
        "scheduledDate": "2026-07-16", "scheduledTime": "10:00", "duration": 60,
        "timezone": "Asia/Jerusalem", "canonicalRevision": 3,
    }
    if action == "create":
        return {**_work_block_command(action)["workBlock"], "taskId": _WORK_BLOCK_TASK_ID,
                "canonicalRevision": 1}
    if action == "move":
        return {**old, "scheduledDate": "2026-07-17", "scheduledTime": "09:15", "timezone": "UTC",
                "canonicalRevision": 4}
    if action == "resize":
        return {**old, "duration": 90, "canonicalRevision": 4}
    return None


def _work_block_interval(block):
    if block is None:
        return None
    start = datetime.strptime(f"{block['scheduledDate']}T{block['scheduledTime']}", "%Y-%m-%dT%H:%M")
    return {
        "localStart": start.strftime("%Y-%m-%dT%H:%M"),
        "localEnd": (start + timedelta(minutes=block["duration"])).strftime("%Y-%m-%dT%H:%M"),
    }


def _work_block_preview(action="create", **overrides):
    args = _work_block_args(action)
    command = _work_block_command(action)
    after = _work_block_after(action)
    before = None if action == "create" else {
        "id": _WORK_BLOCK_ID, "taskId": _WORK_BLOCK_TASK_ID,
        "scheduledDate": "2026-07-16", "scheduledTime": "10:00", "duration": 60,
        "timezone": "Asia/Jerusalem", "canonicalRevision": 3,
    }
    normalized = {
        "contractVersion": "work-block-v1", "source": "local-api", "action": action,
        "taskId": _WORK_BLOCK_TASK_ID, "baseRevision": 7,
        "workBlockRevision": args["workBlockRevision"], "workspaceId": None, "command": command,
    }
    payload = {
        "ok": True, "status": "preview", "result": "preview",
        "requestHash": canonical_json_sha256(normalized), "previewDigest": "a" * 64,
        "previewExpiresAt": "2026-07-15T20:00:00.000Z", "normalizedPayload": normalized,
        "preview": {
            "action": action, "workBlockId": _WORK_BLOCK_ID,
            "interval": {"before": _work_block_interval(before), "after": _work_block_interval(after)},
            "timezone": (after or before)["timezone"],
            "duration": {
                "beforeMinutes": None if before is None else before["duration"],
                "afterMinutes": None if after is None else after["duration"],
            },
            "overlapWarnings": [],
            "taskEffect": {"taskId": _WORK_BLOCK_TASK_ID, "dueDate": {"before": None, "after": None}},
            "finishByBoundary": (
                {"finishBy": command["finishBy"], "satisfied": True}
                if "finishBy" in command else None
            ),
        },
        "readBack": {
            "id": _WORK_BLOCK_TASK_ID, "canonicalRevision": 7,
            "instances": [] if after is None else [after],
        },
    }
    payload.update(overrides)
    return payload


def _work_block_commit(action="create", *, replayed=False, request_hash=None, revision=8):
    args = _work_block_args(action)
    command = _work_block_command(action)
    block = _work_block_after(action)
    normalized = _work_block_preview(action)["normalizedPayload"]
    request_hash = request_hash or canonical_json_sha256(normalized)
    read_back = {
        "id": _WORK_BLOCK_TASK_ID, "workBlock": block,
        "removedWorkBlockId": _WORK_BLOCK_ID if action == "remove" else None,
        "instances": [] if block is None else [block], "workspaceId": None,
        "canonicalRevision": revision, "canonicalUpdatedAt": "2026-07-15T19:30:00.000Z",
    }
    receipt = {
        "contractVersion": "work-block-v1", "operationId": _WORK_BLOCK_OPERATION_ID,
        "source": "local-api", "entityType": "task", "action": f"work_block_{action}",
        "entityId": _WORK_BLOCK_TASK_ID, "workBlockId": _WORK_BLOCK_ID,
        "requestHash": request_hash, "canonicalRevision": revision,
        "canonicalUpdatedAt": "2026-07-15T19:30:00.000Z", "changeSequence": 52,
        "status": "committed", "replayed": replayed,
        "committedAt": "2026-07-15T19:30:01.000Z", "readBack": read_back,
        "readBackHash": postgres_jsonb_sha256(read_back),
    }
    return {"ok": True, "status": "committed", "result": "committed",
            "requestHash": request_hash, "receipt": receipt}


@pytest.mark.parametrize("action,handler", [
    ("create", fst._handle_create_work_block),
    ("move", fst._handle_move_work_block),
    ("resize", fst._handle_resize_work_block),
    ("remove", fst._handle_remove_work_block),
])
def test_work_block_tools_default_to_exact_canonical_preview(monkeypatch, action, handler):
    seen = {}
    response = _work_block_preview(action)
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen(seen, response))

    result = json.loads(handler(_work_block_args(action)))

    assert result["result"] == response
    assert seen["url"].endswith(f"/api/tasks/{_WORK_BLOCK_TASK_ID}/work-blocks")
    assert seen["body"] == {
        "operationId": _WORK_BLOCK_OPERATION_ID, "baseRevision": 7,
        "workBlockRevision": _work_block_args(action)["workBlockRevision"],
        "command": _work_block_command(action), "preview": True,
    }


def test_work_block_preview_preserves_legacy_recurrence_instances(monkeypatch):
    response = _work_block_preview()
    legacy_occurrence = {
        "id": f"instance-{_WORK_BLOCK_TASK_ID}-1780493317847",
        "taskId": _WORK_BLOCK_TASK_ID,
        "scheduledDate": "2026-08-01",
        "scheduledTime": None,
        "duration": 15,
        "timezone": None,
    }
    response["readBack"]["instances"] = [legacy_occurrence, _work_block_after("create")]
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, response))

    result = json.loads(fst._handle_create_work_block(_work_block_args()))

    assert result["result"] == response


def test_create_work_block_apply_binds_approval_and_accepts_verified_replay(monkeypatch):
    seen = {}
    response = _work_block_commit(replayed=True)
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen(seen, response))
    args = {
        **_work_block_args(), "preview": False, "previewDigest": "a" * 64,
        "previewExpiresAt": "2026-07-15T20:00:00.000Z", "requestHash": response["requestHash"],
    }

    result = json.loads(fst._handle_create_work_block(args))

    assert result["result"]["receipt"]["replayed"] is True
    assert "requestHash" not in seen["body"]
    assert seen["body"]["preview"] is False


def test_work_block_commit_preserves_legacy_recurrence_instances(monkeypatch):
    response = _work_block_commit()
    legacy_occurrence = {
        "id": f"instance-{_WORK_BLOCK_TASK_ID}-1780493317847",
        "taskId": _WORK_BLOCK_TASK_ID,
        "scheduledDate": "2026-08-01",
        "scheduledTime": None,
        "duration": 15,
        "timezone": None,
    }
    receipt = response["receipt"]
    receipt["readBack"]["instances"] = [legacy_occurrence, _work_block_after("create")]
    receipt["readBackHash"] = postgres_jsonb_sha256(receipt["readBack"])
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, response))
    args = {
        **_work_block_args(), "preview": False, "previewDigest": "a" * 64,
        "previewExpiresAt": "2026-07-15T20:00:00.000Z", "requestHash": response["requestHash"],
    }

    result = json.loads(fst._handle_create_work_block(args))

    assert result["result"] == response


@pytest.mark.parametrize("missing", ["previewDigest", "previewExpiresAt", "requestHash"])
def test_work_block_apply_requires_exact_preview_proof(missing):
    args = {
        **_work_block_args(), "preview": False, "previewDigest": "a" * 64,
        "previewExpiresAt": "2026-07-15T20:00:00.000Z", "requestHash": "b" * 64,
    }
    args.pop(missing)
    result = json.loads(fst._handle_create_work_block(args))
    assert missing in result["error"]


@pytest.mark.parametrize("response", [
    _work_block_preview(preview={**_work_block_preview()["preview"], "timezone": "UTC"}),
    _work_block_commit(request_hash="c" * 64),
    _work_block_commit(revision=9),
])
def test_work_block_tools_reject_forged_preview_or_receipt(monkeypatch, response):
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, response))
    preview = response["result"] == "preview"
    args = {**_work_block_args(), "preview": preview}
    if not preview:
        args.update({"previewDigest": "a" * 64, "previewExpiresAt": "2026-07-15T20:00:00.000Z",
                     "requestHash": "b" * 64})
    result = json.loads(fst._handle_create_work_block(args))
    assert "could not be verified" in result["error"]


@pytest.mark.parametrize("mutation,error", [
    ({"workBlockId": "random"}, "canonical interval contract"),
    ({"workBlockRevision": 1}, "workBlockRevision"),
    ({"scheduledDate": "2026-02-30"}, "canonical interval contract"),
    ({"scheduledTime": "24:00"}, "canonical interval contract"),
    ({"duration": 0}, "canonical interval contract"),
    ({"timezone": "Mars/Olympus"}, "canonical interval contract"),
    ({"finishBy": "2026-07-16T12:00Z"}, "canonical interval contract"),
])
def test_create_work_block_rejects_invalid_identity_revision_or_interval(mutation, error):
    args = _work_block_args()
    args.update(mutation)
    result = json.loads(fst._handle_create_work_block(args))
    assert error in result["error"]


def test_work_block_schemas_expose_only_canonical_writers():
    from tools.registry import registry

    schemas = [
        fst.FLOWSTATE_CREATE_WORK_BLOCK_SCHEMA,
        fst.FLOWSTATE_MOVE_WORK_BLOCK_SCHEMA,
        fst.FLOWSTATE_RESIZE_WORK_BLOCK_SCHEMA,
        fst.FLOWSTATE_REMOVE_WORK_BLOCK_SCHEMA,
    ]
    assert registry.get_toolset_for_tool("flowstate_schedule_task_instance") is None
    for schema in schemas:
        assert registry.get_toolset_for_tool(schema["name"]) == "flowstate"
        assert schema["parameters"]["additionalProperties"] is False
        assert schema["parameters"]["required"][:5] == [
            "taskId", "workBlockId", "operationId", "baseRevision", "workBlockRevision",
        ]
        assert "previewDigest" in schema["parameters"]["properties"]
        assert "requestHash" in schema["parameters"]["properties"]


def test_done_for_now_defaults_to_non_mutating_preview_and_uses_exact_task_id(monkeypatch):
    seen = {}
    payload = {
        "ok": True,
        "preview": True,
        "requestId": "preview-1",
        "previewVersion": "version-1",
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
        "requestId": "preview-1",
    }))

    assert result["result"] == payload
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/task%2Fone/done-for-now"
    assert seen["body"]["nextDueDate"] == "2026-07-16"
    assert seen["body"]["preview"] is True
    assert seen["body"]["requestId"] == "preview-1"


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
        ({"taskId": "task-1"}, "requestId is required"),
        ({"taskId": "task-1", "nextDueDate": "07/16/2026"}, "nextDueDate must be YYYY-MM-DD"),
        ({"taskId": "task-1", "preview": False, "previewVersion": "version-1"}, "requestId is required"),
        ({"taskId": "task-1", "preview": False, "requestId": "apply-1"}, "previewVersion is required when preview is false"),
        ({"taskId": "task-1", "preview": False, "requestId": "apply-1", "previewVersion": "version-1"}, "requestHash is required when preview is false"),
    ],
)
def test_done_for_now_validates_exact_preview_apply_contract(args, error):
    result = json.loads(fst._handle_done_for_now(args))

    assert result["error"] == error
    assert "token-123" not in json.dumps(result)


def test_done_for_now_apply_forwards_preview_receipt_and_returns_readback(monkeypatch):
    seen = {}
    payload = {
        "ok": True,
        "preview": False,
        "receipt": {"requestId": "apply-1", "completedOccurrenceId": "occ-1"},
        "readBack": {"taskId": "task-1", "nextOccurrenceId": "occ-2", "nextDueDate": "2026-07-16"},
    }
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
        "requestHash": "a" * 64,
    }))

    assert result["result"] == payload
    assert seen["body"] == {
        "nextDueDate": "2026-07-16",
        "preview": False,
        "previewVersion": "version-1",
        "requestId": "apply-1",
        "requestHash": "a" * 64,
    }


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
        "requestHash": "a" * 64,
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
        "requestId": "merge-preview-1",
    }))

    assert result["result"] == payload
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/survivor%2F1/merge"
    assert seen["body"]["duplicateTaskId"] == "duplicate/1"
    assert seen["body"]["preview"] is True
    assert seen["body"]["requestId"] == "merge-preview-1"


def test_merge_tasks_forwards_explicit_recurrence_resolution(monkeypatch):
    seen = {}
    payload = {
        "ok": True,
        "preview": True,
        "previewVersion": "recurrence-merge-v1",
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
        "requestId": "recurrence-preview-1",
        "recurrenceResolution": {"pattern": "daily", "interval": 3, "endType": "never"},
    }))

    assert result["result"] == payload
    assert seen["body"] == {
        "duplicateTaskId": "duplicate-1",
        "preview": True,
        "requestId": "recurrence-preview-1",
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
        "requestId": "merge-preview-1",
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
        "requestId": "merge-preview-1",
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
            "requestId is required",
        ),
        (
            {"survivorTaskId": "survivor-1", "duplicateTaskId": "duplicate-1", "preview": False, "requestId": "r1"},
            "previewVersion is required when preview is false",
        ),
        (
            {"survivorTaskId": "survivor-1", "duplicateTaskId": "duplicate-1", "preview": False, "requestId": "r1", "previewVersion": "v1"},
            "requestHash is required when preview is false",
        ),
    ],
)
def test_merge_tasks_validates_exact_preview_apply_contract(args, error):
    result = json.loads(fst._handle_merge_tasks(args))

    assert result["error"] == error
    assert "token-123" not in json.dumps(result)


def test_merge_tasks_apply_forwards_preview_binding_and_receipt(monkeypatch):
    seen = {}
    payload = {
        "ok": True,
        "preview": False,
        "receipt": {
            "requestId": "merge-apply-1",
            "survivorTaskId": "survivor-1",
            "duplicateTaskId": "duplicate-1",
            "replayed": False,
        },
        "readBack": {"survivorTaskId": "survivor-1", "duplicateArchived": True},
    }
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
        "requestHash": "b" * 64,
    }))

    assert result["result"] == payload
    assert seen["body"] == {
        "duplicateTaskId": "duplicate-1",
        "preview": False,
        "previewVersion": "merge-version-1",
        "requestId": "merge-apply-1",
        "requestHash": "b" * 64,
    }


def test_done_for_now_and_merge_schemas_bind_apply_to_request_hash():
    for schema in (fst.FLOWSTATE_DONE_FOR_NOW_SCHEMA, fst.FLOWSTATE_MERGE_TASKS_SCHEMA):
        assert "requestHash" in schema["parameters"]["properties"]


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


_SUBTASK_PARENT_ID = "61111111-1111-4111-8111-111111111111"
_SUBTASK_OPERATION_ID = "breakdown-session-1"
_SUBTASK_CLIENT_ID = "62222222-2222-4222-8222-222222222222"


def _subtask_operations():
    return [
        {
            "action": "create",
            "subtask": {
                "id": _SUBTASK_CLIENT_ID,
                "title": "Draft outline",
                "doneEnough": "Outline has five headings",
                "estimateMinutes": 20,
            },
            "order": 0,
        },
    ]


def _normalized_subtask_operations():
    return [
        {
            "action": "create",
            "subtask": {
                "id": _SUBTASK_CLIENT_ID,
                "title": "Draft outline",
                "description": "",
                "isCompleted": False,
                "completedPomodoros": 0,
                "doneEnough": "Outline has five headings",
                "estimateMinutes": 20,
            },
            "order": 0,
        },
    ]


def _subtask_preview(*, operations=None, request_hash=None):
    operations = operations or _normalized_subtask_operations()
    normalized = {
        "contractVersion": "subtask-batch-v1",
        "source": "local-api",
        "action": "subtask_batch",
        "taskId": _SUBTASK_PARENT_ID,
        "baseRevision": 7,
        "workspaceId": None,
        "operations": operations,
    }
    return {
        "ok": True,
        "result": "preview",
        "contractVersion": "subtask-batch-v1",
        "operationId": _SUBTASK_OPERATION_ID,
        "taskId": _SUBTASK_PARENT_ID,
        "baseRevision": 7,
        "requestHash": request_hash or canonical_json_sha256(normalized),
        "previewDigest": "a" * 64,
        "previewExpiresAt": "2026-07-15T20:00:00.000Z",
        "normalizedPayload": normalized,
        "readBack": {
            "id": _SUBTASK_PARENT_ID,
            "workspaceId": None,
            "canonicalRevision": 7,
            "subtasks": [
                {
                    **operations[0]["subtask"],
                    "description": "",
                    "isCompleted": False,
                    "completedPomodoros": 0,
                },
            ],
        },
    }


def _subtask_commit(*, replayed=False, request_hash="b" * 64, read_back=None, revision=8):
    read_back = read_back or {
        "id": _SUBTASK_PARENT_ID,
        "canonicalRevision": revision,
        "canonicalUpdatedAt": "2026-07-15T19:30:00.000Z",
        "subtasks": [
            {
                "id": _SUBTASK_CLIENT_ID,
                "title": "Draft outline",
                "description": "",
                "isCompleted": False,
                "completedPomodoros": 0,
                "doneEnough": "Outline has five headings",
                "estimateMinutes": 20,
            },
        ],
    }
    receipt = {
        "status": "committed",
        "contractVersion": "subtask-batch-v1",
        "operationId": _SUBTASK_OPERATION_ID,
        "source": "local-api",
        "entityType": "task",
        "action": "subtask_batch",
        "entityId": _SUBTASK_PARENT_ID,
        "canonicalRevision": revision,
        "canonicalUpdatedAt": "2026-07-15T19:30:00.000Z",
        "changeSequence": 44,
        "replayed": replayed,
        "committedAt": "2026-07-15T19:30:01.000Z",
        "requestHash": request_hash,
        "readBack": read_back,
        "readBackHash": postgres_jsonb_sha256(read_back),
    }
    return {
        "ok": True,
        "status": "committed",
        "result": "committed",
        "requestHash": request_hash,
        "receipt": receipt,
    }


def test_subtask_batch_defaults_to_canonical_preview_and_preserves_order(monkeypatch):
    seen = {}
    operations = _subtask_operations()
    normalized_operations = _normalized_subtask_operations()
    preview = _subtask_preview(operations=normalized_operations)
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen(seen, preview))

    result = json.loads(fst._handle_subtask_batch({
        "taskId": _SUBTASK_PARENT_ID,
        "operationId": _SUBTASK_OPERATION_ID,
        "baseRevision": 7,
        "operations": operations,
    }))

    assert result["result"] == preview
    assert seen["url"].endswith(f"/api/tasks/{_SUBTASK_PARENT_ID}/subtasks/batch")
    assert seen["body"] == {
        "operationId": _SUBTASK_OPERATION_ID,
        "baseRevision": 7,
        "operations": normalized_operations,
        "preview": True,
    }


def test_subtask_batch_apply_binds_exact_preview_and_accepts_replay(monkeypatch):
    seen = {}
    operations = _subtask_operations()
    response = _subtask_commit(replayed=True)
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen(seen, response))

    result = json.loads(fst._handle_subtask_batch({
        "taskId": _SUBTASK_PARENT_ID,
        "operationId": _SUBTASK_OPERATION_ID,
        "baseRevision": 7,
        "operations": operations,
        "preview": False,
        "previewDigest": "a" * 64,
        "previewExpiresAt": "2026-07-15T20:00:00.000Z",
        "requestHash": "b" * 64,
        "approvedSubtaskIds": [_SUBTASK_CLIENT_ID],
    }))

    assert result["result"]["status"] == "committed"
    assert result["result"]["receipt"]["replayed"] is True
    assert seen["body"] == {
        "operationId": _SUBTASK_OPERATION_ID,
        "baseRevision": 7,
        "operations": _normalized_subtask_operations(),
        "preview": False,
        "previewDigest": "a" * 64,
        "previewExpiresAt": "2026-07-15T20:00:00.000Z",
        "approvedSubtaskIds": [_SUBTASK_CLIENT_ID],
    }


@pytest.mark.parametrize(
    "missing", ["previewDigest", "previewExpiresAt", "requestHash", "approvedSubtaskIds"]
)
def test_subtask_batch_apply_requires_exact_approved_preview(missing):
    args = {
        "taskId": _SUBTASK_PARENT_ID,
        "operationId": _SUBTASK_OPERATION_ID,
        "baseRevision": 7,
        "operations": _subtask_operations(),
        "preview": False,
        "previewDigest": "a" * 64,
        "previewExpiresAt": "2026-07-15T20:00:00.000Z",
        "requestHash": "b" * 64,
        "approvedSubtaskIds": [_SUBTASK_CLIENT_ID],
    }
    args.pop(missing)

    result = json.loads(fst._handle_subtask_batch(args))

    assert missing in result["error"]


@pytest.mark.parametrize(
    "mutation,error",
    [
        ({"operationId": ""}, "operationId"),
        ({"baseRevision": 0}, "baseRevision"),
        (
            {"operations": [{"action": "create", "subtask": {"id": "step-1", "title": "First"}}]},
            "canonical subtask batch contract",
        ),
        (
            {"operations": [{
                "action": "create",
                "subtask": {"id": _SUBTASK_CLIENT_ID, "title": "First"},
            }]},
            "canonical subtask batch contract",
        ),
        (
            {"operations": [{"action": "create", "title": "legacy weak write"}]},
            "canonical subtask batch contract",
        ),
        (
            {"operations": [{
                "action": "update",
                "subtaskId": _SUBTASK_CLIENT_ID,
                "patch": {"doneEnough": None},
            }]},
            "canonical subtask batch contract",
        ),
        (
            {"operations": [{
                "action": "update",
                "subtaskId": _SUBTASK_CLIENT_ID,
                "patch": {},
                "order": 100001,
            }]},
            "canonical subtask batch contract",
        ),
    ],
)
def test_subtask_batch_requires_stable_ids_revision_and_canonical_operations(mutation, error):
    args = {
        "taskId": _SUBTASK_PARENT_ID,
        "operationId": _SUBTASK_OPERATION_ID,
        "baseRevision": 7,
        "operations": _subtask_operations(),
    }
    args.update(mutation)

    result = json.loads(fst._handle_subtask_batch(args))

    assert error in result["error"]


def test_subtask_batch_normalizes_complete_reorder_and_delete_without_legacy_writes():
    target = "63333333-3333-4333-8333-333333333333"
    uppercase_target = f"  {target.upper()}  "
    operations = [
        {
            "action": "update",
            "subtaskId": uppercase_target,
            "patch": {
                "title": "  Review outline  ",
                "isCompleted": True,
                "completedPomodoros": 2,
                "doneEnough": "The outline is approved",
                "estimateMinutes": None,
            },
            "order": 1,
        },
        {"action": "delete", "subtaskId": _SUBTASK_CLIENT_ID},
    ]

    assert fst._normalize_subtask_batch_operations(operations) == [
        {
            "action": "update",
            "subtaskId": target,
            "patch": {
                "title": "Review outline",
                "isCompleted": True,
                "completedPomodoros": 2,
                "doneEnough": "The outline is approved",
                "estimateMinutes": None,
            },
            "order": 1,
        },
        {"action": "delete", "subtaskId": _SUBTASK_CLIENT_ID},
    ]
    assert fst._normalize_subtask_batch_operations([
        {"action": "update", "subtaskId": uppercase_target, "patch": {}, "order": 100000},
    ]) == [
        {"action": "update", "subtaskId": target, "patch": {}, "order": 100000},
    ]
    uppercase_client_id = "6ABCDEF0-1234-4234-8234-ABCDEF012345"
    created = fst._normalize_subtask_batch_operations([{
        "action": "create",
        "subtask": {
            "id": uppercase_client_id,
            "title": "First",
            "doneEnough": "The first result exists",
        },
    }])
    assert created[0]["subtask"]["id"] == uppercase_client_id.lower()


def test_subtask_batch_rejects_commit_with_different_ordered_projection(monkeypatch):
    response = _subtask_commit()
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, response))

    result = json.loads(fst._handle_subtask_batch({
        "taskId": _SUBTASK_PARENT_ID,
        "operationId": _SUBTASK_OPERATION_ID,
        "baseRevision": 7,
        "operations": _subtask_operations(),
        "preview": False,
        "previewDigest": "a" * 64,
        "previewExpiresAt": "2026-07-15T20:00:00.000Z",
        "requestHash": "b" * 64,
        "approvedSubtaskIds": ["64444444-4444-4444-8444-444444444444"],
    }))

    assert "could not be verified" in result["error"]


@pytest.mark.parametrize(
    "response",
    [
        _subtask_preview(request_hash="c" * 64),
        _subtask_commit(request_hash="c" * 64),
        _subtask_commit(revision=9),
        _subtask_commit(read_back={
            "id": _SUBTASK_PARENT_ID,
            "canonicalRevision": 8,
            "canonicalUpdatedAt": "2026-07-15T19:30:00.000Z",
            "subtasks": [],
        }),
    ],
)
def test_subtask_batch_rejects_unbound_or_incomplete_proof(monkeypatch, response):
    monkeypatch.setattr(fst.urllib.request, "urlopen", _capturing_urlopen({}, response))
    preview = response.get("result") == "preview"
    args = {
        "taskId": _SUBTASK_PARENT_ID,
        "operationId": _SUBTASK_OPERATION_ID,
        "baseRevision": 7,
        "operations": _subtask_operations(),
        "preview": preview,
    }
    if not preview:
        args.update({
            "previewDigest": "a" * 64,
            "previewExpiresAt": "2026-07-15T20:00:00.000Z",
            "requestHash": "b" * 64,
            "approvedSubtaskIds": [_SUBTASK_CLIENT_ID],
        })

    result = json.loads(fst._handle_subtask_batch(args))

    assert "could not be verified" in result["error"]


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
        "flowstate_restore_task",
        "flowstate_set_task_status",
        "flowstate_get_current_timer",
        "flowstate_get_timer_diagnostics",
        "flowstate_list_task_instances",
        "flowstate_create_work_block",
        "flowstate_move_work_block",
        "flowstate_resize_work_block",
        "flowstate_remove_work_block",
        "flowstate_done_for_now",
        "flowstate_merge_tasks",
        "flowstate_list_subtasks",
        "flowstate_subtask_batch",
    }

    for tool in expected:
        assert registry.get_toolset_for_tool(tool) == "flowstate"
        assert registry._tools[tool].check_fn is (
            fst._check_flowstate_available
            if tool == "flowstate_health"
            else fst._FLOWSTATE_TOOL_CHECKS[tool]
        )


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


def test_lifecycle_schemas_are_preview_first_and_approval_bound():
    create = fst.FLOWSTATE_CREATE_TASK_SCHEMA
    delete = fst.FLOWSTATE_DELETE_TASK_SCHEMA
    restore = fst.FLOWSTATE_RESTORE_TASK_SCHEMA
    set_status = fst.FLOWSTATE_SET_TASK_STATUS_SCHEMA

    assert create["parameters"]["required"] == ["taskId", "operationId", "title"]
    assert set(create["parameters"]["properties"]) == {
        "taskId", "operationId", "title", "description", "status", "priority",
        "dueDate", "projectId", "preview", "previewDigest", "previewExpiresAt", "requestHash",
    }
    assert set(create["parameters"]["properties"]["status"]["enum"]) == {
        "planned", "in_progress", "backlog", "on_hold",
    }
    for schema in (delete, restore):
        assert schema["parameters"]["required"] == ["taskId", "operationId", "baseRevision"]
        assert set(schema["parameters"]["properties"]) == {
            "taskId", "operationId", "baseRevision", "preview", "previewDigest", "previewExpiresAt",
            "requestHash",
        }
        assert schema["parameters"]["additionalProperties"] is False
        assert "preview" in schema["description"].lower()
    assert set_status["parameters"]["required"] == [
        "taskId", "operationId", "baseRevision", "status",
    ]
    assert set(set_status["parameters"]["properties"]["status"]["enum"]) == {
        "planned", "in_progress", "done", "backlog", "on_hold",
    }
    assert "flowstate_done_for_now" in set_status["description"]
    assert "fallback" in set_status["description"].lower()


def test_subtask_batch_schema_exposes_only_atomic_approval_bound_mutation():
    from tools.registry import registry

    schema = fst.FLOWSTATE_SUBTASK_BATCH_SCHEMA
    assert schema["parameters"]["required"] == [
        "taskId", "operationId", "baseRevision", "operations",
    ]
    assert set(schema["parameters"]["properties"]) == {
        "taskId", "operationId", "baseRevision", "operations", "preview",
        "previewDigest", "previewExpiresAt", "requestHash", "approvedSubtaskIds",
    }
    assert schema["parameters"]["additionalProperties"] is False
    assert "Never fall back" in schema["description"]
    create_payload = schema["parameters"]["properties"]["operations"]["items"]["properties"]["subtask"]
    assert create_payload["required"] == ["id", "title", "doneEnough"]
    for legacy_tool in (
        "flowstate_create_subtask", "flowstate_update_subtask", "flowstate_delete_subtask",
    ):
        assert registry.get_toolset_for_tool(legacy_tool) is None


def test_done_for_now_schema_is_preview_first_and_apply_is_receipt_bound():
    schema = fst.FLOWSTATE_DONE_FOR_NOW_SCHEMA

    assert schema["name"] == "flowstate_done_for_now"
    assert schema["parameters"]["required"] == ["taskId", "requestId"]
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
    assert schema["parameters"]["properties"]["limit"]["maximum"] == 25
    assert set(schema["parameters"]["properties"]) == {"query", "limit"}


def test_merge_tasks_schema_is_preview_first_and_exact_id_bound():
    schema = fst.FLOWSTATE_MERGE_TASKS_SCHEMA

    assert schema["name"] == "flowstate_merge_tasks"
    assert schema["parameters"]["required"] == ["survivorTaskId", "duplicateTaskId", "requestId"]
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
