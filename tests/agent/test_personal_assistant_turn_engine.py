from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from agent.personal_assistant_state import PersonalAssistantStateStore
from agent.personal_assistant_turn_adapters import PersonalAssistantEffectRouter
from agent.personal_assistant_turn_engine import drain_one_turn_effect


class RecordingAdapter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.effects = []

    def execute(self, effect):
        self.effects.append(effect)
        return self.responses.pop(0) if self.responses else None


def test_engine_records_context_before_publishing_one_question(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    store.apply_turn_event(
        expected_revision=0,
        event_id="submit",
        event={
            "type": "submit",
            "durableSessionId": "assistant-home",
            "lineageRootId": "assistant-home",
            "submissionId": "plan-today",
            "userIntent": "תכנן את המשך היום",
        },
    )
    adapter = RecordingAdapter(
        [
            {"type": "context-evaluated", "stale": True},
            None,
        ]
    )
    now = datetime(2026, 7, 26, 18, 0, tzinfo=timezone.utc)

    first = drain_one_turn_effect(
        store,
        adapter,
        worker_id="worker",
        now=now,
    )
    second = drain_one_turn_effect(
        store,
        adapter,
        worker_id="worker",
        now=now,
    )

    assert first["effect"]["kind"] == "evaluate-context"
    assert second["effect"]["kind"] == "publish-progress-question"
    assert [effect["kind"] for effect in adapter.effects] == [
        "evaluate-context",
        "publish-progress-question",
    ]
    active = store.get_active_turn()
    assert active["phase"] == "awaiting-context"
    assert active["visibleOutcomeCount"] == 1
    assert active["recommendationCount"] == 0
    assert drain_one_turn_effect(
        store,
        adapter,
        worker_id="worker",
        now=now,
    ) is None


def test_shadow_journey_asks_once_then_reconciles_and_publishes_a_plan(
    tmp_path,
) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    session = Mock()
    context = Mock()
    sources = Mock()
    renderer = Mock()
    renderer.publish.return_value = None
    actions = Mock()
    context.is_stale.return_value = True
    actions.dispatch.return_value = {
        "type": "context-recorded",
        "updates": {"progressReview": "שום דבר"},
    }
    sources.reconcile.return_value = {
        "type": "plan-ready",
        "recommendationCount": 1,
        "outcome": {
            "options": [
                {
                    "taskName": "להעביר 200$ לאלכס",
                    "reason": "עדיפות גבוהה להיום",
                }
            ]
        },
    }
    router = PersonalAssistantEffectRouter(
        session=session,
        context=context,
        sources=sources,
        renderer=renderer,
        actions=actions,
    )
    now = datetime(2026, 7, 26, 18, 0, tzinfo=timezone.utc)

    store.apply_turn_event(
        expected_revision=0,
        event_id="submit",
        event={
            "type": "submit",
            "durableSessionId": "assistant-home",
            "lineageRootId": "assistant-home",
            "submissionId": "plan-today",
            "userIntent": "תכנן את המשך היום",
        },
    )
    while drain_one_turn_effect(
        store,
        router,
        worker_id="worker",
        now=now,
    ):
        pass

    question = store.get_active_turn()
    store.apply_turn_event(
        expected_revision=question["turnRevision"],
        event_id="answer-progress",
        event={
            "type": "card-action",
            "cardRevision": question["activeCardRevision"],
            "actionId": "answer-progress",
            "input": {"text": "שום דבר"},
        },
    )
    while drain_one_turn_effect(
        store,
        router,
        worker_id="worker",
        now=now,
    ):
        pass

    active = store.get_active_turn()
    published = [
        call.kwargs["outcome"] for call in renderer.publish.call_args_list
    ]
    assert [outcome["kind"] for outcome in published] == [
        "progress-question",
        "plan",
    ]
    assert published[1]["options"][0]["taskName"] == "להעביר 200$ לאלכס"
    assert active["phase"] == "completed"
    assert active["confirmedContext"]["progressReview"] == "שום דבר"
    assert active["visibleOutcomeCount"] == 1
    assert active["recommendationCount"] == 1
    assert "session not found" not in str(published).lower()
    context.is_stale.assert_called_once()
    actions.dispatch.assert_called_once()
    sources.reconcile.assert_called_once()


def test_engine_recovers_runtime_and_republishes_without_resubmitting(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    submitted = store.apply_turn_event(
        expected_revision=0,
        event_id="submit",
        event={
            "type": "submit",
            "durableSessionId": "assistant-home",
            "submissionId": "stable-turn",
            "userIntent": "תכנן את המשך היום",
        },
    )
    evaluated = store.apply_turn_event(
        expected_revision=submitted["turn"]["turnRevision"],
        event_id="context-evaluated",
        event={"type": "context-evaluated", "stale": False},
    )
    store.apply_turn_event(
        expected_revision=evaluated["turn"]["turnRevision"],
        event_id="plan-ready",
        event={
            "type": "plan-ready",
            "recommendationCount": 1,
            "outcome": {"options": [{"taskName": "משימה לדוגמה"}]},
        },
    )
    adapter = RecordingAdapter(
        [
            {"type": "runtime-rejected", "runtimeSessionId": "runtime-dead"},
            {"type": "runtime-recovered", "runtimeSessionId": "runtime-live"},
            None,
        ]
    )
    now = datetime(2026, 7, 26, 18, 0, tzinfo=timezone.utc)

    while drain_one_turn_effect(
        store,
        adapter,
        worker_id="worker",
        now=now,
    ):
        pass

    active = store.get_active_turn()
    assert [effect["kind"] for effect in adapter.effects] == [
        "publish-plan",
        "recover-runtime",
        "publish-plan",
    ]
    assert active["phase"] == "completed"
    assert active["runtimeSessionId"] == "runtime-live"
    assert active["acceptedSubmissionCount"] == 1
    assert active["visibleOutcomeCount"] == 1
    assert active["visibleOutcome"]["options"][0]["taskName"] == "משימה לדוגמה"
    assert "runtime-dead" in active["rejectedRuntimeIds"]


def test_effect_result_replay_does_not_append_state_or_effects(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    store.apply_turn_event(
        expected_revision=0,
        event_id="submit",
        event={"type": "submit", "submissionId": "plan-today"},
    )
    adapter = RecordingAdapter([{"type": "context-evaluated", "stale": True}])
    now = datetime(2026, 7, 26, 18, 0, tzinfo=timezone.utc)

    delivered = drain_one_turn_effect(
        store,
        adapter,
        worker_id="worker",
        now=now,
    )
    before = store.read()
    replayed = store.apply_turn_event(
        expected_revision=0,
        event_id=f"{delivered['effect']['effectId']}:result",
        event={"type": "context-evaluated", "stale": True},
    )

    assert replayed["duplicate"] is True
    assert store.read() == before


def test_invalid_effect_result_does_not_mark_delivery_complete(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    applied = store.apply_turn_event(
        expected_revision=0,
        event_id="submit",
        event={"type": "submit", "submissionId": "plan-today"},
    )
    effect_id = applied["effects"][0]["effectId"]
    adapter = RecordingAdapter([{"type": "unsupported-result"}])
    now = datetime(2026, 7, 26, 18, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="unsupported Personal Assistant"):
        drain_one_turn_effect(
            store,
            adapter,
            worker_id="worker",
            now=now,
        )

    effect = next(
        effect
        for effect in store.get_active_turn()["pendingEffects"]
        if effect["effectId"] == effect_id
    )
    assert effect["status"] == "processing"
    assert effect.get("deliveredAt") is None
