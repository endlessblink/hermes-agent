import io
import json
import urllib.error

import pytest

from tools import flowstate_tool as fst


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def _urlopen(seen, payload):
    def open_(request, timeout):
        seen["url"] = request.full_url
        seen["body"] = None if request.data is None else json.loads(request.data)
        return _Response(payload)
    return open_


OPERATIONS = [
    {"kind": "create", "clientId": "draft", "title": "Draft outline", "order": 0},
    {"kind": "update", "subtaskId": "existing", "title": "Review", "order": 1},
]


def _preview():
    return {
        "ok": True,
        "result": "preview",
        "contractVersion": "task-v1",
        "action": "subtask_batch",
        "taskId": "task-1",
        "operationId": "op-1",
        "baseRevision": 7,
        "previewDigest": "a" * 64,
        "previewExpiresAt": "2026-07-16T08:05:00Z",
        "requestHash": "b" * 64,
        "normalizedPayload": {"taskId": "task-1", "operations": OPERATIONS},
        "readBack": {"id": "task-1", "canonicalRevision": 7, "subtasks": []},
    }


def test_list_subtasks_returns_only_fresh_canonical_order(monkeypatch):
    payload = {
        "ok": True,
        "task": {
            "id": "task-1", "title": "Launch", "workspaceId": None,
            "canonicalRevision": 7, "canonicalUpdatedAt": "2026-07-16T08:00:00Z",
        },
        "subtasks": [
            {"id": "a", "title": "Draft", "order": 0},
            {"id": "b", "title": "Review", "order": 1},
        ],
        "page": {"limit": 100, "total": 2, "hasMore": False, "nextCursor": None},
    }
    seen = {}
    monkeypatch.setattr(fst.urllib.request, "urlopen", _urlopen(seen, payload))

    result = json.loads(fst._handle_list_subtasks({"taskId": "task-1"}))

    assert result["result"]["task"]["canonicalRevision"] == 7
    assert [row["id"] for row in result["result"]["subtasks"]] == ["a", "b"]
    assert result["result"]["page"]["total"] == 2
    assert seen["url"].endswith("/api/tasks/task-1/subtasks")


def test_list_subtasks_rejects_noncanonical_order(monkeypatch):
    payload = {
        "ok": True,
        "task": {
            "id": "task-1", "title": "Launch", "workspaceId": None,
            "canonicalRevision": 7, "canonicalUpdatedAt": "2026-07-16T08:00:00Z",
        },
        "subtasks": [{"id": "a", "title": "Draft", "order": 2}],
        "page": {"limit": 100, "total": 1, "hasMore": False, "nextCursor": None},
    }
    monkeypatch.setattr(fst.urllib.request, "urlopen", _urlopen({}, payload))
    assert json.loads(fst._handle_list_subtasks({"taskId": "task-1"})) == {
        "error": "Fresh canonical subtask state could not be verified"
    }


def test_receipt_validator_accepts_the_full_canonical_domain_beyond_one_page():
    rows = [
        {"id": f"step-{order}", "title": f"Step {order}", "order": order}
        for order in range(101)
    ]

    assert fst._valid_subtask_collection(rows, "task-1") is True


def test_list_subtasks_reads_a_revision_bound_page_without_context_flood(monkeypatch):
    cursor = "eyJ2ZXJzaW9uIjoxLCJvZmZzZXQiOjEwMH0"
    payload = {
        "ok": True,
        "task": {
            "id": "task-1", "title": "Launch", "workspaceId": None,
            "canonicalRevision": 7, "canonicalUpdatedAt": "2026-07-16T08:00:00Z",
        },
        "subtasks": [{"id": "step-100", "title": "Last", "order": 100}],
        "page": {"limit": 100, "total": 101, "hasMore": False, "nextCursor": None},
    }
    seen = {}
    monkeypatch.setattr(fst.urllib.request, "urlopen", _urlopen(seen, payload))

    result = json.loads(fst._handle_list_subtasks({
        "taskId": "task-1", "limit": 100, "cursor": cursor,
    }))

    assert result["result"]["subtasks"][0]["order"] == 100
    assert result["result"]["page"] == payload["page"]
    assert seen["url"].endswith(f"/api/tasks/task-1/subtasks?limit=100&cursor={cursor}")


