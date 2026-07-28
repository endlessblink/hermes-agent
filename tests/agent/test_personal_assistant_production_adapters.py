from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from agent.personal_assistant_production_adapters import (
    DurablePlanningContextPort,
    PersistedOutcomeRendererPort,
    ProgressAnswerActionPort,
    ResolvingValidatedLegacySourcePort,
    build_shadow_effect_router,
    ValidatedLegacySourcePort,
    build_validated_legacy_source_port,
)


def test_context_port_reads_the_durable_interview_once() -> None:
    store = Mock()
    interview = {
        "interviewId": "planning-2026-07-27",
        "readinessApproved": True,
    }
    store.get_planning_interview.return_value = interview
    needs_progress_check = Mock(return_value=True)
    now = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)
    port = DurablePlanningContextPort(
        store=store,
        now=lambda: now,
        needs_progress_check=needs_progress_check,
    )

    assert (
        port.is_stale(
            effect_id="effect:context",
            durable_session_id="assistant-home",
            submission_id="turn-1",
        )
        is True
    )
    store.get_planning_interview.assert_called_once_with()
    needs_progress_check.assert_called_once_with(interview, now=now)


def test_context_port_fails_safe_when_durable_context_is_missing() -> None:
    store = Mock()
    store.get_planning_interview.return_value = None
    port = DurablePlanningContextPort(store=store)

    assert (
        port.is_stale(
            effect_id="effect:context",
            durable_session_id="assistant-home",
            submission_id="turn-1",
        )
        is True
    )


def test_source_port_normalizes_only_validated_legacy_planning_output() -> None:
    store = Mock()
    interview = {
        "interviewId": "planning-2026-07-27",
        "readinessApproved": True,
    }
    store.get_planning_interview.return_value = interview
    planner = Mock(return_value='```hermes-ui\n{"type":"day-timeline"}\n```')
    recommendations = Mock(
        return_value=[{"taskId": "task-1", "title": "משימה לדוגמה"}]
    )
    port = ValidatedLegacySourcePort(
        store=store,
        build_validated_plan=planner,
        extract_recommendations=recommendations,
    )

    event = port.reconcile(
        effect_id="effect:source",
        durable_session_id="assistant-home",
        submission_id="turn-1",
        user_intent="תכנן את המשך היום",
    )

    assert event == {
        "type": "plan-ready",
        "recommendationCount": 1,
        "outcome": {
            "content": '```hermes-ui\n{"type":"day-timeline"}\n```',
            "recommendations": [
                {"taskId": "task-1", "title": "משימה לדוגמה"}
            ],
        },
    }
    planner.assert_called_once_with(
        user_intent="תכנן את המשך היום",
        interview=interview,
    )


def test_source_port_fails_closed_without_ready_validated_context() -> None:
    store = Mock()
    store.get_planning_interview.return_value = {"readinessApproved": False}
    planner = Mock()
    port = ValidatedLegacySourcePort(
        store=store,
        build_validated_plan=planner,
    )

    event = port.reconcile(
        effect_id="effect:source",
        durable_session_id="assistant-home",
        submission_id="turn-1",
        user_intent="תכנן את המשך היום",
    )

    assert event == {
        "type": "turn-failed",
        "code": "planning-context-not-ready",
    }
    planner.assert_not_called()


def test_source_factory_wraps_the_existing_validated_planning_boundary() -> None:
    agent = object()
    store = Mock()
    interview = {
        "interviewId": "planning-2026-07-27",
        "readinessApproved": True,
    }
    store.get_planning_interview.return_value = interview
    planning_response_builder = Mock(return_value="validated plan")
    port = build_validated_legacy_source_port(
        agent=agent,
        store=store,
        planning_response_builder=planning_response_builder,
        extract_recommendations=lambda _response: [],
    )

    event = port.reconcile(
        effect_id="effect:source",
        durable_session_id="assistant-home",
        submission_id="turn-1",
        user_intent="תכנן את המשך היום",
    )

    assert event["type"] == "plan-ready"
    planning_response_builder.assert_called_once_with(
        agent,
        None,
        "תכנן את המשך היום",
        interview,
    )


