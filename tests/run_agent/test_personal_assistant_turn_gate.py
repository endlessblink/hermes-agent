import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.conversation_loop import (
    _build_fast_personal_assistant_plan,
    _build_fast_personal_assistant_interview_response,
    _build_personal_assistant_empty_response_recovery,
    _build_initial_personal_assistant_planning_response,
    _build_post_tool_personal_assistant_interview_response,
    _build_ready_personal_assistant_plan,
    _effective_pressure_threshold,
    _parse_personal_assistant_plan_adjustment,
    _personal_assistant_pressure_exceeded,
    _personal_assistant_soft_pressure_requires_compaction,
    _successful_safety_review_in_batch,
)
from agent.personal_assistant_output_gate import OutputGateDecision


def test_personal_assistant_compacts_before_long_history_becomes_slow() -> None:
    assert _effective_pressure_threshold(120_000, personal_assistant_mode=True) == 60_000
    assert _effective_pressure_threshold(120_000, personal_assistant_mode=False) == 120_000
    assert _personal_assistant_pressure_exceeded(
        SimpleNamespace(personal_assistant_mode=True),
        60_000,
    )
    assert not _personal_assistant_pressure_exceeded(
        SimpleNamespace(personal_assistant_mode=False),
        80_000,
    )
    agent = SimpleNamespace(personal_assistant_mode=True)
    assert _personal_assistant_soft_pressure_requires_compaction(agent, 89_000, 0)
    assert not _personal_assistant_soft_pressure_requires_compaction(
        agent,
        89_000,
        1,
    )


def test_personal_assistant_finishes_immediately_after_verified_safety_review() -> None:
    assistant_message = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                id="call-review",
                function=SimpleNamespace(name="personal_assistant_safety_review"),
            )
        ]
    )
    messages = [
        {
            "content": json.dumps({"result": {"receipt": {"complete": True}}}),
            "role": "tool",
            "tool_call_id": "call-review",
        }
    ]

    assert _successful_safety_review_in_batch(messages, assistant_message)
    with (
        patch(
            "agent.personal_assistant_calendar_gate.calendar_first_task_inventory",
            return_value=({"task-1"}, True),
        ),
        patch(
            "agent.personal_assistant_calendar_gate.calendar_first_task_records",
            return_value={"task-1": {"id": "task-1", "title": "Task"}},
        ),
        patch(
            "agent.personal_assistant_calendar_gate.calendar_first_task_details",
            return_value={},
        ),
        patch(
            "agent.personal_assistant_output_gate.build_grounded_plan_fallback",
            return_value="compact plan",
        ),
    ):
        result = _build_fast_personal_assistant_plan(
            SimpleNamespace(personal_assistant_mode=True),
            SimpleNamespace(action="planning.query"),
            "plan my day",
            messages,
            assistant_message,
        )

    assert result == "compact plan"


def test_fast_plan_reads_its_actual_selected_tasks_before_rendering() -> None:
    assistant_message = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                id="call-review",
                function=SimpleNamespace(name="personal_assistant_safety_review"),
            )
        ]
    )
    messages = [
        {
            "content": json.dumps({"result": {"receipt": {"complete": True}}}),
            "role": "tool",
            "tool_call_id": "call-review",
        }
    ]
    records = {
        "jobs": {
            "id": "jobs",
            "title": "להגיש משרות ל10+2",
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-24",
            "estimatedDuration": None,
        },
        "medicine": {
            "id": "medicine",
            "title": "להזמין כדורים",
            "status": "todo",
            "priority": "high",
            "dueDate": "2026-07-23",
        },
        "clean": {
            "id": "clean",
            "title": "לנקות",
            "status": "todo",
            "priority": "medium",
            "dueDate": "2026-07-23",
        },
    }

    def get_task(args):
        task = dict(records[args["taskId"]])
        if args["taskId"] == "jobs":
            task["instances"] = [
                {
                    "scheduledDate": "2026-07-24",
                    "status": "scheduled",
                    "duration": 40,
                }
            ]
        return json.dumps({"result": {"task": task}})

    with (
        patch(
            "agent.personal_assistant_calendar_gate.calendar_first_task_inventory",
            return_value=(set(records), True),
        ),
        patch(
            "agent.personal_assistant_calendar_gate.calendar_first_task_records",
            return_value=records,
        ),
        patch(
            "agent.personal_assistant_calendar_gate.calendar_first_task_details",
            return_value={},
        ),
        patch("agent.personal_assistant_calendar_gate.record_calendar_first_task_detail"),
        patch("tools.flowstate_tool._handle_get_task", side_effect=get_task),
    ):
        result = _build_fast_personal_assistant_plan(
            SimpleNamespace(personal_assistant_mode=True),
            SimpleNamespace(action="planning.query"),
            "תכנן לי את שאר היום",
            messages,
            assistant_message,
        )

    assert result is not None
    assert "40 דק׳" in result


def _response(content: str):
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        model="test/model",
        usage=None,
    )


def _tool_response(name: str = "flowstate_get_task"):
    tool_call = SimpleNamespace(
        id="call-final-tool",
        function=SimpleNamespace(name=name, arguments='{"taskId":"task-1"}'),
    )
    message = SimpleNamespace(content="", tool_calls=[tool_call])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
        model="test/model",
        usage=None,
    )