@pytest.mark.parametrize("args", [
    {"taskId": "task-1", "limit": 0},
    {"taskId": "task-1", "limit": 101},
    {"taskId": "task-1", "limit": True},
    {"taskId": "task-1", "cursor": ""},
    {"taskId": "task-1", "cursor": "x" * 2049},
])
def test_list_subtasks_rejects_invalid_page_request_before_io(monkeypatch, args):
    monkeypatch.setattr(
        fst.urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("reached API")
    )
    error = json.loads(fst._handle_list_subtasks(args))["error"]
    assert "limit" in error or "cursor" in error


def test_list_subtasks_rejects_cursor_from_a_stale_revision_with_current_revision(monkeypatch):
    def reject(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            409,
            "Conflict",
            {},
            io.BytesIO(json.dumps({
                "ok": False,
                "error": {
                    "code": "stale_revision",
                    "message": "task changed while reading subtasks",
                    "currentRevision": 8,
                },
            }).encode()),
        )

    monkeypatch.setattr(fst.urllib.request, "urlopen", reject)
    result = json.loads(fst._handle_list_subtasks({"taskId": "task-1", "cursor": "opaque"}))

    assert result == {
        "error": "Task changed since preview.",
        "code": "stale_revision",
        "status": 409,
        "currentRevision": 8,
    }


@pytest.mark.parametrize("handler", [fst._handle_list_subtasks, fst._handle_subtask_batch])
@pytest.mark.parametrize("task_id", [7, " task-1", "x" * 161])
def test_subtask_tools_reject_noncanonical_task_identity_before_io(monkeypatch, handler, task_id):
    monkeypatch.setattr(
        fst.urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("reached API")
    )
    args = {"taskId": task_id}
    if handler is fst._handle_subtask_batch:
        args.update({
            "operationId": "op-1",
            "baseRevision": 7,
            "operations": OPERATIONS,
            "proposalId": "proposal-1",
            "proposalRevision": 1,
        })
    assert "taskId" in json.loads(handler(args))["error"]


def test_batch_preview_preserves_exact_operations_and_emits_approval_request(monkeypatch):
    seen = {}
    monkeypatch.setattr(fst.urllib.request, "urlopen", _urlopen(seen, _preview()))
    result = json.loads(fst._handle_subtask_batch({
        "taskId": "task-1", "operationId": "op-1", "baseRevision": 7,
        "operations": OPERATIONS, "proposalId": "proposal-1", "proposalRevision": 1,
    }))["result"]

    assert seen["body"] == {
        "operationId": "op-1", "baseRevision": 7, "preview": True,
        "operations": OPERATIONS,
    }
    assert result["approvalRequest"]["operations"] == OPERATIONS
    assert result["approvalRequest"]["previewDigest"] == "a" * 64
    assert result["approvalRequest"]["proposalId"] == "proposal-1"
    assert result["approvalRequest"]["proposalRevision"] == 1


def test_batch_apply_requires_exact_gateway_capability_before_io(monkeypatch):
    monkeypatch.setattr(
        fst.urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("reached API")
    )
    result = json.loads(fst._handle_subtask_batch({
        "taskId": "task-1", "operationId": "op-1", "baseRevision": 7,
        "operations": OPERATIONS, "preview": False, "previewDigest": "a" * 64,
        "previewExpiresAt": "2026-07-16T08:05:00Z", "requestHash": "b" * 64,
        "proposalId": "proposal-1", "proposalRevision": 1,
    }))
    assert result == {"error": "approvalCapability is required for apply"}


