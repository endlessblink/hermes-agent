"""Regression proof for Calendar retry continuing into the planning interview."""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone


class _Response:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _task(index: int) -> dict:
    return {
        "id": f"00000000-0000-4000-8000-{index:012d}",
        "title": f"Task {index}",
        "status": "todo",
        "canonicalRevision": 1,
    }


def _inventory_page(items: list[dict], *, next_cursor: str | None) -> dict:
    return {
        "source": "flowstate",
        "scope": "all open tasks visible to the authenticated user",
        "scopeKind": "personal",
        "scopeFingerprint": "0123456789abcdef",
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "appVersion": "1.4.260",
        "fresh": True,
        "complete": False,
        "items": items,
        "page": {
            "limit": 25,
            "nextCursor": next_cursor,
            "hasMore": next_cursor is not None,
        },
    }


def _question_card(interview: dict) -> str:
    cursor = interview["cursor"]
    return "```hermes-ui\n" + json.dumps(
        {
            "type": "task-profile-review",
            "id": "active-planning-question",
            "interviewId": interview["interviewId"],
            "revision": interview["interviewRevision"],
            "task": {"id": cursor["taskId"], "title": "Task 1"},
            "title": "Task 1",
            "progress": {"current": 1, "total": 74},
            "profileFields": [
                {"id": cursor["questionId"], "label": "What should happen next?"}
            ],
            "question": {
                "id": cursor["questionId"],
                "profileFieldId": cursor["questionId"],
                "label": "What should happen next?",
                "type": "single-choice",
                "options": [{"value": "continue", "label": "Continue"}],
                "allowCustomAnswer": True,
            },
        },
        separators=(",", ":"),
    ) + "\n```"


def test_calendar_retry_continues_through_all_tasks_to_the_active_question(
    monkeypatch, tmp_path
):
    import hermes_cli.profiles as profiles
    import hermes_constants
    from agent.personal_assistant_calendar_gate import build_calendar_preflight_receipt
    from agent.personal_assistant_output_gate import evaluate_personal_assistant_output
    from agent.personal_assistant_state import PersonalAssistantStateStore
    from tools import flowstate_tool as fst
    from tools import personal_assistant_tool as pat
    from tools.registry import registry

    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "office-work")
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(fst, "_FLOW_STATE_API_URL", "http://127.0.0.1:5577")
    monkeypatch.setattr(fst, "_FLOW_STATE_API_TOKEN", "test-token")

    seeded = json.loads(
        pat._handle_interview_start(
            {
                "interviewId": "weekly-2026-07-20",
                "requestId": "initial-start",
                "planningDate": "2026-07-21",
                "sourceSnapshot": {"source": "FlowState", "fresh": True},
                "tasks": [
                    {"taskId": f"task-{index}", "title": f"Task {index}"}
                    for index in range(1, 75)
                ],
            }
        )
    )["result"]["interview"]
    assert seeded["status"] == "active"

    calendar_attempt = 0

    def calendar_receipt(**kwargs):
        nonlocal calendar_attempt
        calendar_attempt += 1

        def run_gws(parts, params):
            if calendar_attempt == 1:
                raise RuntimeError("Google Workspace is not authenticated")
            if parts[-2:] == ["calendarList", "list"]:
                return {"items": [{"id": "primary", "summary": "Primary"}]}
            return {"items": []}

        return build_calendar_preflight_receipt(**kwargs, run_gws=run_gws)

    monkeypatch.setattr(pat, "build_calendar_preflight_receipt", calendar_receipt)

    first = json.loads(
        registry.dispatch(
            "personal_assistant_calendar_preflight",
            {
                "startDate": "2026-07-21",
                "endDate": "2026-07-21",
                "timezone": "Asia/Jerusalem",
            },
        )
    )["result"]["receipt"]
    assert first["status"] == "unavailable"
    assert first["requiredInteraction"]["choices"][0]["id"] == "retry"

    second = json.loads(
        registry.dispatch(
            "personal_assistant_calendar_preflight",
            {
                "startDate": "2026-07-21",
                "endDate": "2026-07-21",
                "timezone": "Asia/Jerusalem",
            },
        )
    )["result"]["receipt"]
    assert second["status"] == "complete"
    assert second["range"]["endDate"] == "2026-07-22"

    tasks = [_task(index) for index in range(1, 75)]
    pages = {
        "": _inventory_page(tasks[:25], next_cursor="page-2"),
        "page-2": _inventory_page(tasks[25:50], next_cursor="page-3"),
        "page-3": _inventory_page(tasks[50:], next_cursor=None),
    }

    def urlopen(request, timeout):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        cursor = query.get("cursor", [""])[0]
        return _Response(pages[cursor])

    monkeypatch.setattr(fst.urllib.request, "urlopen", urlopen)
    reviewed_ids: list[str] = []
    cursor = ""
    while True:
        args = {"status": "open", "mode": "page", "limit": 25}
        if cursor:
            args["cursor"] = cursor
        page = json.loads(registry.dispatch("flowstate_list_tasks", args))["result"]
        reviewed_ids.extend(item["id"] for item in page["items"])
        if not page["page"]["hasMore"]:
            break
        cursor = page["page"]["nextCursor"]

    assert len(reviewed_ids) == 74
    assert len(set(reviewed_ids)) == 74

    resumed = json.loads(
        registry.dispatch(
            "personal_assistant_interview_start",
            {
                "interviewId": "retry-2026-07-21",
                "requestId": "retry-after-calendar",
                "planningDate": "2026-07-21",
                "sourceSnapshot": {"source": "FlowState", "fresh": True},
                "tasks": [{"taskId": "replacement", "title": "Replacement"}],
            },
        )
    )
    assert "error" not in resumed
    assert resumed["result"]["resumed"] is True
    interview = resumed["result"]["interview"]
    assert interview["interviewId"] == seeded["interviewId"]

    persisted = PersonalAssistantStateStore(tmp_path).read()
    decision = evaluate_personal_assistant_output(
        _question_card(interview),
        interview=interview,
        calendar_receipt=persisted["calendar_preflight_receipt"],
    )
    assert decision.accepted is True