class _InterviewStore:
    def __init__(self, interview: dict):
        self.interview = interview

    def get_planning_interview(self):
        return self.interview

    def public(self):
        return {
            "version": 9,
            "commitments": [{"id": "pet", "title": "Check PET results"}],
            "protectedItems": [{"id": "pet", "title": "Check PET results"}],
            "planningInterview": self.interview,
        }


def _interview() -> dict:
    return {
        "interviewId": "planning-1",
        "interviewRevision": 4,
        "status": "active",
        "readinessApproved": False,
        "currentTaskId": "pet-results",
        "currentQuestionId": "pet-next-step",
        "tasks": [{"id": "pet-results", "title": "Check PET results"}],
    }


def _task_card() -> str:
    return """```hermes-ui
{"type":"task-profile-review","id":"pet-card","interviewId":"planning-1","revision":4,"task":{"id":"pet-results","title":"PET results"},"title":"PET results","progress":{"current":1,"total":1},"profileFields":[{"id":"pet-next-step","label":"Next check"}],"question":{"id":"pet-next-step","profileFieldId":"pet-next-step","label":"What is the next check?","type":"single-choice","options":[{"value":"check","label":"Check results"}],"allowCustomAnswer":true}}
```"""


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    from run_agent import AIAgent

    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        instance = AIAgent(
            session_id="personal-assistant-gate-test",
            api_key="test-key",
            base_url="https://example.invalid/v1",
            provider="openai-compat",
            model="test/model",
            max_iterations=2,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    instance._cached_system_prompt = "stable test prompt"
    instance._session_db = None
    instance._session_json_enabled = False
    instance.save_trajectories = False
    instance.compression_enabled = False
    instance._cleanup_task_resources = lambda *_a, **_kw: None
    instance._save_trajectory = lambda *_a, **_kw: None
    instance.personal_assistant_mode = True
    instance.personal_assistant_state_store = _InterviewStore(_interview())
    return instance


def test_invalid_plan_is_privately_retried_before_persistence(agent, monkeypatch):
    answers = iter(
        [
            _response(
                "```hermes-ui\n"
                '{"type":"week-planner","weekStart":"2026-07-20","days":[]}\n'
                "```"
            ),
            _response(_task_card()),
        ]
    )
    sent_prompts = []

    def model_call(kwargs):
        sent_prompts.append(kwargs["messages"])
        return next(answers)

    agent._interruptible_api_call = model_call
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
    ):
        result = agent.run_conversation("what next?")

    assert result["final_response"] == _task_card()
    assert result["completed"] is True
    assert any(
        message.get("_personal_assistant_gate_synthetic") is True
        for message in result["messages"]
    )
    assert "workflow.advance" in sent_prompts[0][-1]["content"]
    assert "Check PET results" in sent_prompts[0][-1]["content"]


def test_committed_interview_continuation_never_calls_the_model_or_compressor(
    agent, monkeypatch
):
    agent._interruptible_api_call = lambda _kwargs: pytest.fail(
        "committed interview continuation reached the model"
    )
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]) as invoke_hook,
        patch(
            "agent.conversation_compression.conversation_history_after_compression"
        ) as compress,
        patch(
            "agent.personal_assistant_intent.build_personal_assistant_turn_context"
        ) as build_turn_context,
    ):
        result = agent.run_conversation(
            "Continue personal-assistant interview planning-1 after committed answer; "
            'receipt={"interviewRevision":4}.'
        )

    assert result["completed"] is True
    assert result["api_calls"] == 0
    assert '"type":"task-profile-review"' in result["final_response"]
    compress.assert_not_called()
    build_turn_context.assert_not_called()
    assert all(
        call.args[0] != "pre_llm_call"
        for call in invoke_hook.call_args_list
        if call.args
    )


def test_deterministic_timer_turn_never_calls_model_or_compressor(agent, monkeypatch):
    response = "```hermes-ui\n" + json.dumps(
        {
            "type": "task-table",
            "title": "משימה אמיתית",
            "columns": ["task"],
            "rows": [],
        },
        ensure_ascii=False,
    ) + "\n```"
    agent._interruptible_api_call = lambda _kwargs: pytest.fail(
        "deterministic timer turn reached the model"
    )
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]) as invoke_hook,
        patch(
            "agent.personal_assistant_timer.build_deterministic_timer_response",
            return_value=response,
        ),
        patch(
            "agent.conversation_compression.conversation_history_after_compression"
        ) as compress,
    ):
        result = agent.run_conversation("תתחיל את המשימה משימה אמיתית.")

    assert result["completed"] is True
    assert result["api_calls"] == 0
    assert result["final_response"] == response
    compress.assert_not_called()
    assert all(
        call.args[0] != "pre_llm_call"
        for call in invoke_hook.call_args_list
        if call.args
    )


def test_interview_start_tool_result_renders_its_question_without_another_model_call(
    agent,
):
    response = _build_post_tool_personal_assistant_interview_response(
        agent,
        {"personal_assistant_interview_start"},
    )

    assert response is not None
    assert '"type":"task-profile-review"' in response
    assert '"profileFieldId":"pet-next-step"' in response


