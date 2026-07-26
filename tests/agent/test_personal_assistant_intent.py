import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from agent.personal_assistant_calendar_gate import (
    begin_calendar_first_planning_turn,
    same_day_grounding_gate_message,
)
from agent.personal_assistant_intent import (
    build_personal_assistant_turn_context,
    resolve_turn_intention,
)


def test_daily_planning_requests_are_canonical_planning_intents():
    for message in (
        "תכנן לי את שאר היום",
        "מה כדאי לי לעשות בזמן שנשאר היום?",
        "replan my remaining day",
        "schedule my week",
        "תעדף מחדש את השבוע שלי",
    ):
        assert resolve_turn_intention(message).action == "planning.query"


def test_committed_interview_continuation_is_a_canonical_planning_intent():
    resolved = resolve_turn_intention(
        "Continue personal-assistant interview planning-2026-07-24 after committed "
        'answer; receipt={"interviewRevision":5}.'
    )

    assert resolved.action == "planning.query"
    assert resolved.evidence == ("committed-interview-continuation",)


def test_direct_task_commands_are_not_reclassified_as_planning():
    assert resolve_turn_intention("תתחיל את המשימה").action.startswith("task.timer.start")
    assert resolve_turn_intention("תעלה את המשימה לדחיפות גבוהה").action == "general.query"


def test_hebrew_start_command_with_task_title_requires_lookup_and_execution():
    resolved = resolve_turn_intention(
        "תתחיל עכשיו את המשימה לבדוק אם התקבלו תוצאות בדיקות PET."
    )

    assert resolved.action == "task.timer.start.lookup"
    assert resolved.metadata == {"taskQuery": "לבדוק אם התקבלו תוצאות בדיקות PET"}


def test_hebrew_stop_command_is_a_canonical_timer_action():
    resolved = resolve_turn_intention("עצור את המשימה הנוכחית.")

    assert resolved.action == "task.timer.stop"


def _workflow(*, workflow_id: str = "planning-1", status: str = "active") -> dict:
    return {
        "workflowId": workflow_id,
        "type": "planning-interview",
        "status": status,
        "revision": 4,
        "currentTaskId": "pet-results",
        "currentQuestionId": "pet-next-step",
    }


def test_what_next_advances_the_only_active_workflow() -> None:
    resolved = resolve_turn_intention(
        "ok, what next?",
        workflows=[_workflow()],
        ui_binding={
            "workflowId": "planning-1",
            "taskId": "pet-results",
            "questionId": "pet-next-step",
            "revision": 4,
        },
    )

    assert resolved.action == "workflow.advance"
    assert resolved.workflow_id == "planning-1"
    assert resolved.requires_clarification is False
    assert resolved.evidence == ("active-workflow", "current-ui-binding")


def test_hebrew_what_next_advances_the_active_workflow() -> None:
    resolved = resolve_turn_intention("אוקיי, מה הדבר הבא?", workflows=[_workflow()])

    assert resolved.action == "workflow.advance"
    assert resolved.workflow_id == "planning-1"


def test_hebrew_activate_it_resolves_the_bound_flowstate_task() -> None:
    resolved = resolve_turn_intention(
        "תפעיל אותה",
        workflows=[_workflow()],
        ui_binding={"workflowId": "planning-1", "taskId": "pet-results"},
    )

    assert resolved.action == "task.timer.start"
    assert resolved.metadata["taskId"] == "pet-results"
    assert resolved.requires_clarification is False


def test_english_start_task_resolves_the_only_current_task() -> None:
    resolved = resolve_turn_intention("start this task", workflows=[_workflow()])

    assert resolved.action == "task.timer.start"
    assert resolved.metadata["taskId"] == "pet-results"


def test_unrelated_question_preserves_active_workflow() -> None:
    resolved = resolve_turn_intention(
        "What is the weather tomorrow?",
        workflows=[_workflow()],
    )

    assert resolved.action == "general.query"
    assert resolved.workflow_id is None
    assert resolved.preserve_workflow_ids == ("planning-1",)


def test_implicit_command_without_active_workflow_is_not_a_planning_request() -> None:
    resolved = resolve_turn_intention("what next?", workflows=[])

    assert resolved.action == "general.query"
    assert resolved.requires_clarification is False


def test_multiple_active_workflows_require_one_clarification() -> None:
    resolved = resolve_turn_intention(
        "continue",
        workflows=[_workflow(workflow_id="planning-1"), _workflow(workflow_id="health-1")],
    )

    assert resolved.action == "workflow.ambiguous"
    assert resolved.requires_clarification is True
    assert resolved.candidates == ("planning-1", "health-1")


