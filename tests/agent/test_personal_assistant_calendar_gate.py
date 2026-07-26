from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import subprocess

import agent.personal_assistant_calendar_gate as calendar_gate
from agent.personal_assistant_calendar_gate import (
    begin_calendar_first_planning_turn,
    build_calendar_preflight_receipt,
    calendar_first_candidate_records,
    calendar_preflight_gate,
    calendar_first_task_inventory,
    calendar_first_task_records,
    complete_inventory_repeat_gate,
    calendar_first_task_details,
    calendar_receipt_is_fresh_complete,
    complete_calendar_first_planning_turn,
    record_calendar_first_task_inventory,
    record_calendar_first_task_detail,
    record_calendar_first_candidate_inventory,
    same_day_grounding_gate_message,
)
from agent.personal_assistant_output_gate import evaluate_personal_assistant_output
from agent.prompt_builder import PERSONAL_ASSISTANT_GUIDANCE


def test_preflight_reads_every_calendar_and_paginates_calendar_and_events():
    calls: list[tuple[list[str], dict]] = []

    def run(parts: list[str], params: dict):
        calls.append((parts, params))
        if parts[-2:] == ["calendarList", "list"]:
            if params.get("pageToken") == "cal-next":
                return {"items": [{"id": "secondary", "summary": "Other"}]}
            return {
                "items": [{"id": "primary", "summary": "Primary"}],
                "nextPageToken": "cal-next",
            }
        if params["calendarId"] == "primary" and not params.get("pageToken"):
            return {"items": [{"id": "one"}], "nextPageToken": "event-next"}
        if params["calendarId"] == "primary":
            return {"items": [{"id": "two"}]}
        return {"items": [{"id": "three"}]}

    receipt = build_calendar_preflight_receipt(
        start_date="2026-07-21",
        end_date="2026-07-22",
        timezone_name="Asia/Jerusalem",
        run_gws=run,
        now=datetime(2026, 7, 21, 6, tzinfo=timezone.utc),
    )

    assert receipt["status"] == "complete"
    assert receipt["complete"] is True
    assert receipt["coverage"]["calendarCount"] == 2
    assert receipt["coverage"]["eventCount"] == 3
    assert receipt["range"]["timeMin"].endswith("+03:00")
    assert receipt["range"]["timeMax"].endswith("+03:00")
    assert len(calls) == 5


def test_preflight_treats_equal_dates_as_one_local_day():
    receipt = build_calendar_preflight_receipt(
        start_date="2026-07-21",
        end_date="2026-07-21",
        timezone_name="Asia/Jerusalem",
        run_gws=lambda parts, params: {"items": []},
        now=datetime(2026, 7, 21, 6, tzinfo=timezone.utc),
    )

    assert receipt["status"] == "complete"
    assert receipt["range"]["startDate"] == "2026-07-21"
    assert receipt["range"]["endDate"] == "2026-07-22"
    assert receipt["range"]["timeMax"].startswith("2026-07-22T00:00:00")


def test_preflight_marks_partial_when_one_readable_calendar_fails():
    def run(parts: list[str], params: dict):
        if parts[-2:] == ["calendarList", "list"]:
            return {"items": [{"id": "primary"}, {"id": "broken"}]}
        if params["calendarId"] == "broken":
            raise RuntimeError("forbidden")
        return {"items": []}

    receipt = build_calendar_preflight_receipt(
        start_date="2026-07-21",
        end_date="2026-07-22",
        timezone_name="Asia/Jerusalem",
        run_gws=run,
    )

    assert receipt["status"] == "partial"
    assert receipt["complete"] is False
    assert receipt["errors"][0]["calendarId"] == "broken"
    assert [choice["id"] for choice in receipt["requiredInteraction"]["choices"]] == [
        "retry",
        "repair-calendar",
        "cancel",
    ]


def test_preflight_marks_unavailable_when_calendar_list_cannot_be_read():
    receipt = build_calendar_preflight_receipt(
        start_date="2026-07-21",
        end_date="2026-07-22",
        timezone_name="Asia/Jerusalem",
        run_gws=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("auth")),
    )

    assert receipt["status"] == "unavailable"
    assert receipt["complete"] is False


def test_task_inventory_reads_remain_available_without_calendar_receipt():
    assert calendar_preflight_gate("flowstate_list_tasks", None) is None
    assert calendar_preflight_gate("flowstate_search_tasks", None) is None
    assert calendar_preflight_gate("flowstate_get_task", None) is None