def test_alternatives_action_parses_day_scope_and_visible_title_exclusions() -> None:
    preferred_title, excluded_titles = _parse_personal_assistant_plan_adjustment(
        "הצג שלוש אפשרויות אחרות לתכנון היום. "
        "אל תחזור על „משימה ראשונה”, „משימה שנייה”, „משימה שלישית”."
    )

    assert preferred_title == ""
    assert excluded_titles == (
        "משימה ראשונה",
        "משימה שנייה",
        "משימה שלישית",
    )


def test_alternatives_action_reuses_ready_interview_without_model_or_source_reads(
    agent,
) -> None:
    receipt = {
        "status": "complete",
        "complete": True,
        "expiresAt": "2099-01-01T00:00:00+00:00",
        "timezone": "Asia/Jerusalem",
        "range": {
            "startDate": "2026-07-24",
            "endDate": "2026-07-25",
        },
    }
    interview = {
        "interviewId": "planning-2026-07-24",
        "status": "active",
        "mode": "daily-grounding",
        "planningDate": "2026-07-24",
        "readinessApproved": True,
        "sourceSnapshot": {"calendarReceipt": receipt},
        "tasks": [{"taskId": "day-context", "title": "תכנון שאר היום"}],
    }
    request = (
        "הצג שלוש אפשרויות אחרות לתכנון היום. "
        "אל תחזור על „משימה ראשונה”, „משימה שנייה”, „משימה שלישית”."
    )

    with (
        patch(
            "agent.conversation_loop._build_ready_personal_assistant_plan",
            return_value="replacement plan",
        ) as build_plan,
        patch("tools.registry.registry.dispatch") as dispatch,
    ):
        response = _build_initial_personal_assistant_planning_response(
            agent,
            SimpleNamespace(action="planning.query"),
            request,
            interview,
        )

    assert response == "replacement plan"
    dispatch.assert_not_called()
    build_plan.assert_called_once_with(
        agent,
        interview,
        preferred_task_title="",
        excluded_task_titles=(
            "משימה ראשונה",
            "משימה שנייה",
            "משימה שלישית",
        ),
    )


def test_new_planning_request_opens_calendar_grounded_question_without_model(agent):
    receipt = {
        "status": "complete",
        "complete": True,
        "expiresAt": "2099-01-01T00:00:00+00:00",
        "timezone": "Asia/Jerusalem",
        "range": {
            "startDate": "2026-07-24",
            "endDate": "2026-07-25",
        },
    }
    started = {
        "interviewId": "planning-2026-07-24",
        "interviewRevision": 1,
        "status": "active",
        "mode": "daily-grounding",
        "planningDate": "2026-07-24",
        "readinessApproved": False,
        "sourceSnapshot": {"calendarReceipt": receipt},
        "tasks": [{"taskId": "day-context", "title": "תכנון מחר"}],
        "cursor": {"taskId": "day-context", "questionId": "availability"},
    }
    calls = []

    def dispatch(name, args):
        calls.append((name, dict(args)))
        if name == "personal_assistant_calendar_preflight":
            return json.dumps({"result": {"receipt": receipt}})
        return json.dumps({"result": {"interview": started}})

    with patch("tools.registry.registry.dispatch", side_effect=dispatch):
        response = _build_initial_personal_assistant_planning_response(
            agent,
            SimpleNamespace(action="planning.query"),
            "תכנן לי את מחר",
            None,
        )

    assert response is not None
    assert '"type":"task-profile-review"' in response
    assert "באילו שעות" in response
    assert [name for name, _args in calls] == [
        "personal_assistant_calendar_preflight",
        "personal_assistant_interview_start",
    ]


def test_week_planning_request_does_not_open_daily_grounding_interview(agent):
    with patch("tools.registry.registry.dispatch") as dispatch:
        response = _build_initial_personal_assistant_planning_response(
            agent,
            SimpleNamespace(action="planning.query"),
            (
                "תכנן לי את השבוע הבא לעומק לפי כל המשימות וכל היומנים, "
                "כולל תלות בין משימות, עומס בכל יום ושלוש חלופות לכל יום."
            ),
            None,
        )

    assert response is None
    dispatch.assert_not_called()


def test_hebrew_planning_request_survives_missing_precomputed_intent(agent):
    receipt = {
        "status": "complete",
        "complete": True,
        "expiresAt": "2099-01-01T00:00:00+00:00",
        "timezone": "Asia/Jerusalem",
        "range": {
            "startDate": "2026-07-24",
            "endDate": "2026-07-25",
        },
    }
    started = {
        "interviewId": "planning-2026-07-24",
        "interviewRevision": 1,
        "status": "active",
        "mode": "daily-grounding",
        "planningDate": "2026-07-24",
        "readinessApproved": False,
        "sourceSnapshot": {"calendarReceipt": receipt},
        "tasks": [{"taskId": "day-context", "title": "תכנון מחר"}],
        "cursor": {"taskId": "day-context", "questionId": "availability"},
    }

    def dispatch(name, _args):
        if name == "personal_assistant_calendar_preflight":
            return json.dumps({"result": {"receipt": receipt}})
        return json.dumps({"result": {"interview": started}})

    with patch("tools.registry.registry.dispatch", side_effect=dispatch):
        response = _build_initial_personal_assistant_planning_response(
            agent,
            None,
            (
                "תכנן לי את מחר מחדש לפי כל המשימות וכל היומנים. "
                "אל תשאל שוב על מה שכבר ידוע. תן לי רק 3 אפשרויות קצרות לפי סדר עדיפות."
            ),
            None,
        )

    assert response is not None
    assert '"type":"task-profile-review"' in response