def test_batch_schema_exposes_only_reduced_canonical_surface():
    names = {item[0] for item in fst._FLOWSTATE_TOOL_REGISTRATIONS}
    assert {"flowstate_list_subtasks", "flowstate_subtask_batch"}.issubset(names)
    assert "flowstate_create_subtask" not in names
    assert "flowstate_update_subtask" not in names
    assert "flowstate_delete_subtask" not in names
    required = fst.FLOWSTATE_SUBTASK_BATCH_SCHEMA["parameters"]["required"]
    assert {"proposalId", "proposalRevision"}.issubset(required)
    list_properties = fst.FLOWSTATE_LIST_SUBTASKS_SCHEMA["parameters"]["properties"]
    assert {"taskId", "limit", "cursor"} == set(list_properties)


def test_batch_preserves_bounded_stale_revision_details(monkeypatch):
    def reject(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            409,
            "Conflict",
            {},
            io.BytesIO(json.dumps({
                "error": {
                    "code": "stale_revision",
                    "message": "internal table secret changed",
                    "currentRevision": 8,
                    "authorization": "Bearer secret",
                },
                "debug": "database secret",
            }).encode()),
        )

    monkeypatch.setattr(fst.urllib.request, "urlopen", reject)
    result = json.loads(fst._handle_subtask_batch({
        "taskId": "task-1", "operationId": "op-1", "baseRevision": 7,
        "operations": OPERATIONS, "proposalId": "proposal-1", "proposalRevision": 1,
    }))

    assert result == {
        "error": "Task changed since preview.",
        "code": "stale_revision",
        "status": 409,
        "currentRevision": 8,
    }


def test_batch_preserves_subtask_limit_failure(monkeypatch):
    def reject(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            409,
            "Conflict",
            {},
            io.BytesIO(json.dumps({
                "error": {
                    "code": "subtask_limit_exceeded",
                    "message": "internal row count",
                },
            }).encode()),
        )

    monkeypatch.setattr(fst.urllib.request, "urlopen", reject)
    result = json.loads(fst._handle_subtask_batch({
        "taskId": "task-1", "operationId": "op-1", "baseRevision": 7,
        "operations": OPERATIONS, "proposalId": "proposal-1", "proposalRevision": 1,
    }))

    assert result == {
        "error": "This task already has the maximum number of subtasks.",
        "code": "subtask_limit_exceeded",
        "status": 409,
    }
    assert "secret" not in json.dumps(result).lower()


def test_batch_preserves_invalid_existing_subtasks_stop_code(monkeypatch):
    def reject(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            409,
            "Conflict",
            {},
            io.BytesIO(json.dumps({
                "error": {
                    "code": "invalid_existing_subtasks",
                    "message": "raw storage secret",
                }
            }).encode()),
        )

    monkeypatch.setattr(fst.urllib.request, "urlopen", reject)
    result = json.loads(fst._handle_subtask_batch({
        "taskId": "task-1", "operationId": "op-1", "baseRevision": 7,
        "operations": OPERATIONS, "proposalId": "proposal-1", "proposalRevision": 1,
    }))
    assert result == {
        "error": "Existing FlowState subtask data needs bounded repair.",
        "code": "invalid_existing_subtasks",
        "status": 409,
    }
    assert "secret" not in json.dumps(result).lower()


def test_list_subtasks_preserves_redacted_auth_failure(monkeypatch):
    def reject(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Bearer secret",
            {},
            io.BytesIO(json.dumps({
                "error": {"code": "unauthorized", "message": "token secret"}
            }).encode()),
        )

    monkeypatch.setattr(fst.urllib.request, "urlopen", reject)
    result = json.loads(fst._handle_list_subtasks({"taskId": "task-1"}))
    assert result == {
        "error": "Flow State Local Task API rejected the bearer token. Check FLOW_STATE_API_TOKEN.",
        "code": "unauthorized",
        "status": 401,
    }
    assert "secret" not in json.dumps(result).lower()