def test_resolving_source_port_uses_the_current_runtime_agent() -> None:
    store = Mock()
    store.get_planning_interview.return_value = {"readinessApproved": True}
    agent = object()
    resolve_agent = Mock(return_value=agent)
    planning_response_builder = Mock(return_value="validated plan")
    port = ResolvingValidatedLegacySourcePort(
        store=store,
        resolve_agent=resolve_agent,
        planning_response_builder=planning_response_builder,
        extract_recommendations=lambda _response: [],
    )

    event = port.reconcile(
        effect_id="effect:source",
        durable_session_id="assistant-home",
        submission_id="turn-1",
        user_intent="תכנן את המשך היום",
    )

    assert event["type"] == "plan-ready"
    resolve_agent.assert_called_once_with("assistant-home")
    planning_response_builder.assert_called_once_with(
        agent,
        None,
        "תכנן את המשך היום",
        {"readinessApproved": True},
    )


def test_resolving_source_port_fails_closed_without_runtime_agent() -> None:
    port = ResolvingValidatedLegacySourcePort(
        store=Mock(),
        resolve_agent=lambda _session_id: None,
    )

    assert port.reconcile(
        effect_id="effect:source",
        durable_session_id="assistant-home",
        submission_id="turn-1",
        user_intent="תכנן את המשך היום",
    ) == {
        "type": "turn-failed",
        "code": "runtime-session-unavailable",
    }


def test_progress_action_port_normalizes_a_bounded_answer() -> None:
    port = ProgressAnswerActionPort()

    assert port.dispatch(
        effect_id="effect:action",
        action_id="answer-progress",
        card_revision=2,
        input={"progressReview": "סיימתי את המשימה לדוגמה"},
    ) == {
        "type": "context-recorded",
        "updates": {"progressReview": "סיימתי את המשימה לדוגמה"},
    }


def test_progress_action_port_rejects_unknown_or_empty_actions() -> None:
    port = ProgressAnswerActionPort()

    with pytest.raises(ValueError, match="unsupported"):
        port.dispatch(
            effect_id="effect:action",
            action_id="start-task",
            card_revision=2,
            input={"progressReview": "משהו"},
        )
    with pytest.raises(ValueError, match="non-empty"):
        port.dispatch(
            effect_id="effect:action",
            action_id="answer-progress",
            card_revision=2,
            input={"progressReview": " "},
        )


def test_persisted_renderer_acknowledges_without_a_second_write() -> None:
    port = PersistedOutcomeRendererPort()

    assert port.publish(
        effect_id="effect:publish",
        outcome={"kind": "progress-question", "cardRevision": 2},
    ) is None


def test_shadow_router_binds_runtime_and_persisted_state_authorities() -> None:
    store = Mock()
    store.get_planning_interview.return_value = {"readinessApproved": True}
    agent = object()
    planning_response_builder = Mock(return_value="validated plan")
    router = build_shadow_effect_router(
        store=store,
        resolve_agent=lambda _session_id: agent,
        recover_runtime=lambda durable_session_id, rejected_runtime_ids: (
            f"runtime:{durable_session_id}"
            if not rejected_runtime_ids
            else "runtime:replacement"
        ),
        planning_response_builder=planning_response_builder,
        extract_recommendations=lambda _response: [],
        needs_progress_check=lambda _interview, *, now: False,
    )

    assert router.execute(
        {
            "effectId": "effect:context",
            "kind": "evaluate-context",
            "payload": {
                "durableSessionId": "assistant-home",
                "submissionId": "turn-1",
            },
        }
    ) == {"type": "context-evaluated", "stale": False}
    assert router.execute(
        {
            "effectId": "effect:source",
            "kind": "reconcile-sources",
            "payload": {
                "durableSessionId": "assistant-home",
                "submissionId": "turn-1",
                "userIntent": "תכנן את המשך היום",
            },
        }
    )["type"] == "plan-ready"