def test_ready_interview_retrieves_every_configured_source_and_returns_plan_without_model(
    agent,
):
    from agent.personal_assistant_calendar_gate import (
        begin_calendar_first_planning_turn,
        calendar_preflight_gate,
        record_calendar_first_candidate_inventory,
    )

    interview = {
        "interviewId": "planning-1",
        "status": "active",
        "mode": "daily-grounding",
        "readinessApproved": True,
        "planningDate": "2026-07-24",
        "sourceSnapshot": {
            "calendarReceipt": {
                "status": "complete",
                "complete": True,
                "expiresAt": "2099-01-01T00:00:00+00:00",
                "timezone": "Asia/Jerusalem",
                "range": {
                    "startDate": "2026-07-24",
                    "endDate": "2026-07-25",
                },
            }
        },
        "tasks": [{"taskId": "day-context", "profile": {"availability": "09:00-21:00"}}],
    }
    agent.personal_assistant_state_store.interview = interview
    agent.personal_assistant_state_store.public = lambda: {
        "taskSourceManifest": [
            {
                "id": "flowstate",
                "inventoryTool": "flowstate_list_tasks",
                "available": True,
            },
            {
                "id": "notion-bina-work",
                "inventoryTool": "notion_data_source_list",
                "available": True,
            },
        ],
        "protectedItems": [],
    }
    begin_calendar_first_planning_turn(required=True)
    calls = []

    def dispatch(name, args):
        calls.append((name, dict(args)))
        if name == "flowstate_list_tasks":
            payload = {
                "complete": True,
                "total": 2,
                "items": [
                    {
                        "id": "flow-1",
                        "title": "משימת FlowState ראשונה",
                        "status": "todo",
                        "priority": "high",
                        "dueDate": "2026-07-24",
                    },
                    {
                        "id": "flow-2",
                        "title": "משימת FlowState שנייה",
                        "status": "todo",
                        "priority": "medium",
                        "dueDate": "2026-07-25",
                    },
                ],
            }
            record_calendar_first_candidate_inventory("flowstate", payload)
            return json.dumps({"result": payload})
        payload = {
            "ok": True,
            "query": {
                "has_more": False,
                "results": [
                    {
                        "id": "notion-1",
                        "properties": {
                            "Name": {
                                "type": "title",
                                "title": [{"plain_text": "משימת Notion חשובה"}],
                            },
                            "Priority": {
                                "type": "select",
                                "select": {"name": "High"},
                            },
                            "Due": {
                                "type": "date",
                                "date": {"start": "2026-07-24"},
                            },
                        },
                    }
                ],
            },
        }
        record_calendar_first_candidate_inventory("notion-bina-work", payload)
        return json.dumps(payload)

    with (
        patch("tools.registry.registry.dispatch", side_effect=dispatch),
        patch(
            "tools.flowstate_tool._handle_get_task",
            side_effect=lambda args: json.dumps(
                {
                    "result": {
                        "task": {
                            "id": args["taskId"],
                            "title": (
                                "משימת FlowState ראשונה"
                                if args["taskId"] == "flow-1"
                                else "משימת FlowState שנייה"
                            ),
                            "status": "todo",
                            "priority": "high",
                            "dueDate": "2026-07-24",
                        }
                    }
                }
            ),
        ),
    ):
        response = _build_post_tool_personal_assistant_interview_response(
            agent,
            {"personal_assistant_interview_start"},
        )

    assert response is not None
    assert '"type":"task-table"' in response
    assert "משימת Notion חשובה" in response
    assert (
        calendar_preflight_gate(
            "flowstate_list_tasks",
            interview["sourceSnapshot"]["calendarReceipt"],
        )
        is None
    )
    assert calls == [
        ("flowstate_list_tasks", {"limit": 100}),
        ("notion_data_source_list", {"page_size": 100}),
    ]


def test_ready_interview_names_failed_source_and_offers_safe_next_step(agent):
    from agent.personal_assistant_calendar_gate import begin_calendar_first_planning_turn

    interview = {
        "interviewId": "planning-1",
        "status": "active",
        "mode": "daily-grounding",
        "readinessApproved": True,
        "planningDate": "2026-07-24",
        "sourceSnapshot": {
            "calendarReceipt": {
                "status": "complete",
                "complete": True,
                "expiresAt": "2099-01-01T00:00:00+00:00",
                "timezone": "Asia/Jerusalem",
                "range": {
                    "startDate": "2026-07-24",
                    "endDate": "2026-07-25",
                },
            }
        },
        "tasks": [{"taskId": "day-context", "profile": {"availability": "09:00-21:00"}}],
    }
    agent.personal_assistant_state_store.public = lambda: {
        "taskSourceManifest": [
            {
                "id": "flowstate",
                "inventoryTool": "flowstate_list_tasks",
                "available": True,
            },
            {
                "id": "notion-bina-work",
                "inventoryTool": "notion_data_source_list",
                "available": True,
            },
        ],
        "protectedItems": [],
    }
    begin_calendar_first_planning_turn(required=True)

    with patch(
        "tools.registry.registry.dispatch",
        return_value=json.dumps(
            {
                "ok": False,
                "error": "Flow State Local Task API is unavailable",
                "error_type": "flowstate_unavailable",
            }
        ),
    ):
        response = _build_ready_personal_assistant_plan(agent, interview)

    assert response is not None
    assert "FlowState" in response
    assert "היומן נבדק" in response
    assert "לחבר מחדש את המקור" in response
    assert "ואבנה סביב זה" in response
    assert "נסה שוב בעוד רגע" not in response