def test_named_task_correction_allows_only_exact_task_read_and_update_before_interview():
    begin_calendar_first_planning_turn(
        required=True,
        same_day_grounding_required=True,
        task_fact_correction_required=True,
    )

    assert calendar_preflight_gate("flowstate_get_task", None) is None
    assert calendar_preflight_gate("flowstate_update_task", None) is None
    assert calendar_preflight_gate("flowstate_list_tasks", None) is not None
    assert calendar_preflight_gate("flowstate_search_tasks", None) is not None
    assert same_day_grounding_gate_message("python") is not None
    assert same_day_grounding_gate_message("clarify") is not None


def test_ordinary_planning_still_blocks_task_update_before_interview():
    begin_calendar_first_planning_turn(
        required=True,
        same_day_grounding_required=True,
    )

    assert calendar_preflight_gate("flowstate_get_task", None) is not None
    assert calendar_preflight_gate("flowstate_update_task", None) is not None


def test_planning_turn_blocks_task_reads_until_this_turn_completes_calendar_preflight():
    begin_calendar_first_planning_turn(required=True)

    blocked = calendar_preflight_gate("flowstate_get_task", None)

    assert blocked is not None
    assert blocked["error_type"] == "calendar_preflight_required_this_turn"
    assert calendar_preflight_gate("personal_assistant_calendar_preflight", None) is None

    complete_calendar_first_planning_turn(complete=False)
    assert calendar_preflight_gate("flowstate_list_tasks", None) is not None

    complete_calendar_first_planning_turn(complete=True)
    assert calendar_preflight_gate("flowstate_list_tasks", None) is None


def test_nonplanning_turn_does_not_gate_direct_task_commands():
    begin_calendar_first_planning_turn(required=False)

    assert calendar_preflight_gate("flowstate_get_task", None) is None
    assert calendar_preflight_gate("flowstate_update_task", None) is None


