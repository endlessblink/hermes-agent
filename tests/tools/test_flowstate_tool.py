"""Tests for the Flow State local API tool module."""

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from tools import flowstate_tool as fst


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
        ({}, "query is required"),
        ({"query": "   "}, "query is required"),
        ({"query": "laundry", "limit": 0}, "limit must be an integer from 1 to 25"),
        ({"query": "laundry", "limit": 26}, "limit must be an integer from 1 to 25"),
        ({"query": "laundry", "limit": "many"}, "limit must be an integer from 1 to 25"),
    ],
)
def test_search_tasks_validates_query_and_limit_without_calling_api(args, error):
    result = json.loads(fst._handle_search_tasks(args))

    assert result["error"] == error
    assert "token-123" not in json.dumps(result)


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


def test_update_task_requires_a_change():
    result = json.loads(fst._handle_update_task({"id": "task-1"}))

    assert result["error"]
    assert "at least one field" in result["error"]


def test_update_task_validates_status():
    result = json.loads(fst._handle_update_task({"id": "task-1", "status": "paused"}))

    assert result["error"] == "status must be todo|done"


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


@pytest.mark.parametrize(
    "args,error",
    [
        ({}, "taskId is required"),
        ({"taskId": "task-1", "nextDueDate": "07/16/2026"}, "nextDueDate must be YYYY-MM-DD"),
        ({"taskId": "task-1", "preview": False, "previewVersion": "version-1"}, "requestId is required when preview is false"),
        ({"taskId": "task-1", "preview": False, "requestId": "apply-1"}, "previewVersion is required when preview is false"),
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
    }))

    assert result["result"] == payload
    assert seen["body"] == {
        "nextDueDate": "2026-07-16",
        "preview": False,
        "previewVersion": "version-1",
        "requestId": "apply-1",
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
    }))

    assert result["result"] == payload
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/survivor%2F1/merge"
    assert seen["body"] == {"duplicateTaskId": "duplicate/1", "preview": True}


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
    }))

    assert result["result"] == payload
    assert seen["body"] == {
        "duplicateTaskId": "duplicate-1",
        "preview": False,
        "previewVersion": "merge-version-1",
        "requestId": "merge-apply-1",
    }


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
    }


def test_timer_diagnostics_schema_is_read_only_and_verification_focused():
    schema = fst.FLOWSTATE_TIMER_DIAGNOSTICS_SCHEMA

    assert schema["name"] == "flowstate_get_timer_diagnostics"
    assert "read-only" in schema["description"].lower()
    assert "leader" in schema["description"].lower()
    assert schema["parameters"] == {"type": "object", "properties": {}, "required": []}


def test_search_tasks_schema_is_read_only_and_requires_a_query():
    schema = fst.FLOWSTATE_SEARCH_TASKS_SCHEMA

    assert schema["name"] == "flowstate_search_tasks"
    assert "read-only" in schema["description"].lower()
    assert schema["parameters"]["required"] == ["query"]
    assert set(schema["parameters"]["properties"]) == {"query", "limit"}


def test_merge_tasks_schema_is_preview_first_and_exact_id_bound():
    schema = fst.FLOWSTATE_MERGE_TASKS_SCHEMA

    assert schema["name"] == "flowstate_merge_tasks"
    assert schema["parameters"]["required"] == ["survivorTaskId", "duplicateTaskId"]
    assert "Defaults to preview" in schema["description"]
    assert "title similarity" in schema["description"].lower()
    assert set(schema["parameters"]["properties"]) == {
        "survivorTaskId",
        "duplicateTaskId",
        "preview",
        "requestId",
        "previewVersion",
    }
