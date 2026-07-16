"""Tests for the Flow State local API tool module."""

import json
import io
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


def _complete_inventory():
    return {
        "source": "flowstate",
        "scope": "all open tasks visible to the authenticated user",
        "scopeKind": "personal",
        "scopeFingerprint": "0123456789abcdef",
        "capturedAt": "2026-07-16T09:00:00Z",
        "appVersion": "1.4.263",
        "fresh": True,
        "complete": True,
        "changeSequence": 12,
        "total": 1,
        "items": [
            {
                "id": "00000000-0000-4000-8000-000000000001",
                "title": "Plan",
                "canonicalRevision": 3,
            }
        ],
        "page": {"limit": 100, "nextCursor": None, "hasMore": False},
    }


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


def test_unfiltered_list_uses_complete_inventory_without_stale_cache(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen(seen, _complete_inventory()),
    )

    result = json.loads(fst._handle_list_tasks({}))

    assert result["result"]["complete"] is True
    assert result["result"]["total"] == 1
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/inventory"


def test_unfiltered_list_fails_closed_on_incomplete_inventory(monkeypatch):
    payload = _complete_inventory()
    payload["complete"] = False
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen({}, payload),
    )

    result = json.loads(fst._handle_list_tasks({}))

    assert "invalid inventory receipt" in result["error"]


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


def test_merge_tasks_defaults_to_non_mutating_preview_with_exact_ids(monkeypatch):
    seen = {}
    payload = {"ok": True, "preview": True, "previewVersion": "merge-v1"}
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
    assert seen["url"] == "http://127.0.0.1:5577/api/tasks/survivor%2F1/merge"
    assert seen["body"] == {"duplicateTaskId": "duplicate/1", "preview": True}


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
        "recurrenceResolution": {"pattern": "daily", "interval": 3, "endType": "never"},
    }))

    assert result["result"] == payload
    assert seen["body"] == {
        "duplicateTaskId": "duplicate-1",
        "preview": True,
        "recurrenceResolution": {"pattern": "daily", "interval": 3, "endType": "never"},
    }


def test_merge_tasks_refuses_apply_without_interactive_capability():
    result = json.loads(fst._handle_merge_tasks({
        "survivorTaskId": "survivor-1",
        "duplicateTaskId": "duplicate-1",
        "preview": False,
        "requestId": "request-1",
        "previewVersion": "merge-v1",
    }))

    assert result["error"] == (
        "merge apply is unavailable until exact interactive approval is registered"
    )


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


def test_merge_tasks_turns_successful_preview_recurrence_conflict_into_stop(monkeypatch):
    payload = {
        "ok": True,
        "preview": True,
        "conflicts": [
            {
                "code": "recurring_merge_unsupported",
                "message": "Recurring definitions require explicit series semantics",
            }
        ],
    }
    monkeypatch.setattr(
        fst.urllib.request,
        "urlopen",
        _capturing_urlopen({}, payload),
    )

    result = json.loads(fst._handle_merge_tasks({
        "survivorTaskId": "survivor-1",
        "duplicateTaskId": "duplicate-1",
    }))

    assert result == {
        "error": "Recurring tasks require an exact cadence choice before they can be merged",
        "code": "recurring_merge_unsupported",
        "status": 409,
        "action": "stop_mutations_and_request_recurrence_resolution",
    }


def test_merge_tasks_preserves_stop_action_for_unsupported_recurring_merge(monkeypatch):
    def _raise(req, timeout):
        body = json.dumps({
            "ok": False,
            "error": {
                "code": "recurring_merge_unsupported",
                "message": "Recurring task merge is not supported",
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
        "error": "Recurring tasks require an exact cadence choice before they can be merged",
        "code": "recurring_merge_unsupported",
        "status": 409,
        "action": "stop_mutations_and_request_recurrence_resolution",
    }


def test_merge_tasks_drops_untrusted_stop_action(monkeypatch):
    def _raise(req, timeout):
        body = json.dumps({
            "error": {"code": "state_conflict", "message": "State changed"},
            "action": "stop_mutations_and_request_recurrence_resolution",
        }).encode("utf-8")
        raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, io.BytesIO(body))

    monkeypatch.setattr(fst.urllib.request, "urlopen", _raise)

    result = json.loads(fst._handle_merge_tasks({
        "survivorTaskId": "survivor-1",
        "duplicateTaskId": "duplicate-1",
    }))

    assert result["code"] == "state_conflict"
    assert "action" not in result


def test_merge_tasks_preserves_stop_action_for_established_recurrence_history(monkeypatch):
    def _raise(req, timeout):
        body = json.dumps({
            "error": {
                "code": "recurrence_history_unsupported",
                "message": "Recurring task history requires an explicit series strategy",
            },
            "action": "stop_mutations_and_report_recurrence_history",
        }).encode("utf-8")
        raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, io.BytesIO(body))

    monkeypatch.setattr(fst.urllib.request, "urlopen", _raise)

    result = json.loads(fst._handle_merge_tasks({
        "survivorTaskId": "survivor-1",
        "duplicateTaskId": "duplicate-1",
        "recurrenceResolution": {"pattern": "daily", "interval": 3, "endType": "never"},
    }))

    assert result["code"] == "recurrence_history_unsupported"
    assert result["action"] == "stop_mutations_and_report_recurrence_history"


def test_merge_tasks_schema_is_preview_only_and_recurrence_aware():
    schema = fst.FLOWSTATE_MERGE_TASKS_SCHEMA

    assert schema["name"] == "flowstate_merge_tasks"
    assert schema["parameters"]["required"] == ["survivorTaskId", "duplicateTaskId"]
    assert schema["parameters"]["properties"]["preview"]["const"] is True
    assert "requestId" not in schema["parameters"]["properties"]
    assert "previewVersion" not in schema["parameters"]["properties"]
    assert "recurrenceResolution" in schema["parameters"]["properties"]
    assert "stop all further Flow State mutations" in schema["description"]


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


def test_toolset_registration_maps_all_flowstate_tools():
    from tools.registry import registry

    expected = {
        "flowstate_health",
        "flowstate_list_tasks",
        "flowstate_create_task",
        "flowstate_update_task",
        "flowstate_delete_task",
        "flowstate_get_current_timer",
        "flowstate_merge_tasks",
    }

    for tool in expected:
        assert registry.get_toolset_for_tool(tool) == "flowstate"
