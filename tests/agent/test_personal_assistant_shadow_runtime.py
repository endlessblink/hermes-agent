from __future__ import annotations

import pytest

from agent.personal_assistant_shadow_runtime import PersonalAssistantShadowRuntime
from agent.personal_assistant_shadow_worker import (
    PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE,
    build_acceptance_shadow_worker_lifecycle,
)
from agent.personal_assistant_state import PersonalAssistantStateStore
from agent.personal_assistant_state import TurnRevisionConflict


def build_runtime(tmp_path, *, profile=PERSONAL_ASSISTANT_ACCEPTANCE_PROFILE):
    store = PersonalAssistantStateStore(tmp_path)
    store.get_planning_interview = lambda: {"readinessApproved": True}
    lifecycle = build_acceptance_shadow_worker_lifecycle(
        store=store,
        active_profile=profile,
        resolve_agent=lambda _session_id: object(),
        recover_runtime=lambda _session_id, _rejected: "runtime-1",
        planning_response_builder=lambda *_args: "validated plan",
        extract_recommendations=lambda _response: [
            {"taskId": "task-1", "title": "משימה לדוגמה"}
        ],
        needs_progress_check=lambda _interview, *, now: False,
        poll_seconds=0.01,
    )
    return store, PersonalAssistantShadowRuntime(store=store, lifecycle=lifecycle)


def test_runtime_submits_and_drains_one_generated_turn(tmp_path) -> None:
    store, runtime = build_runtime(tmp_path)

    result = runtime.submit(
        event_id="submit:plan-today",
        durable_session_id="assistant-home",
        submission_id="plan-today",
        user_intent="תכנן את המשך היום",
    )

    assert result["duplicate"] is False
    assert runtime.wait_until_idle(timeout=1) is True
    runtime.close(timeout=1)
    assert store.get_active_turn()["phase"] == "completed"


def test_runtime_replays_duplicate_submission_without_a_second_turn(tmp_path) -> None:
    store, runtime = build_runtime(tmp_path)
    request = {
        "event_id": "submit:plan-today",
        "durable_session_id": "assistant-home",
        "submission_id": "plan-today",
        "user_intent": "תכנן את המשך היום",
    }

    first = runtime.submit(**request)
    duplicate = runtime.submit(**request)

    assert duplicate["duplicate"] is True
    assert duplicate["receipt"] == first["receipt"]
    assert store.get_active_turn()["acceptedSubmissionCount"] == 1
    runtime.close(timeout=1)


def test_runtime_refuses_office_work_without_writing_state(tmp_path) -> None:
    store, runtime = build_runtime(tmp_path, profile="office-work")

    with pytest.raises(RuntimeError, match="disabled"):
        runtime.submit(
            event_id="submit:real-profile",
            durable_session_id="assistant-home",
            submission_id="real-profile",
            user_intent="תכנן את היום",
        )

    assert store.get_active_turn() is None


def test_runtime_retries_when_worker_advances_turn_revision(tmp_path) -> None:
    store, runtime = build_runtime(tmp_path)
    original_apply = store.apply_turn_event
    calls = 0

    def racing_apply(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TurnRevisionConflict(
                1,
                {"turnRevision": 1, "phase": "idle"},
            )
        return original_apply(**kwargs)

    store.apply_turn_event = racing_apply

    runtime.submit(
        event_id="submit:plan-today",
        durable_session_id="assistant-home",
        submission_id="plan-today",
        user_intent="תכנן את המשך היום",
    )

    assert calls == 2
    runtime.close(timeout=1)
