"""Canonical preview/apply contracts for FlowState task lifecycle tools."""

from __future__ import annotations

import json

import pytest

from tools import flowstate_tool as fst
from tools.flowstate_receipts import canonical_json_hash


DIGEST = "a" * 64
REQUEST_HASH = "c" * 64
EXPIRY = "2026-07-15T20:30:00.000Z"
UPDATED_AT = "2026-07-15T20:10:00.000Z"
COMMITTED_AT = "2026-07-15T20:10:01.000Z"
TASK_ID = "task-1"


def _state(action: str, revision: int) -> dict:
    deleted = action == "delete"
    return {
        "id": TASK_ID,
        "title": "Lifecycle task",
        "status": "todo",
        "completedAt": None,
        "isDeleted": deleted,
        "deletedAt": UPDATED_AT if deleted else None,
        "tombstonePresent": deleted,
        "workspaceId": None,
        "canonicalRevision": revision,
        "canonicalUpdatedAt": None if revision == 0 else UPDATED_AT,
    }


def _preview(action: str, *, revision: int = 7) -> dict:
    read_back = _state(action, revision)
    return {
        "ok": True,
        "result": "preview",
        "contractVersion": "task-v1",
        "operationId": "op-123",
        "action": action,
        "taskId": TASK_ID,
        "baseRevision": revision,
        "requestHash": REQUEST_HASH,
        "previewDigest": DIGEST,
        "previewExpiresAt": EXPIRY,
        "normalizedPayload": {
            "taskId": TASK_ID,
            **({
                "title": "Lifecycle task", "description": "", "priority": None,
                "dueDate": None, "projectId": None,
            } if action == "create" else {}),
        },
        "readBack": read_back,
    }


def _commit(action: str) -> dict:
    read_back = _state(action, 8 if action != "create" else 1)
    affected_action = "update" if action == "reopen" else action
    affected = {
        "entityType": "task",
        "entityId": TASK_ID,
        "action": affected_action,
        "canonicalRevision": read_back["canonicalRevision"],
        "changeSequence": 42,
        "readBack": read_back,
        "readBackHash": canonical_json_hash(read_back),
    }
    receipt = {
        "ok": True,
        "status": "committed",
        "contractVersion": "task-v1",
        "operationId": "op-123",
        "requestHash": REQUEST_HASH,
        "source": "local-api",
        "entityType": "task",
        "action": action,
        "entityId": TASK_ID,
        "canonicalRevision": read_back["canonicalRevision"],
        "canonicalUpdatedAt": UPDATED_AT,
        "changeSequence": 42,
        "committedAt": COMMITTED_AT,
        "replayed": False,
        "readBack": read_back,
        "readBackHash": canonical_json_hash(read_back),
        "affected": [affected],
    }
    return {
        "ok": True,
        "result": "committed",
        "operationId": "op-123",
        "action": action,
        "taskId": TASK_ID,
        "requestHash": REQUEST_HASH,
        "receipt": receipt,
    }


def _apply_args(action: str) -> dict:
    args = {
        "operationId": "op-123",
        "baseRevision": 0 if action == "create" else 7,
        "preview": False,
        "previewDigest": DIGEST,
        "previewExpiresAt": EXPIRY,
        "requestHash": REQUEST_HASH,
    }
    if action == "create":
        args.update({"taskId": TASK_ID, "title": "Lifecycle task"})
    else:
        args["id"] = TASK_ID
    return args


def test_create_defaults_to_verified_preview_with_deterministic_identity(monkeypatch):
    seen = {}

    def request(method, path, body):
        seen.update(method=method, path=path, body=body)
        return _preview("create", revision=0)

    monkeypatch.setattr(fst, "_request", request)
    result = json.loads(fst._handle_create_task({
        "operationId": "op-123", "baseRevision": 0, "title": "Lifecycle task",
    }))

    assert result["result"]["taskId"] == TASK_ID
    assert seen == {
        "method": "POST",
        "path": "/api/tasks",
        "body": {
            "operationId": "op-123",
            "baseRevision": 0,
            "payload": {
                "title": "Lifecycle task", "description": "", "priority": None,
                "dueDate": None, "projectId": None,
            },
            "preview": True,
        },
    }


@pytest.mark.parametrize("missing", ["taskId", "previewDigest", "previewExpiresAt", "requestHash"])
def test_create_apply_requires_exact_preview_binding_before_io(monkeypatch, missing):
    monkeypatch.setattr(fst, "_request", lambda *_a, **_k: pytest.fail("request reached API"))
    args = _apply_args("create")
    del args[missing]

    result = json.loads(fst._handle_create_task(args))

    assert "required" in result["error"]