def test_saved_ready_interview_never_finishes_with_an_empty_visible_response(agent):
    agent.personal_assistant_state_store.interview = {
        "interviewId": "planning-1",
        "status": "active",
        "mode": "daily-grounding",
        "readinessApproved": True,
        "tasks": [
            {
                "taskId": "day-context",
                "title": "תכנון מחר",
                "profile": {"availability": "09:00-21:00"},
            }
        ],
    }

    response = _build_personal_assistant_empty_response_recovery(agent)

    assert response is not None
    assert "התשובה נשמרה" in response
    assert "נסה שוב" in response
    assert "planning-1" not in response
    assert "taskId" not in response


def test_saved_ready_interview_empty_model_turn_returns_visible_recovery(agent):
    agent.personal_assistant_state_store.interview = {
        "interviewId": "planning-1",
        "status": "active",
        "mode": "daily-grounding",
        "readinessApproved": True,
        "tasks": [
            {
                "taskId": "day-context",
                "title": "תכנון מחר",
                "profile": {"availability": "09:00-21:00"},
            }
        ],
    }
    agent.max_iterations = 10
    agent._interruptible_api_call = lambda _kwargs: _response("")

    with patch(
        "agent.conversation_loop._build_initial_personal_assistant_planning_response",
        return_value=None,
    ):
        result = agent.run_conversation("תכנן לי את מחר")

    assert result["turn_exit_reason"] == "empty_response_exhausted"
    assert result["final_response"]
    assert "התשובה נשמרה" in result["final_response"]
    assert "Hermes finished without producing" not in result["final_response"]
    assert result["messages"][-1]["content"] == result["final_response"]


def test_committed_ready_interview_enters_grounded_plan_without_model(agent):
    agent.personal_assistant_state_store.interview = {
        "interviewId": "planning-1",
        "status": "active",
        "mode": "daily-grounding",
        "readinessApproved": True,
        "planningDate": "2026-07-24",
        "tasks": [
            {
                "taskId": "day-context",
                "profile": {"availability": "09:00-21:00"},
            }
        ],
    }

    with patch(
        "agent.conversation_loop._build_ready_personal_assistant_plan",
        return_value="grounded plan",
    ) as build_plan:
        response = _build_fast_personal_assistant_interview_response(
            agent,
            interview=agent.personal_assistant_state_store.interview,
            messages=[],
            current_turn_user_idx=0,
            user_message=(
                "Continue personal-assistant interview planning-1 after committed answer; "
                'receipt={"interviewRevision":2}.'
            ),
        )

    assert response == "grounded plan"
    build_plan.assert_called_once_with(
        agent,
        agent.personal_assistant_state_store.interview,
    )


def test_committed_ready_interview_recovers_before_restarted_agent_mode_is_hydrated(agent):
    agent.personal_assistant_mode = False
    agent.personal_assistant_state_store.interview = {
        "interviewId": "planning-1",
        "status": "active",
        "mode": "daily-grounding",
        "readinessApproved": True,
        "planningDate": "2026-07-24",
        "tasks": [
            {
                "taskId": "day-context",
                "profile": {"availability": "09:00-21:00"},
            }
        ],
    }

    with patch(
        "agent.conversation_loop._build_ready_personal_assistant_plan",
        return_value="recovered grounded plan",
    ):
        response = _build_fast_personal_assistant_interview_response(
            agent,
            interview=agent.personal_assistant_state_store.interview,
            messages=[],
            current_turn_user_idx=0,
            user_message=(
                "Continue personal-assistant interview planning-1 after committed answer; "
                'receipt={"interviewRevision":2}.'
            ),
        )

    assert response == "recovered grounded plan"


def test_completed_daily_interview_with_no_work_window_finishes_without_model_work(
    agent, monkeypatch
):
    agent.personal_assistant_state_store.interview = {
        "interviewId": "planning-1",
        "interviewRevision": 5,
        "status": "completed",
        "mode": "daily-grounding",
        "readinessApproved": True,
        "currentTaskId": None,
        "currentQuestionId": None,
        "tasks": [
            {
                "taskId": "day-context",
                "title": "תכנון שאר היום",
                "profile": {
                    "energy": "low",
                    "workBoundary": "עכשיו",
                    "location": "home",
                },
            }
        ],
    }
    agent._interruptible_api_call = lambda _kwargs: pytest.fail(
        "completed no-work interview reached the model"
    )
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch(
            "agent.conversation_loop._build_initial_personal_assistant_planning_response",
            return_value=None,
        ),
        patch(
            "agent.conversation_compression.conversation_history_after_compression"
        ) as compress,
    ):
        result = agent.run_conversation(
            "Continue personal-assistant interview planning-1 after committed answer; "
            'receipt={"interviewRevision":5}.'
        )

    assert result["completed"] is True
    assert result["api_calls"] == 0
    assert "אין חלון עבודה נוסף" in result["final_response"]
    compress.assert_not_called()