def test_planning_inventory_accepts_only_a_complete_exact_same_turn_receipt():
    begin_calendar_first_planning_turn(required=True)
    record_calendar_first_task_inventory({"complete": False, "items": [{"id": "old"}]})
    assert calendar_first_task_inventory() == (frozenset(), False)

    record_calendar_first_task_inventory(
        {"complete": True, "total": 3, "items": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    )
    assert calendar_first_task_inventory() == (frozenset({"a", "b", "c"}), True)


def test_complete_planning_inventory_blocks_repeated_full_list_reads() -> None:
    begin_calendar_first_planning_turn(required=True)
    complete_calendar_first_planning_turn(complete=True)
    assert complete_inventory_repeat_gate("flowstate_list_tasks") is None

    record_calendar_first_task_inventory(
        {"complete": True, "total": 2, "items": [{"id": "a"}, {"id": "b"}]}
    )

    blocked = complete_inventory_repeat_gate("flowstate_list_tasks")
    assert blocked is not None
    assert blocked["error_type"] == "complete_inventory_already_available"
    assert blocked["total"] == 2
    assert complete_inventory_repeat_gate("flowstate_get_task") is None


def test_planning_inventory_keeps_matching_recurring_occurrence_duration() -> None:
    begin_calendar_first_planning_turn(required=True)
    record_calendar_first_task_inventory(
        {
            "complete": True,
            "total": 1,
            "items": [
                {
                    "id": "jobs",
                    "title": "להגיש משרות",
                    "dueDate": "2026-07-24",
                    "estimatedDuration": None,
                    "instances": [
                        {
                            "scheduledDate": "2026-07-24",
                            "status": "scheduled",
                            "duration": 40,
                        }
                    ],
                }
            ],
        }
    )

    assert calendar_first_task_records()["jobs"]["estimatedDuration"] == 40


def test_normalized_candidate_records_capture_flowstate_and_notion_per_source() -> None:
    begin_calendar_first_planning_turn(required=True)
    record_calendar_first_task_inventory(
        {
            "complete": True,
            "total": 1,
            "items": [
                {
                    "id": "jobs",
                    "title": "להגיש משרות",
                    "status": "todo",
                    "priority": "high",
                    "dueDate": "2026-07-24",
                    "estimatedDuration": None,
                    "instances": [
                        {
                            "scheduledDate": "2026-07-24",
                            "status": "scheduled",
                            "duration": 40,
                        }
                    ],
                }
            ],
        }
    )
    record_calendar_first_candidate_inventory(
        "notion-bina-work",
        {
            "complete": True,
            "total": 1,
            "items": [
                {
                    "id": "notion-one",
                    "title": "Notion task",
                    "status": "open",
                    "priority": "medium",
                    "due": "2026-07-25",
                }
            ],
        },
    )

    candidates = calendar_first_candidate_records()

    assert candidates["flowstate"]["jobs"] == {
        "id": "jobs",
        "sourceId": "flowstate",
        "title": "להגיש משרות",
        "status": "todo",
        "priority": "high",
        "dueDate": "2026-07-24",
        "estimatedDuration": 40,
    }
    assert candidates["notion-bina-work"]["notion-one"] == {
        "id": "notion-one",
        "sourceId": "notion-bina-work",
        "title": "Notion task",
        "status": "open",
        "priority": "medium",
        "dueDate": "2026-07-25",
        "estimatedDuration": None,
    }


def test_normalized_candidate_records_parse_complete_live_notion_pages() -> None:
    begin_calendar_first_planning_turn(required=True)
    record_calendar_first_candidate_inventory(
        "notion-bina-work",
        {
            "ok": True,
            "data_source_id": "external-notion-uuid",
            "query": {
                "has_more": False,
                "results": [
                    {
                        "id": "notion-page-1",
                        "properties": {
                            "שם המשימה": {
                                "type": "title",
                                "title": [{"plain_text": "לשלוח הצעת מחיר"}],
                            },
                            "Done": {"type": "checkbox", "checkbox": False},
                            "Due": {
                                "type": "date",
                                "date": {"start": "2026-07-24"},
                            },
                            "דחיפות": {
                                "type": "select",
                                "select": {"name": "גבוהה"},
                            },
                        },
                    }
                ],
            },
        },
    )

    assert calendar_first_candidate_records()["notion-bina-work"]["notion-page-1"] == {
        "id": "notion-page-1",
        "sourceId": "notion-bina-work",
        "title": "לשלוח הצעת מחיר",
        "status": "open",
        "priority": "high",
        "dueDate": "2026-07-24",
        "estimatedDuration": None,
    }


def test_normalized_candidate_records_reset_with_new_planning_turn() -> None:
    begin_calendar_first_planning_turn(required=True)
    record_calendar_first_candidate_inventory(
        "notion-bina-work",
        {
            "complete": True,
            "items": [{"id": "notion-one", "title": "Notion task"}],
        },
    )

    assert calendar_first_candidate_records()["notion-bina-work"]["notion-one"]["title"] == "Notion task"

    begin_calendar_first_planning_turn(required=True)

    assert calendar_first_candidate_records() == {}


def test_full_task_details_are_scoped_to_the_current_planning_turn():
    begin_calendar_first_planning_turn(required=True)
    record_calendar_first_task_detail(
        {"task": {"id": "a", "instances": [{"scheduledDate": "2026-07-23"}]}}
    )
    assert calendar_first_task_details()["a"]["instances"][0]["scheduledDate"] == "2026-07-23"

    begin_calendar_first_planning_turn(required=True)
    assert calendar_first_task_details() == {}


def test_planning_interview_can_start_before_source_checks():
    assert calendar_preflight_gate("personal_assistant_interview_start", None) is None

    now = datetime.now(timezone.utc)
    receipt = {
        "status": "complete",
        "complete": True,
        "capturedAt": now.isoformat(),
        "expiresAt": (now + timedelta(minutes=5)).isoformat(),
    }
    assert calendar_receipt_is_fresh_complete(receipt, now=now)
    assert calendar_preflight_gate(
        "personal_assistant_interview_start", receipt, now=now
    ) is None
    assert calendar_preflight_gate("flowstate_get_current_timer", None, now=now) is None


def test_same_day_grounding_allows_calendar_then_interview_but_blocks_task_sources():
    begin_calendar_first_planning_turn(required=True, same_day_grounding_required=True)

    assert calendar_preflight_gate("personal_assistant_calendar_preflight", None) is None
    assert calendar_preflight_gate("personal_assistant_interview_start", None) is None
    blocked = calendar_preflight_gate("flowstate_list_tasks", None)
    assert blocked is not None
    assert blocked["error_type"] == "same_day_grounding_required"
    assert "personal_assistant_calendar_preflight" in same_day_grounding_gate_message("clarify")
    assert same_day_grounding_gate_message("personal_assistant_interview_start") is None


def test_planning_artifact_is_blocked_without_complete_calendar_receipt():
    response = '```hermes-ui\n{"type":"day-timeline","id":"today"}\n```'

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        calendar_receipt=None,
    )

    assert decision.accepted is False
    assert decision.reason == "calendar_preflight_required"