def test_exact_proposal_lineage_authorizes_apply_and_mismatch_fails_before_io(monkeypatch):
    from agent.subtask_approval_capabilities import subtask_approval_capabilities
    from tools.approval import reset_current_session_key, set_current_session_key

    read_back = {
        "id": "task-1",
        "canonicalRevision": 8,
        "canonicalUpdatedAt": "2026-07-16T08:01:00Z",
        "subtasks": [
            {"id": "created", "clientId": "draft", "title": "Draft outline", "order": 0},
            {"id": "existing", "title": "Review", "order": 1},
        ],
    }
    affected = {
        "entityType": "task",
        "entityId": "task-1",
        "action": "update",
        "canonicalRevision": 8,
        "changeSequence": 44,
        "readBack": read_back,
        "readBackHash": fst._canonical_hash(read_back),
    }
    payload = {
        "result": "committed",
        "operationId": "op-1",
        "requestHash": "b" * 64,
        "receipt": {
            "ok": True,
            "status": "committed",
            "contractVersion": "task-v1",
            "source": "local-api",
            "entityType": "task",
            "operationId": "op-1",
            "requestHash": "b" * 64,
            "action": "subtask_batch",
            "entityId": "task-1",
            "canonicalRevision": 8,
            "canonicalUpdatedAt": "2026-07-16T08:01:00Z",
            "changeSequence": 44,
            "committedAt": "2026-07-16T08:01:01Z",
            "readBack": read_back,
            "readBackHash": fst._canonical_hash(read_back),
            "affected": [affected],
        },
    }
    proof = {
        "taskId": "task-1",
        "operationId": "op-1",
        "baseRevision": 7,
        "operations": OPERATIONS,
        "previewDigest": "a" * 64,
        "previewExpiresAt": "2099-07-16T08:05:00Z",
        "requestHash": "b" * 64,
        "proposalId": "proposal-1",
        "proposalRevision": 3,
    }
    subtask_approval_capabilities.revoke_session("session-a")
    capability = subtask_approval_capabilities.register("session-a", proof)
    seen = {}
    monkeypatch.setattr(fst.urllib.request, "urlopen", _urlopen(seen, payload))
    context = set_current_session_key("session-a")
    try:
        args = {**proof, "preview": False, "approvalCapability": capability}
        result = json.loads(fst._handle_subtask_batch(args))
        assert result["result"]["receipt"]["canonicalRevision"] == 8
        assert "proposalId" not in seen["body"]

        payload["receipt"]["status"] = "replayed"
        payload["receipt"]["replayed"] = True
        monkeypatch.setattr(fst.urllib.request, "urlopen", _urlopen(seen, payload))
        replayed = json.loads(fst._handle_subtask_batch(args))
        assert replayed["result"]["receipt"]["status"] == "replayed"

        payload["receipt"].pop("replayed")
        monkeypatch.setattr(fst.urllib.request, "urlopen", _urlopen(seen, payload))
        status_only_replay = json.loads(fst._handle_subtask_batch(args))
        assert status_only_replay["result"]["receipt"]["status"] == "replayed"

        payload["receipt"]["status"] = "committed"
        payload["receipt"]["replayed"] = True
        monkeypatch.setattr(fst.urllib.request, "urlopen", _urlopen(seen, payload))
        assert json.loads(fst._handle_subtask_batch(args)) == {
            "error": "Canonical subtask receipt could not be verified"
        }

        wrong = {**args, "proposalRevision": 4}
        monkeypatch.setattr(
            fst.urllib.request,
            "urlopen",
            lambda *_args, **_kwargs: pytest.fail("mismatched proposal reached API"),
        )
        assert json.loads(fst._handle_subtask_batch(wrong)) == {
            "error": "approvalCapability is invalid for this exact apply"
        }
    finally:
        reset_current_session_key(context)
        subtask_approval_capabilities.revoke_session("session-a")