def test_short_acknowledgement_resumes_active_interview_without_model_work(
    agent, monkeypatch
):
    agent._interruptible_api_call = lambda _kwargs: pytest.fail(
        "safe acknowledgement reached the model"
    )
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
    ):
        result = agent.run_conversation(
            "נכון",
            conversation_history=[
                {
                    "role": "assistant",
                    "content": "בדקתי מחדש את היומן. המפגש עם רבקה הוא 20:00–23:00.",
                }
            ],
        )

    assert result["completed"] is True
    assert result["api_calls"] == 0
    assert '"type":"task-profile-review"' in result["final_response"]


def test_short_answer_to_an_explicit_question_still_reaches_the_model(
    agent, monkeypatch
):
    calls = 0

    def model_call(_kwargs):
        nonlocal calls
        calls += 1
        return _response(_task_card())

    agent._interruptible_api_call = model_call
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
    ):
        result = agent.run_conversation(
            "כן",
            conversation_history=[
                {"role": "assistant", "content": "האם לשנות את המשימה?"}
            ],
        )

    assert result["completed"] is True
    assert result["api_calls"] == 1
    assert calls == 1


def test_later_gate_retries_keep_every_prior_correction(agent, monkeypatch):
    agent.max_iterations = 3
    agent.iteration_budget.max_total = 3
    answers = iter(
        [
            _response("first invalid answer"),
            _response("second invalid answer"),
            _response("valid final answer"),
        ]
    )
    decisions = iter(
        [
            OutputGateDecision(False, "invalid_task_table", "Remove cells.task."),
            OutputGateDecision(
                False,
                "planning_interactivity_required",
                "Add two useful actions per task.",
            ),
            OutputGateDecision(True),
        ]
    )
    sent_prompts = []

    def model_call(kwargs):
        sent_prompts.append(kwargs["messages"])
        return next(answers)

    agent._interruptible_api_call = model_call
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch(
            "agent.conversation_loop._build_initial_personal_assistant_planning_response",
            return_value=None,
        ),
        patch(
            "agent.personal_assistant_output_gate.evaluate_personal_assistant_output",
            side_effect=lambda *_args, **_kwargs: next(decisions),
        ),
    ):
        result = agent.run_conversation("plan the rest of my day")

    assert result["final_response"] == "valid final answer"
    final_correction = sent_prompts[2][-1]["content"]
    assert "Remove cells.task." in final_correction
    assert "Add two useful actions per task." in final_correction


def test_missing_pending_approval_explanation_is_repaired_without_discarding_the_plan(agent, monkeypatch):
    agent.max_iterations = 1
    agent.iteration_budget.max_total = 1
    agent._interruptible_api_call = lambda _kwargs: _response("revised plan")
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch(
            "agent.personal_assistant_output_gate.evaluate_personal_assistant_output",
            return_value=OutputGateDecision(
                False,
                "durable_capture_approval_explanation_required",
                "Explain that the proposal is awaiting approval.",
            ),
        ),
    ):
        result = agent.run_conversation("תזכור את התיקון ותתכנן מחדש")

    assert "revised plan" in result["final_response"]
    assert "ממתינה לאישור" in result["final_response"]
    assert "טרם נשמר" in result["final_response"]


def test_invalid_grounded_plan_uses_verified_options_only_after_repair_attempts(agent, monkeypatch):
    answers = iter([_response("invalid first plan"), _response("invalid second plan")])
    model_calls = 0
    records = {
        "one": {"title": "First real task", "dueDate": "2026-07-22"},
        "two": {"title": "Second real task", "dueDate": "2026-07-23"},
        "three": {"title": "Third real task", "dueDate": "2026-07-24"},
    }
    def model_call(_kwargs):
        nonlocal model_calls
        model_calls += 1
        return next(answers)

    agent._interruptible_api_call = model_call
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch(
            "agent.conversation_loop._build_initial_personal_assistant_planning_response",
            return_value=None,
        ),
        patch(
            "agent.personal_assistant_output_gate.evaluate_personal_assistant_output",
            return_value=OutputGateDecision(False, "compact_day_plan_options_required", "Return a compact table."),
        ),
        patch("agent.personal_assistant_calendar_gate.calendar_first_task_records", return_value=records),
        patch("agent.personal_assistant_calendar_gate.calendar_first_task_details", return_value=records),
        patch("agent.personal_assistant_calendar_gate.calendar_first_task_inventory", return_value=(frozenset(records), True)),
    ):
        result = agent.run_conversation("plan the rest of my day with 3 options")

    assert "First real task" in result["final_response"]
    assert "Second real task" in result["final_response"]
    assert "Third real task" in result["final_response"]
    assert "could not finish checking" not in result["final_response"]
    assert model_calls == 2