def test_registry_dispatch_allows_task_inventory_read_in_office_work(monkeypatch, tmp_path):
    import hermes_cli.profiles as profiles
    import hermes_constants
    import tools.flowstate_tool  # noqa: F401 - registers the production tool
    from tools.registry import registry

    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "office-work")
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    begin_calendar_first_planning_turn(required=False)

    monkeypatch.setattr(
        registry.get_entry("flowstate_search_tasks"),
        "handler",
        lambda args, **kwargs: json.dumps({"tasks": [], "query": args["query"]}),
    )

    result = json.loads(registry.dispatch("flowstate_search_tasks", {"query": "today"}))

    assert result == {"tasks": [], "query": "today"}


def test_registry_records_each_fresh_full_task_read(monkeypatch, tmp_path):
    import hermes_cli.profiles as profiles
    import hermes_constants
    import tools.flowstate_tool  # noqa: F401 - registers the production tool
    from tools.registry import registry

    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "office-work")
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    begin_calendar_first_planning_turn(required=False)
    monkeypatch.setattr(
        registry.get_entry("flowstate_get_task"),
        "handler",
        lambda args, **kwargs: json.dumps(
            {
                "result": {
                    "task": {
                        "id": args["id"],
                        "instances": [{"scheduledDate": "2026-07-23"}],
                    }
                }
            }
        ),
    )

    registry.dispatch("flowstate_get_task", {"id": "task-a"})

    assert calendar_first_task_details()["task-a"]["instances"] == [
        {"scheduledDate": "2026-07-23"}
    ]


def test_registry_records_notion_candidate_inventory_by_source(monkeypatch, tmp_path):
    import hermes_cli.profiles as profiles
    import hermes_constants
    from agent.personal_assistant_state import PersonalAssistantStateStore
    from tools.registry import registry

    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "office-work")
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(
        PersonalAssistantStateStore,
        "read",
        lambda self: {
            "task_source_manifest": [
                {
                    "id": "notion-bina-work",
                    "inventoryTool": "notion_data_source_list",
                    "available": True,
                }
            ]
        },
    )
    begin_calendar_first_planning_turn(required=False)
    registry.register(
        name="notion_data_source_list",
        toolset="test-notion",
        schema={"name": "notion_data_source_list", "parameters": {"type": "object"}},
        handler=lambda args, **kwargs: json.dumps(
            {
                "ok": True,
                "data_source_id": "external-notion-uuid",
                "query": {
                    "has_more": False,
                    "results": [
                        {
                            "id": "notion-one",
                            "properties": {
                                "Name": {
                                    "type": "title",
                                    "title": [{"plain_text": "Notion task"}],
                                },
                                "Status": {
                                    "type": "status",
                                    "status": {"name": "Open"},
                                },
                                "Priority": {
                                    "type": "select",
                                    "select": {"name": "Medium"},
                                },
                                "Due": {
                                    "type": "date",
                                    "date": {"start": "2026-07-25"},
                                },
                            },
                        }
                    ],
                },
            }
        ),
        override=True,
    )

    try:
        result = json.loads(
            registry.dispatch("notion_data_source_list", {})
        )
        assert result["ok"] is True
        assert calendar_first_candidate_records()["notion-bina-work"]["notion-one"] == {
            "id": "notion-one",
            "sourceId": "notion-bina-work",
            "title": "Notion task",
            "status": "open",
            "priority": "medium",
            "dueDate": "2026-07-25",
            "estimatedDuration": None,
        }
    finally:
        registry.deregister("notion_data_source_list")


def test_registry_unlocks_planning_reads_only_after_successful_preflight(monkeypatch, tmp_path):
    import hermes_cli.profiles as profiles
    import hermes_constants
    import tools.flowstate_tool  # noqa: F401
    import tools.personal_assistant_tool  # noqa: F401
    from tools.registry import registry

    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "office-work")
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(
        registry.get_entry("personal_assistant_calendar_preflight"),
        "handler",
        lambda args, **kwargs: json.dumps(
            {"result": {"receipt": {"complete": args.get("succeed") is True}}}
        ),
    )
    monkeypatch.setattr(
        registry.get_entry("flowstate_list_tasks"),
        "handler",
        lambda args, **kwargs: json.dumps({"tasks": ["all"]}),
    )

    begin_calendar_first_planning_turn(required=True)
    registry.dispatch("personal_assistant_calendar_preflight", {"succeed": False})
    blocked = json.loads(registry.dispatch("flowstate_list_tasks", {}))
    assert blocked["error_type"] == "calendar_preflight_required_this_turn"

    registry.dispatch("personal_assistant_calendar_preflight", {"succeed": True})
    assert json.loads(registry.dispatch("flowstate_list_tasks", {})) == {"tasks": ["all"]}


