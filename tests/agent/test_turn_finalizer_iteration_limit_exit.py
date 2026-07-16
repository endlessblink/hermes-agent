"""Regression tests for iteration-limit exit normalization (#61631)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.turn_finalizer import finalize_turn


class _LimitAgent:
    def __init__(
        self,
        *,
        max_iterations=60,
        budget_remaining=0,
        completion_explainer=False,
    ):
        self.max_iterations = max_iterations
        self.iteration_budget = SimpleNamespace(
            remaining=budget_remaining, used=max_iterations, max_total=max_iterations
        )
        self.quiet_mode = True
        self.model = "test-model"
        self.provider = "test-provider"
        self.base_url = ""
        self.session_id = "sess-test"
        self.context_compressor = SimpleNamespace(last_prompt_tokens=0)
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.session_cache_read_tokens = 0
        self.session_cache_write_tokens = 0
        self.session_reasoning_tokens = 0
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self.session_estimated_cost_usd = 0
        self.session_cost_status = "unknown"
        self.session_cost_source = "test"
        self._tool_guardrail_halt_decision = None
        self._interrupt_message = None
        self._response_was_previewed = False
        self._skill_nudge_interval = 0
        self._iters_since_skill = 0
        self.valid_tool_names = []
        self.persisted_messages = None
        self._handle_max_iterations_called = False
        self._completion_explainer = completion_explainer
        self._resumable_iteration_checkpoint = False

    def _handle_max_iterations(self, messages, api_call_count):
        self._handle_max_iterations_called = True
        return "summary from extra call"

    def _emit_status(self, *_args, **_kwargs):
        pass

    def _safe_print(self, *_args, **_kwargs):
        pass

    def _save_trajectory(self, *_args, **_kwargs):
        pass

    def _cleanup_task_resources(self, *_args, **_kwargs):
        pass

    def _drop_trailing_empty_response_scaffolding(self, messages):
        pass

    def _persist_session(self, messages, conversation_history):
        self.persisted_messages = list(messages)

    def _file_mutation_verifier_enabled(self):
        return False

    def _turn_completion_explainer_enabled(self):
        return self._completion_explainer

    def _format_turn_completion_explanation(self, _reason):
        return "iteration-limit explanation"

    def _drain_pending_steer(self):
        return None

    def clear_interrupt(self):
        pass

    def _sync_external_memory_for_turn(self, **_kwargs):
        pass


def _finalize(
    agent,
    *,
    final_response,
    exit_reason,
    api_call_count=60,
    pending_verification_response=None,
):
    return finalize_turn(
        agent,
        final_response=final_response,
        api_call_count=api_call_count,
        interrupted=False,
        failed=False,
        messages=[{"role": "user", "content": "task"}],
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="task",
        original_user_message="task",
        _should_review_memory=False,
        _turn_exit_reason=exit_reason,
        _pending_verification_response=pending_verification_response,
    )


def test_pending_verify_response_is_preserved_for_cron_delivery(monkeypatch):
    """A held-back verification response survives last-turn exhaustion."""
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent()
    report = "complete cron report body"

    result = _finalize(
        agent,
        final_response=None,
        exit_reason="unknown",
        pending_verification_response=report,
    )

    assert result["final_response"] == report
    assert result["turn_exit_reason"] == "max_iterations_reached(60/60)"
    assert agent._handle_max_iterations_called is False


def test_pending_pre_verify_response_is_preserved_on_budget_exhaustion(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent()
    report = "budget exhausted but complete"

    result = _finalize(
        agent,
        final_response=None,
        exit_reason="budget_exhausted",
        pending_verification_response=report,
    )

    assert result["final_response"] == report
    assert result["turn_exit_reason"] == "max_iterations_reached(60/60)"
    assert agent._handle_max_iterations_called is False


def test_empty_pending_verification_response_uses_summary_fallback(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent()

    result = _finalize(
        agent,
        final_response=None,
        exit_reason="unknown",
        pending_verification_response="",
    )

    assert result["final_response"] == "summary from extra call"
    assert result["turn_exit_reason"] == "max_iterations_reached(60/60)"
    assert agent._handle_max_iterations_called is True


def test_personal_assistant_budget_exhaustion_returns_resumable_checkpoint(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent(max_iterations=6)
    agent._resumable_iteration_checkpoint = True
    messages = [
        {"role": "user", "content": "organize my day and update the approved tasks"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "flowstate_list_tasks", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "flowstate_list_tasks",
            "content": '{"tasks": []}',
        },
    ]

    result = finalize_turn(
        agent,
        final_response=None,
        api_call_count=6,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="organize my day and update the approved tasks",
        original_user_message="organize my day and update the approved tasks",
        _should_review_memory=False,
        _turn_exit_reason="budget_exhausted",
    )

    assert agent._handle_max_iterations_called is False
    assert result["completed"] is False
    assert result["turn_exit_reason"] == "max_iterations_reached(6/6)"
    assert result["continuation_checkpoint"] == {
        "version": 1,
        "reason": "iteration_budget_exhausted",
        "resumable": True,
        "completed_phases": ["Read FlowState tasks"],
        "pending_phase": "Review the latest FlowState task list and continue the requested work.",
    }
    assert "resumable checkpoint" in result["final_response"].lower()
    assert "Continue" in result["final_response"]


def test_resumable_checkpoint_is_persisted_as_active_working_state(monkeypatch, tmp_path):
    from hermes_state import SessionDB

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("sess-test", "cli", model="test-model")
    agent = _LimitAgent(max_iterations=6)
    agent._resumable_iteration_checkpoint = True
    agent._session_db = db
    messages = [
        {"role": "user", "content": "prepare and apply my approved plan"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "flowstate_update_task",
                        "arguments": '{"id":"task-1","preview":true}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "flowstate_update_task",
            "content": '{"preview": "p1"}',
        },
    ]

    finalize_turn(
        agent,
        final_response=None,
        api_call_count=6,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="prepare and apply my approved plan",
        original_user_message="prepare and apply my approved plan",
        _should_review_memory=False,
        _turn_exit_reason="budget_exhausted",
    )

    state = db.get_working_state("sess-test")
    assert state["active_task"] == "prepare and apply my approved plan"
    assert state["status"] == "checkpoint"
    assert state["phase"] == (
        "Review the FlowState change preview; apply it only if it is already approved, "
        "then verify the canonical result."
    )
    assert state["completed_actions"] == ["Prepared a FlowState change preview"]
    assert state["blockers"] == ["This turn reached its iteration budget before the workflow finished."]


def test_failed_tool_result_is_pending_not_completed(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent(max_iterations=6)
    agent._resumable_iteration_checkpoint = True
    messages = [
        {"role": "user", "content": "find the task"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "flowstate_search_tasks", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "flowstate_search_tasks",
            "content": "- Status: 400\n- Error: query is required",
        },
    ]

    result = finalize_turn(
        agent,
        final_response=None,
        api_call_count=6,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="find the task",
        original_user_message="find the task",
        _should_review_memory=False,
        _turn_exit_reason="budget_exhausted",
    )

    checkpoint = result["continuation_checkpoint"]
    assert checkpoint["completed_phases"] == []
    assert checkpoint["pending_phase"] == (
        "Correct or retry the failed FlowState task search, then continue the requested work."
    )


def test_repeated_checkpoint_keeps_original_task_and_prior_progress(monkeypatch, tmp_path):
    from hermes_state import SessionDB

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("sess-test", "cli", model="test-model")
    db.set_working_state(
        "sess-test",
        {
            "active_task": "organize my day and apply the approved plan",
            "status": "checkpoint",
            "completed_actions": ["Read FlowState tasks"],
        },
        source="test",
    )
    agent = _LimitAgent(max_iterations=6)
    agent._resumable_iteration_checkpoint = True
    agent._session_db = db
    messages = [
        {"role": "user", "content": "continue and resume"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {
                        "name": "flowstate_update_task",
                        "arguments": '{"id":"task-1","preview":true}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-2",
            "name": "flowstate_update_task",
            "content": '{"preview": "p2"}',
        },
    ]

    finalize_turn(
        agent,
        final_response=None,
        api_call_count=6,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn-2",
        user_message="continue and resume",
        original_user_message="continue and resume",
        _should_review_memory=False,
        _turn_exit_reason="budget_exhausted",
    )

    state = db.get_working_state("sess-test")
    assert state["active_task"] == "organize my day and apply the approved plan"
    assert state["completed_actions"] == [
        "Read FlowState tasks",
        "Prepared a FlowState change preview",
    ]


def test_checkpoint_only_reports_tools_from_current_turn(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent(max_iterations=6)
    agent._resumable_iteration_checkpoint = True
    messages = [
        {"role": "user", "content": "old request"},
        {
            "role": "tool",
            "name": "notion_list_tasks",
            "content": '{"tasks": ["old"]}',
        },
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new request"},
        {
            "role": "tool",
            "name": "flowstate_list_tasks",
            "content": '{"tasks": ["new"]}',
        },
    ]

    result = finalize_turn(
        agent,
        final_response=None,
        api_call_count=6,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="new request",
        original_user_message="new request",
        _should_review_memory=False,
        _turn_exit_reason="budget_exhausted",
        current_turn_user_idx=3,
    )

    assert result["continuation_checkpoint"]["completed_phases"] == ["Read FlowState tasks"]


def test_checkpoint_uses_stable_turn_marker_after_compression(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent(max_iterations=6)
    agent._resumable_iteration_checkpoint = True
    messages = [
        {"role": "user", "content": "[CONTEXT COMPACTION] old tools summarized"},
        {"role": "tool", "name": "notion_list_tasks", "content": '{"tasks": ["old"]}'},
        {"role": "user", "content": "continue", "_turn_id": "turn-current"},
        {"role": "tool", "name": "flowstate_list_tasks", "content": '{"tasks": ["new"]}'},
    ]

    result = finalize_turn(
        agent,
        final_response=None,
        api_call_count=6,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn-current",
        user_message="continue",
        original_user_message="continue",
        _should_review_memory=False,
        _turn_exit_reason="budget_exhausted",
        current_turn_user_idx=99,
    )

    assert result["continuation_checkpoint"]["completed_phases"] == ["Read FlowState tasks"]


def test_successful_same_tool_retry_clears_failure(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent(max_iterations=6)
    agent._resumable_iteration_checkpoint = True
    messages = [
        {"role": "user", "content": "find the task"},
        {
            "role": "tool",
            "name": "flowstate_search_tasks",
            "content": "Error executing tool 'flowstate_search_tasks': query is required",
        },
        {
            "role": "tool",
            "name": "flowstate_search_tasks",
            "content": '{"tasks": ["task-1"]}',
        },
    ]

    result = finalize_turn(
        agent,
        final_response=None,
        api_call_count=6,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="find the task",
        original_user_message="find the task",
        _should_review_memory=False,
        _turn_exit_reason="budget_exhausted",
    )

    checkpoint = result["continuation_checkpoint"]
    assert checkpoint["completed_phases"] == ["Read FlowState tasks"]
    assert checkpoint["pending_phase"] == (
        "Review the latest FlowState task list and continue the requested work."
    )


def test_unrelated_success_does_not_hide_earlier_failure(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent(max_iterations=6)
    agent._resumable_iteration_checkpoint = True
    messages = [
        {"role": "user", "content": "find and inspect the task"},
        {
            "role": "tool",
            "name": "flowstate_search_tasks",
            "content": "- Status: 400\n- Error: query is required",
        },
        {"role": "tool", "name": "flowstate_list_tasks", "content": '{"tasks": []}'},
    ]

    result = finalize_turn(
        agent,
        final_response=None,
        api_call_count=6,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="find and inspect the task",
        original_user_message="find and inspect the task",
        _should_review_memory=False,
        _turn_exit_reason="budget_exhausted",
    )

    assert result["continuation_checkpoint"]["pending_phase"] == (
        "Correct or retry the failed FlowState task search, then continue the requested work."
    )


def test_activation_apply_is_not_reported_as_preview(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent(max_iterations=6)
    agent._resumable_iteration_checkpoint = True
    messages = [
        {"role": "user", "content": "apply the approved activation"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-activate",
                    "type": "function",
                    "function": {
                        "name": "notion_flowstate_activate",
                        "arguments": '{"mode":"apply"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-activate",
            "name": "notion_flowstate_activate",
            "content": '{"status":"committed"}',
        },
    ]

    result = finalize_turn(
        agent,
        final_response=None,
        api_call_count=6,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="apply the approved activation",
        original_user_message="apply the approved activation",
        _should_review_memory=False,
        _turn_exit_reason="budget_exhausted",
    )

    assert result["continuation_checkpoint"]["completed_phases"] == [
        "Activated a Notion task in FlowState"
    ]


def test_new_task_checkpoint_replaces_prior_task_progress(monkeypatch, tmp_path):
    from hermes_state import SessionDB

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("sess-test", "cli", model="test-model")
    db.set_working_state(
        "sess-test",
        {
            "active_task": "old task",
            "status": "checkpoint",
            "completed_actions": ["Read Notion tasks"],
        },
        source="test",
    )
    agent = _LimitAgent(max_iterations=6)
    agent._resumable_iteration_checkpoint = True
    agent._session_db = db
    messages = [
        {"role": "user", "content": "organize a different day"},
        {"role": "tool", "name": "flowstate_list_tasks", "content": '{"tasks": []}'},
    ]

    finalize_turn(
        agent,
        final_response=None,
        api_call_count=6,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="organize a different day",
        original_user_message="organize a different day",
        _should_review_memory=False,
        _turn_exit_reason="budget_exhausted",
        current_turn_user_idx=0,
    )

    state = db.get_working_state("sess-test")
    assert state["active_task"] == "organize a different day"
    assert state["completed_actions"] == ["Read FlowState tasks"]


def test_short_generated_summary_keeps_abnormal_turn_explainer(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent(completion_explainer=True)
    agent._handle_max_iterations = lambda *_args: "The"

    result = _finalize(agent, final_response=None, exit_reason="unknown")

    assert result["final_response"] == "The\n\niteration-limit explanation"


def test_short_preserved_verification_response_is_not_rewritten(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent(completion_explainer=True)

    result = _finalize(
        agent,
        final_response=None,
        exit_reason="unknown",
        pending_verification_response="The",
    )

    assert result["final_response"] == "The"


def test_text_response_exit_not_rewritten_at_iteration_limit(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent(budget_remaining=5)
    exit_reason = "text_response(finish_reason=stop)"

    result = _finalize(
        agent,
        final_response="normal answer",
        exit_reason=exit_reason,
        api_call_count=59,
    )

    assert result["turn_exit_reason"] == exit_reason
    assert agent._handle_max_iterations_called is False


@pytest.mark.parametrize(
    "exit_reason",
    [
        "error_near_max_iterations(boom)",
        "guardrail_halt",
        "partial_stream_recovery",
        "fallback_prior_turn_content",
        "empty_response_exhausted",
    ],
)
def test_unrelated_non_success_response_is_not_reclassified(monkeypatch, exit_reason):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent()

    result = _finalize(
        agent,
        final_response="diagnostic or partial content",
        exit_reason=exit_reason,
    )

    assert result["turn_exit_reason"] == exit_reason
    assert result["completed"] is False
    assert agent._handle_max_iterations_called is False


@pytest.mark.parametrize(
    ("exit_reason", "interrupted", "failed"),
    [
        ("interrupted_by_user", True, False),
        ("all_retries_exhausted_no_response", False, False),
        ("provider_failure", False, True),
    ],
)
def test_pending_response_does_not_mask_later_terminal_exit(
    monkeypatch, exit_reason, interrupted, failed
):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    agent = _LimitAgent()

    result = finalize_turn(
        agent,
        final_response=None,
        api_call_count=60,
        interrupted=interrupted,
        failed=failed,
        messages=[{"role": "user", "content": "task"}],
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="task",
        original_user_message="task",
        _should_review_memory=False,
        _turn_exit_reason=exit_reason,
        _pending_verification_response="stale premature report",
    )

    assert result["final_response"] is None
    assert result["turn_exit_reason"] == exit_reason
    assert result["completed"] is False
    assert agent._handle_max_iterations_called is False


def test_pending_response_records_kanban_timeout(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-123")
    record = MagicMock(name="record_task_failure")
    conn = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr("hermes_cli.kanban_db.connect", lambda: conn)
    monkeypatch.setattr("hermes_cli.kanban_db._record_task_failure", record)
    agent = _LimitAgent()

    result = _finalize(
        agent,
        final_response=None,
        exit_reason="unknown",
        pending_verification_response="composed report",
    )

    assert result["turn_exit_reason"] == "max_iterations_reached(60/60)"
    record.assert_called_once_with(
        conn,
        "task-123",
        error=(
            "Iteration budget exhausted (60/60) — task could not complete "
            "within the allowed iterations"
        ),
        outcome="timed_out",
        release_claim=True,
        end_run=True,
        event_payload_extra={"budget_used": 60, "budget_max": 60},
    )