def test_explicit_correction_is_not_discarded_by_the_generic_plan_fallback(agent, monkeypatch):
    answers = iter([_response("first incomplete plan"), _response("revised plan with correction")])
    decisions = iter(
        [
            OutputGateDecision(False, "planning_interactivity_required", "Add useful actions."),
            OutputGateDecision(True),
        ]
    )
    agent._interruptible_api_call = lambda _kwargs: next(answers)
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")
    records = {
        "one": {"title": "First real task"},
        "two": {"title": "Second real task"},
        "three": {"title": "Third real task"},
    }

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch(
            "agent.personal_assistant_output_gate.evaluate_personal_assistant_output",
            side_effect=lambda *_args, **_kwargs: next(decisions),
        ),
        patch("agent.personal_assistant_calendar_gate.calendar_first_task_records", return_value=records),
        patch("agent.personal_assistant_calendar_gate.calendar_first_task_details", return_value=records),
        patch(
            "agent.personal_assistant_calendar_gate.calendar_first_task_inventory",
            return_value=(frozenset(records), True),
        ),
    ):
        result = agent.run_conversation("תזכור שהמשימה לוקחת 40 דקות ותתכנן מחדש")

    assert result["final_response"] == "revised plan with correction"


def test_non_personal_assistant_turn_is_unchanged(agent, monkeypatch):
    agent.personal_assistant_mode = False
    plan = (
        "```hermes-ui\n"
        '{"type":"week-planner","weekStart":"2026-07-20","days":[]}\n'
        "```"
    )
    agent._interruptible_api_call = lambda _kwargs: _response(plan)
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch(
            "agent.conversation_loop._build_initial_personal_assistant_planning_response",
            return_value=None,
        ),
    ):
        result = agent.run_conversation("make a plan")

    assert result["final_response"] == plan
    assert not any(
        message.get("_personal_assistant_gate_synthetic")
        for message in result["messages"]
    )


def test_explicit_lasting_update_cannot_finish_with_a_memory_promise(agent, monkeypatch):
    agent.personal_assistant_state_store.interview = None
    agent.valid_tool_names = {"personal_assistant_propose_capture"}
    answers = iter(
        [
            _response("Got it. I will remember that."),
            _response("Understood. I will keep that preference in mind."),
        ]
    )
    sent_prompts = []

    def model_call(kwargs):
        sent_prompts.append(kwargs["messages"])
        return next(answers)

    agent._interruptible_api_call = model_call
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch(
            "agent.conversation_loop._build_initial_personal_assistant_planning_response",
            return_value=None,
        ),
    ):
        result = agent.run_conversation(
            "From now on, always keep morning plans under three items"
        )

    assert result["final_response"] not in {
        "Got it. I will remember that.",
        "Understood. I will keep that preference in mind.",
    }
    assert any(
        message.get("_personal_assistant_gate_synthetic") is True
        for message in result["messages"]
    )
    assert "personal_assistant_propose_capture" in sent_prompts[1][-1]["content"]


def test_desktop_prose_question_is_privately_retried_through_clarify(agent, monkeypatch):
    agent.personal_assistant_mode = False
    agent.platform = "desktop"
    agent.valid_tool_names = {"clarify"}
    answers = iter(
        [
            _response(
                "לפני שאני מתקדם — מה הבעיה בשיעור הראשון?\n\n"
                "- הוא מתחיל כללי מדי?\n"
                "- חסר שם דמו ברור?"
            ),
            _response("I need your answer through the question control."),
        ]
    )
    sent_prompts = []

    def model_call(kwargs):
        sent_prompts.append(kwargs["messages"])
        return next(answers)

    agent._interruptible_api_call = model_call
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch(
            "agent.conversation_loop._build_initial_personal_assistant_planning_response",
            return_value=None,
        ),
    ):
        result = agent.run_conversation("תמשיך")

    assert result["final_response"] == "I need your answer through the question control."
    assert any(
        message.get("_desktop_clarify_gate_synthetic") is True
        for message in result["messages"]
    )
    assert "call the `clarify` tool" in sent_prompts[1][-1]["content"].lower()


def test_gate_exhaustion_returns_safe_interaction_not_invalid_plan(agent, monkeypatch):
    agent.max_iterations = 1
    agent.iteration_budget.max_total = 1
    invalid = (
        "```hermes-ui\n"
        '{"type":"day-timeline","date":"2026-07-20","blocks":[]}\n'
        "```"
    )
    agent._interruptible_api_call = lambda _kwargs: _response(invalid)
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
    ):
        result = agent.run_conversation("what next?")

    assert result["final_response"] != invalid
    assert '"type":"task-profile-review"' in result["final_response"]
    assert result["messages"][-1]["content"] == result["final_response"]


def test_personal_assistant_gets_one_visible_answer_after_final_budget_tool(
    agent, monkeypatch
):
    agent.max_iterations = 1
    agent.iteration_budget.max_total = 1
    agent.valid_tool_names = {"flowstate_get_task"}
    answers = iter([_tool_response(), _response("בדקתי. הנה התוצאה הבטוחה.")])
    model_calls = 0

    def model_call(_kwargs):
        nonlocal model_calls
        model_calls += 1
        return next(answers)

    agent._interruptible_api_call = model_call
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch("run_agent.handle_function_call", return_value='{"taskId":"task-1"}'),
        patch(
            "agent.personal_assistant_output_gate.evaluate_personal_assistant_output",
            return_value=OutputGateDecision(True),
        ),
    ):
        result = agent.run_conversation("בדוק את המשימה")

    assert model_calls == 2
    assert result["final_response"] == "בדקתי. הנה התוצאה הבטוחה."