def test_material_pending_action_is_not_approved_by_ambiguous_shorthand() -> None:
    resolved = resolve_turn_intention(
        "go ahead",
        workflows=[_workflow()],
        pending_actions=[
            {
                "id": "send-clinic-message",
                "consequence": "external-message",
                "approvedScope": None,
            }
        ],
    )

    assert resolved.action == "confirmation.required"
    assert resolved.requires_confirmation is True
    assert resolved.pending_action_id == "send-clinic-message"


def test_explicit_ui_answer_wins_over_free_text_guessing() -> None:
    resolved = resolve_turn_intention(
        "the second one",
        workflows=[_workflow()],
        ui_binding={
            "workflowId": "planning-1",
            "taskId": "pet-results",
            "questionId": "pet-next-step",
            "revision": 4,
            "selectedValues": ["call-clinic"],
        },
    )

    assert resolved.action == "workflow.answer"
    assert resolved.answer == {"selectedValues": ["call-clinic"]}


def test_turn_context_reads_authoritative_nested_interview_cursor() -> None:
    interview = {
        "interviewId": "planning-1",
        "revision": 7,
        "status": "active",
        "cursor": {"taskId": "pet-results", "questionId": "urgency"},
        "tasks": [{"taskId": "pet-results", "title": "Check PET results"}],
    }

    class Store:
        def get_planning_interview(self):
            return interview

        def public(self):
            return {"version": 3}

    context, resolved, returned = build_personal_assistant_turn_context(
        SimpleNamespace(
            personal_assistant_mode=True,
            personal_assistant_state_store=Store(),
        ),
        "what next?",
    )
    payload = json.loads(context.split("```json\n", 1)[1].rsplit("\n```", 1)[0])

    assert resolved.action == "workflow.advance"
    assert returned == interview
    assert payload["activeWorkflow"]["currentTaskId"] == "pet-results"
    assert payload["activeWorkflow"]["currentQuestionId"] == "urgency"
    assert payload["activeWorkflow"]["currentTask"]["title"] == "Check PET results"


def test_timer_intent_context_carries_task_id_and_mandates_tool_execution() -> None:
    interview = {
        "interviewId": "planning-1",
        "status": "active",
        "cursor": {"taskId": "pet-results", "questionId": "urgency"},
        "tasks": [{"taskId": "pet-results", "title": "Check PET results"}],
    }

    class Store:
        def get_planning_interview(self):
            return interview

        def public(self):
            return {"version": 3}

    context, resolved, _returned = build_personal_assistant_turn_context(
        SimpleNamespace(
            personal_assistant_mode=True,
            personal_assistant_state_store=Store(),
        ),
        "start this task",
    )
    payload = json.loads(context.split("```json\n", 1)[1].rsplit("\n```", 1)[0])

    assert resolved.action == "task.timer.start"
    assert payload["intent"]["metadata"] == {"taskId": "pet-results"}
    assert "must call flowstate_start_timer" in context
    assert "prose-only" in context
    assert "empty or skipped clarify answer cancels" in context


def test_named_timer_intent_context_preserves_task_name_for_safe_fallback() -> None:
    class Store:
        def get_planning_interview(self):
            return None

        def public(self):
            return {"version": 3}

    context, resolved, _returned = build_personal_assistant_turn_context(
        SimpleNamespace(
            personal_assistant_mode=True,
            personal_assistant_state_store=Store(),
        ),
        "תתחיל את המשימה לבדוק תשובה לגבי הצילום.",
    )
    payload = json.loads(context.split("```json\n", 1)[1].rsplit("\n```", 1)[0])

    assert resolved.action == "task.timer.start.lookup"
    assert payload["intent"]["metadata"] == {
        "taskQuery": "לבדוק תשובה לגבי הצילום"
    }


def test_ready_tomorrow_interview_unlocks_tomorrow_planning_sources() -> None:
    tomorrow = (datetime.now(ZoneInfo("Asia/Jerusalem")).date() + timedelta(days=1)).isoformat()
    interview = {
        "interviewId": f"daily-grounding-{tomorrow}",
        "planningDate": tomorrow,
        "readinessApproved": True,
        "status": "active",
        "cursor": {"taskId": None, "questionId": None},
        "tasks": [],
    }

    class Store:
        def get_planning_interview(self):
            return interview

        def public(self):
            return {"version": 3}

    try:
        build_personal_assistant_turn_context(
            SimpleNamespace(
                personal_assistant_mode=True,
                personal_assistant_state_store=Store(),
            ),
            "תכנן לי את מחר מהבוקר עד הערב",
        )

        assert same_day_grounding_gate_message("personal_assistant_calendar_preflight") is None
    finally:
        begin_calendar_first_planning_turn(required=False)