def test_registry_deduplicates_complete_planning_inventory_when_global_profile_is_unset(
    monkeypatch, tmp_path
):
    """Desktop sessions must use the active turn contract, not process-global profile state."""
    import hermes_cli.profiles as profiles
    import hermes_constants
    import tools.flowstate_tool  # noqa: F401
    from tools.registry import registry

    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: None)
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(
        registry.get_entry("flowstate_list_tasks"),
        "handler",
        lambda args, **kwargs: calls.append(dict(args))
        or json.dumps(
            {
                "result": {
                    "complete": True,
                    "fresh": True,
                    "total": 2,
                    "items": [
                        {"id": "task-a", "title": "Alpha"},
                        {"id": "task-b", "title": "Beta"},
                    ],
                }
            }
        ),
    )

    begin_calendar_first_planning_turn(required=True)
    complete_calendar_first_planning_turn(complete=True)

    first = json.loads(registry.dispatch("flowstate_list_tasks", {"limit": 100}))
    second = json.loads(
        registry.dispatch("flowstate_list_tasks", {"mode": "page", "limit": 25})
    )

    assert first["result"]["total"] == 2
    assert second["error_type"] == "complete_inventory_already_available"
    assert calls == [{"limit": 100}]


def test_interview_start_does_not_depend_on_calendar_date_scope():
    now = datetime.now(timezone.utc)
    receipt = {
        "status": "complete",
        "complete": True,
        "capturedAt": now.isoformat(),
        "expiresAt": (now + timedelta(minutes=5)).isoformat(),
        "timezone": "Asia/Jerusalem",
        "range": {"startDate": "2026-07-20", "endDate": "2026-07-21"},
    }

    blocked = calendar_preflight_gate(
        "personal_assistant_interview_start",
        receipt,
        tool_args={"planningDate": "2026-07-21"},
        now=now,
    )

    assert blocked is None


def test_interview_start_does_not_depend_on_calendar_timezone_scope():
    now = datetime.now(timezone.utc)
    receipt = {
        "status": "complete",
        "complete": True,
        "capturedAt": now.isoformat(),
        "expiresAt": (now + timedelta(minutes=5)).isoformat(),
        "timezone": "UTC",
        "range": {"startDate": "2026-07-21", "endDate": "2026-07-22"},
    }

    blocked = calendar_preflight_gate(
        "personal_assistant_interview_start",
        receipt,
        tool_args={"planningDate": "2026-07-21"},
        now=now,
    )

    assert blocked is None


def test_day_plan_date_must_match_calendar_receipt_scope():
    now = datetime.now(timezone.utc)
    receipt = {
        "status": "complete",
        "complete": True,
        "capturedAt": now.isoformat(),
        "expiresAt": (now + timedelta(minutes=5)).isoformat(),
        "timezone": "Asia/Jerusalem",
        "range": {"startDate": "2026-07-20", "endDate": "2026-07-21"},
    }
    response = '```hermes-ui\n{"type":"day-timeline","date":"2026-07-21","blocks":[]}\n```'

    decision = evaluate_personal_assistant_output(
        response,
        interview=None,
        calendar_receipt=receipt,
    )

    assert decision.accepted is False
    assert decision.reason == "calendar_preflight_scope_mismatch"


def test_calendar_guidance_uses_exclusive_end_and_does_not_escalate_recovered_validation_error():
    assert "exclusive next local date" in PERSONAL_ASSISTANT_GUIDANCE
    assert "correct the arguments and retry once" in PERSONAL_ASSISTANT_GUIDANCE
    assert "Do not open a recovery prompt" in PERSONAL_ASSISTANT_GUIDANCE
    assert "always run a fresh `personal_assistant_calendar_preflight` first" in PERSONAL_ASSISTANT_GUIDANCE
    assert "Never reuse the failed receipt" in PERSONAL_ASSISTANT_GUIDANCE


def test_default_runner_uses_existing_native_gws_encrypted_credentials(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    gws_config = tmp_path / "gws"
    gws_config.mkdir()
    (gws_config / "credentials.enc").write_bytes(b"encrypted")
    captured: dict = {}

    monkeypatch.setenv("HERMES_GWS_BIN", "/usr/bin/gws")
    monkeypatch.delenv("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_WORKSPACE_CLI_CONFIG_DIR", raising=False)
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(calendar_gate, "_native_gws_config_dir", lambda: gws_config)

    def fake_run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, stdout='{"items": []}', stderr="")

    monkeypatch.setattr(calendar_gate.subprocess, "run", fake_run)

    assert calendar_gate._default_gws_runner(["calendar", "calendarList", "list"], {}) == {"items": []}
    assert captured["env"]["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] == str(gws_config)
    assert "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE" not in captured["env"]