def test_named_duration_correction_retries_interview_until_canonical_preview(
    agent, monkeypatch
):
    agent.personal_assistant_state_store.interview = None
    agent.max_iterations = 4
    agent.iteration_budget.max_total = 4
    agent.valid_tool_names = {"flowstate_update_task"}
    answers = iter(
        [
            _response("מתי האנרגיה שלך צפויה להיות טובה יותר מחר?"),
            _tool_response("flowstate_update_task"),
            _response("העדכון למשימה ממתין לאישור שלך."),
        ]
    )
    model_calls = 0

    def model_call(_kwargs):
        nonlocal model_calls
        model_calls += 1
        return next(answers)

    agent._interruptible_api_call = model_call
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch(
            "run_agent.handle_function_call",
            return_value='{"result":"preview","normalizedPayload":{"estimatedDuration":40}}',
        ),
    ):
        result = agent.run_conversation(
            "המשימה להגיש משרות ל10+2 לוקחת 40 דקות, לא 25, "
            "והיא בעדיפות גבוהה. תזכור את זה ותתכנן את מחר מחדש."
        )

    assert model_calls == 3
    assert result["final_response"] == "העדכון למשימה ממתין לאישור שלך."


def test_gate_uses_interview_started_during_the_same_turn(agent, monkeypatch):
    agent.personal_assistant_state_store.interview = None
    answers = iter([
        _response(
            "```hermes-ui\n"
            '{"type":"week-planner","weekStart":"2026-07-20","days":[]}\n'
            "```"
        ),
        _response(_task_card()),
    ])

    def model_call(_kwargs):
        agent.personal_assistant_state_store.interview = _interview()
        return next(answers)

    agent._interruptible_api_call = model_call
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch(
            "agent.conversation_loop._build_initial_personal_assistant_planning_response",
            return_value=None,
        ),
    ):
        result = agent.run_conversation("plan my week")

    assert result["final_response"] == _task_card()
    assert any(
        message.get("_personal_assistant_gate_synthetic") is True
        for message in result["messages"]
    )


def test_desktop_renders_same_turn_interview_card_without_clarify_retry(agent, monkeypatch):
    agent.platform = "desktop"
    agent.valid_tool_names = {"clarify", "personal_assistant_interview_start"}
    agent.personal_assistant_state_store.interview = None
    calls = 0

    def model_call(_kwargs):
        nonlocal calls
        calls += 1
        agent.personal_assistant_state_store.interview = _interview()
        return _response(_task_card())

    agent._interruptible_api_call = model_call
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch(
            "agent.conversation_loop._build_initial_personal_assistant_planning_response",
            return_value=None,
        ),
    ):
        result = agent.run_conversation("plan the rest of today")

    assert result["completed"] is True
    assert result["final_response"] == _task_card()
    assert calls == 1
    assert not any(
        message.get("_desktop_clarify_gate_synthetic") is True
        for message in result["messages"]
    )


def test_planning_retry_resumes_active_interview_without_model_retry(agent, monkeypatch):
    agent.platform = "desktop"
    agent.valid_tool_names = {"clarify", "personal_assistant_interview_start"}
    calls = 0

    def model_call(_kwargs):
        nonlocal calls
        calls += 1
        return _response("The task sources are blocked, so no plan can be returned.")

    agent._interruptible_api_call = model_call
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
        patch(
            "agent.conversation_loop._build_initial_personal_assistant_planning_response",
            return_value=None,
        ),
    ):
        result = agent.run_conversation("plan the rest of today")

    assert result["completed"] is True
    assert '"type":"task-profile-review"' in result["final_response"]
    assert calls == 1
    assert not any(
        message.get("_personal_assistant_gate_synthetic") is True
        for message in result["messages"]
    )


def test_general_query_cannot_bypass_interview_with_planning_report(agent, monkeypatch):
    agent.max_iterations = 1
    agent.iteration_budget.max_total = 1
    invalid = """## תמונת על של שלושת הקורסים

| תוצר מרכזי | אורך | פורמט |
| --- | --- | --- |
| workflow עובד | 4 שיעורים | אונליין |

## מה עדיין צריך לדייק

1. באיזה כלי עובדים בפועל?
2. איזה סוג פרויקטים הכי מתאים לקהל?
3. האם המשתתף מביא פרויקט משלו?
4. האם הקורס צריך להיות סביב מוצר אחד בלבד?
"""
    agent._interruptible_api_call = lambda _kwargs: _response(invalid)
    monkeypatch.setenv("HERMES_VERIFY_ON_STOP", "0")

    with (
        patch("hermes_cli.plugins.has_hook", return_value=False),
        patch("hermes_cli.plugins.invoke_hook", return_value=[]),
    ):
        result = agent.run_conversation("יר")

    assert result["final_response"] != invalid
    assert '"type":"task-profile-review"' in result["final_response"]
    assert result["messages"][-1]["finish_reason"] == "personal_assistant_safe_fallback"