def test_create_preview_rejects_server_normalization_that_changes_the_requested_title(monkeypatch):
    preview = _preview("create", revision=0)
    preview["normalizedPayload"]["title"] = "Different task"
    monkeypatch.setattr(fst, "_request", lambda *_a, **_k: preview)

    result = json.loads(fst._handle_create_task({
        "operationId": "op-123", "baseRevision": 0, "title": "Lifecycle task",
    }))

    assert "preview could not be verified" in result["error"].lower()


@pytest.mark.parametrize("action,handler", [
    ("create", fst._handle_create_task),
    ("delete", fst._handle_delete_task),
    ("restore", getattr(fst, "_handle_restore_task", None)),
    ("reopen", getattr(fst, "_handle_reopen_task", None)),
])
def test_lifecycle_apply_accepts_only_verified_receipt(monkeypatch, action, handler):
    assert handler is not None, f"{action} handler missing"
    seen = {}

    def request(method, path, body):
        seen.update(method=method, path=path, body=body)
        return _commit(action)

    monkeypatch.setattr(fst, "_request", request)
    result = json.loads(handler(_apply_args(action)))

    assert result["result"]["receipt"]["action"] == action
    assert seen["method"] == "POST"
    assert seen["body"]["requestHash"] == REQUEST_HASH
    assert seen["path"] == (
        "/api/tasks" if action == "create" else f"/api/tasks/{TASK_ID}/{action}"
    )


@pytest.mark.parametrize("action,handler", [
    ("create", fst._handle_create_task),
    ("delete", fst._handle_delete_task),
])
def test_lifecycle_rejects_forged_read_back_before_reporting_success(monkeypatch, action, handler):
    payload = _commit(action)
    payload["receipt"]["readBackHash"] = "f" * 64
    monkeypatch.setattr(fst, "_request", lambda *_a, **_k: payload)

    result = json.loads(handler(_apply_args(action)))

    assert "receipt could not be verified" in result["error"].lower()


def test_lifecycle_rejects_outer_task_identity_that_disagrees_with_receipt(monkeypatch):
    payload = _commit("delete")
    payload["taskId"] = "different-task"
    monkeypatch.setattr(fst, "_request", lambda *_a, **_k: payload)

    result = json.loads(fst._handle_delete_task(_apply_args("delete")))

    assert "receipt could not be verified" in result["error"].lower()


def test_restore_and_reopen_default_to_non_mutating_preview(monkeypatch):
    seen = []

    def request(method, path, body):
        action = path.rsplit("/", 1)[-1]
        seen.append((method, path, body))
        return _preview(action)

    monkeypatch.setattr(fst, "_request", request)
    for action, handler in [
        ("restore", getattr(fst, "_handle_restore_task", None)),
        ("reopen", getattr(fst, "_handle_reopen_task", None)),
    ]:
        assert handler is not None, f"{action} handler missing"
        result = json.loads(handler({"id": TASK_ID, "operationId": "op-123", "baseRevision": 7}))
        assert result["result"]["result"] == "preview"

    assert [item[1] for item in seen] == [
        f"/api/tasks/{TASK_ID}/restore", f"/api/tasks/{TASK_ID}/reopen",
    ]
    assert all(item[2]["preview"] is True for item in seen)


def test_reopen_recurring_conflict_requests_a_trusted_halt(monkeypatch):
    def conflict(*_args, **_kwargs):
        raise fst._FlowStateApiError(
            "Recurring completion history requires review.",
            code="recurring_task",
            status=409,
        )

    monkeypatch.setattr(fst, "_request", conflict)
    result = json.loads(fst._handle_reopen_task({
        "id": TASK_ID, "operationId": "op-123", "baseRevision": 7,
    }))

    assert result["code"] == "recurring_task"
    assert result["action"] == "stop_mutations_and_report_recurrence_history"


def test_lifecycle_tool_schemas_are_preview_first_and_registered():
    from tools.registry import registry

    for name in [
        "flowstate_create_task", "flowstate_delete_task",
        "flowstate_restore_task", "flowstate_reopen_task",
    ]:
        assert registry.get_toolset_for_tool(name) == "flowstate"
        schema = registry.get_schema(name)
        assert "preview" in schema["parameters"]["properties"]
        assert "operationId" in schema["parameters"]["required"]
        assert "baseRevision" in schema["parameters"]["required"]
        assert "preview" in schema["description"].lower()
