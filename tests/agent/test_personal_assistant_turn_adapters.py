from __future__ import annotations

from unittest.mock import Mock

import pytest

from agent.personal_assistant_turn_adapters import PersonalAssistantEffectRouter


@pytest.fixture
def ports():
    result = {
        "session": Mock(),
        "context": Mock(),
        "sources": Mock(),
        "renderer": Mock(),
        "actions": Mock(),
    }
    result["renderer"].publish.return_value = None
    return result


@pytest.fixture
def router(ports):
    return PersonalAssistantEffectRouter(**ports)


def effect(kind, payload=None):
    return {
        "effectId": f"effect:{kind}",
        "kind": kind,
        "payload": payload or {},
    }


def test_session_recovery_returns_a_new_non_rejected_runtime(router, ports) -> None:
    ports["session"].recover_runtime.return_value = "runtime-live"

    event = router.execute(
        effect(
            "recover-runtime",
            {
                "durableSessionId": "assistant-home",
                "rejectedRuntimeIds": ["runtime-dead"],
            },
        )
    )

    assert event == {
        "type": "runtime-recovered",
        "runtimeSessionId": "runtime-live",
    }
    ports["session"].recover_runtime.assert_called_once_with(
        effect_id="effect:recover-runtime",
        durable_session_id="assistant-home",
        rejected_runtime_ids=("runtime-dead",),
    )


def test_session_recovery_fails_closed_if_the_port_returns_a_rejected_runtime(
    router,
    ports,
) -> None:
    ports["session"].recover_runtime.return_value = "runtime-dead"

    event = router.execute(
        effect(
            "recover-runtime",
            {
                "durableSessionId": "assistant-home",
                "rejectedRuntimeIds": ["runtime-dead"],
            },
        )
    )

    assert event == {
        "type": "turn-failed",
        "code": "session-recovery-returned-rejected-runtime",
    }
    assert "runtime-dead" not in str(event)


def test_context_and_source_ports_return_only_normalized_events(router, ports) -> None:
    ports["context"].is_stale.return_value = True
    ports["sources"].reconcile.return_value = {
        "type": "plan-ready",
        "recommendationCount": 1,
        "outcome": {"options": [{"taskName": "משימה לדוגמה"}]},
    }

    context_event = router.execute(
        effect(
            "evaluate-context",
            {
                "durableSessionId": "assistant-home",
                "submissionId": "turn-1",
                "userIntent": "תכנן את המשך היום",
            },
        )
    )
    plan_event = router.execute(
        effect(
            "reconcile-sources",
                {
                    "durableSessionId": "assistant-home",
                    "submissionId": "turn-1",
                    "userIntent": "תכנן את המשך היום",
                },
            )
        )

    assert context_event == {"type": "context-evaluated", "stale": True}
    assert plan_event["type"] == "plan-ready"
    assert plan_event["outcome"]["options"][0]["taskName"] == "משימה לדוגמה"
    ports["sources"].reconcile.assert_called_once_with(
        effect_id="effect:reconcile-sources",
        durable_session_id="assistant-home",
        submission_id="turn-1",
        user_intent="תכנן את המשך היום",
    )


def test_renderer_receives_the_exact_persisted_outcome_and_effect_identity(
    router,
    ports,
) -> None:
    outcome = {
        "kind": "progress-question",
        "cardRevision": 4,
        "questionId": "progressReview",
    }

    assert router.execute(effect("publish-progress-question", {"outcome": outcome})) is None
    ports["renderer"].publish.assert_called_once_with(
        effect_id="effect:publish-progress-question",
        outcome=outcome,
    )


def test_renderer_can_request_runtime_recovery_without_exposing_raw_errors(
    router,
    ports,
) -> None:
    ports["renderer"].publish.return_value = {
        "type": "runtime-rejected",
        "runtimeSessionId": "runtime-dead",
        "rawMessage": "Prompt failed: session not found",
    }

    event = router.execute(
        effect(
            "publish-plan",
            {
                "outcome": {
                    "kind": "plan",
                    "cardRevision": 4,
                    "options": [{"taskName": "משימה לדוגמה"}],
                }
            },
        )
    )

    assert event == {
        "type": "runtime-rejected",
        "runtimeSessionId": "runtime-dead",
    }
    assert "session not found" not in str(event)


def test_progress_answer_is_dispatched_and_normalized(router, ports) -> None:
    ports["actions"].dispatch.return_value = {
        "type": "context-recorded",
        "updates": {"progressReview": "שום דבר"},
    }

    event = router.execute(
        effect(
            "dispatch-card-action",
            {
                "actionId": "answer-progress",
                "cardRevision": 4,
                "input": {"text": "שום דבר"},
            },
        )
    )

    assert event == {
        "type": "context-recorded",
        "updates": {"progressReview": "שום דבר"},
    }
    ports["actions"].dispatch.assert_called_once_with(
        effect_id="effect:dispatch-card-action",
        action_id="answer-progress",
        card_revision=4,
        input={"text": "שום דבר"},
    )


def test_dispatch_ports_cannot_return_an_unrecognized_event(router, ports) -> None:
    ports["sources"].reconcile.return_value = {
        "type": "invented-source-result",
        "rawMessage": "session not found",
    }

    with pytest.raises(ValueError, match="unsupported source adapter event"):
        router.execute(
            effect(
                "reconcile-sources",
                {
                    "durableSessionId": "assistant-home",
                    "submissionId": "turn-1",
                    "userIntent": "תכנן את המשך היום",
                },
            )
        )


def test_unknown_effect_kind_is_rejected_without_falling_through(router) -> None:
    with pytest.raises(ValueError, match="unsupported Personal Assistant effect"):
        router.execute(effect("invented-effect"))
