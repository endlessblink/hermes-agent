from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from agent.personal_assistant_state import (
    PersonalAssistantStateStore,
    TurnRevisionConflict,
)
from agent.personal_assistant_turn_state import (
    apply_turn_event,
    decide_turn_effects,
    replay_turn_events,
)


def test_duplicate_submission_is_accepted_and_rendered_once() -> None:
    state = replay_turn_events(
        {"phase": "idle", "durableSessionId": "assistant-home"},
        [
            {"type": "submit", "submissionId": "stable-turn"},
            {"type": "submit", "submissionId": "stable-turn"},
            {"type": "turn-completed", "submissionId": "stable-turn"},
            {"type": "turn-completed", "submissionId": "stable-turn"},
        ],
    )

    assert state["acceptedSubmissionCount"] == 1
    assert state["transcriptUserMessageCount"] == 1
    assert state["visibleOutcomeCount"] == 1
    assert state["finalPhase"] == "completed"


def test_submission_intent_is_persisted_and_forwarded_to_reconciliation() -> None:
    submitted = apply_turn_event(
        {"phase": "idle", "durableSessionId": "assistant-home"},
        {
            "type": "submit",
            "submissionId": "turn-1",
            "userIntent": "תכנן את המשך היום",
        },
    )
    planning = apply_turn_event(
        submitted,
        {"type": "context-evaluated", "stale": False},
    )

    assert submitted["userIntent"] == "תכנן את המשך היום"
    assert decide_turn_effects(
        submitted,
        planning,
        {"type": "context-evaluated", "stale": False},
    ) == [
        {
            "kind": "reconcile-sources",
            "payload": {
                "durableSessionId": "assistant-home",
                "submissionId": "turn-1",
                "userIntent": "תכנן את המשך היום",
            },
        }
    ]


def test_public_state_exposes_only_the_safe_current_turn_outcome(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    submitted = store.apply_turn_event(
        expected_revision=0,
        event_id="submit",
        event={
            "type": "submit",
            "durableSessionId": "assistant-home",
            "submissionId": "plan-today",
            "userIntent": "תכנן את המשך היום",
        },
    )
    store.apply_turn_event(
        expected_revision=submitted["turn"]["turnRevision"],
        event_id="context-evaluated",
        event={"type": "context-evaluated", "stale": True},
    )

    active_turn = store.public()["activeTurn"]

    assert active_turn == {
        "submissionId": "plan-today",
        "phase": "awaiting-context",
        "revision": 2,
        "cardRevision": 1,
        "outcome": {
            "kind": "progress-question",
            "cardRevision": 1,
            "questionId": "progressReview",
        },
    }
    assert "pendingEffects" not in active_turn
    assert "eventReceipts" not in active_turn
    assert "runtimeSessionId" not in active_turn


def test_rejected_runtime_cannot_reenter_the_recovery_ladder() -> None:
    restoring = apply_turn_event(
        {
            "phase": "submitting",
            "runtimeSessionId": "runtime-dead",
            "submissionId": "stable-turn",
        },
        {"type": "runtime-rejected", "runtimeSessionId": "runtime-dead"},
    )

    assert restoring["phase"] == "restoring"
    assert restoring["runtimeSessionId"] is None
    with pytest.raises(ValueError, match="rejected runtime"):
        apply_turn_event(
            restoring,
            {"type": "runtime-recovered", "runtimeSessionId": "runtime-dead"},
        )


def test_stale_context_produces_one_question_and_no_recommendations() -> None:
    state = replay_turn_events(
        {"phase": "completed"},
        [
            {"type": "submit", "submissionId": "plan-today"},
            {"type": "context-evaluated", "stale": True},
        ],
    )

    assert state["finalPhase"] == "awaiting-context"
    assert state["visibleOutcomeKind"] == "progress-question"
    assert state["visibleOutcomeCount"] == 1
    assert state["recommendationCount"] == 0
    assert state["visibleOutcome"] == {
        "kind": "progress-question",
        "cardRevision": 1,
        "questionId": "progressReview",
    }


def test_current_context_enters_planning_without_publishing_a_question() -> None:
    state = replay_turn_events(
        {"phase": "idle"},
        [
            {"type": "submit", "submissionId": "plan-today"},
            {"type": "context-evaluated", "stale": False},
        ],
    )

    assert state["finalPhase"] == "planning"
    assert state["visibleOutcomeCount"] == 0


def test_only_the_active_card_revision_can_submit_an_action() -> None:
    current = {
        "phase": "completed",
        "activeCardRevision": 8,
        "visibleOutcomeCount": 1,
    }

    stale = apply_turn_event(
        current,
        {"type": "card-action", "cardRevision": 7, "actionId": "choose"},
    )
    accepted = apply_turn_event(
        current,
        {"type": "card-action", "cardRevision": 8, "actionId": "choose"},
    )

    assert stale["acceptedActionCount"] == 0
    assert stale["phase"] == "completed"
    assert accepted["acceptedActionCount"] == 1
    assert accepted["phase"] == "submitting"


def test_progress_answer_is_persisted_before_planning_resumes() -> None:
    awaiting = {
        "phase": "awaiting-context",
        "durableSessionId": "assistant-home",
        "submissionId": "turn-1",
        "activeCardRevision": 3,
        "visibleOutcome": {
            "kind": "progress-question",
            "cardRevision": 3,
            "questionId": "progressReview",
        },
        "visibleOutcomeKind": "progress-question",
        "visibleOutcomeCount": 1,
    }
    action = {
        "type": "card-action",
        "cardRevision": 3,
        "actionId": "answer-progress",
        "input": {"text": "שום דבר"},
    }
    submitting = apply_turn_event(awaiting, action)

    assert submitting["phase"] == "submitting"
    assert submitting["pendingAction"] == {
        "kind": "progress-question",
        "actionId": "answer-progress",
        "input": {"text": "שום דבר"},
    }
    assert decide_turn_effects(awaiting, submitting, action) == [
        {
            "kind": "dispatch-card-action",
            "payload": {
                "durableSessionId": "assistant-home",
                "submissionId": "turn-1",
                "actionId": "answer-progress",
                "cardRevision": 3,
                "input": {"text": "שום דבר"},
            },
        }
    ]

    recorded = apply_turn_event(
        submitting,
        {
            "type": "context-recorded",
            "updates": {"progressReview": "שום דבר"},
        },
    )

    assert recorded["phase"] == "planning"
    assert recorded["confirmedContext"]["progressReview"] == "שום דבר"
    assert recorded.get("pendingAction") is None


def test_the_core_assigns_monotonic_card_revisions() -> None:
    first = apply_turn_event(
        {"phase": "planning", "activeCardRevision": 8},
        {
            "type": "plan-ready",
            "recommendationCount": 1,
            "outcome": {
                "title": "תכנון היום",
                "options": [{"taskName": "משימה לדוגמה"}],
            },
        },
    )
    awaiting_approval = apply_turn_event(
        {"phase": "planning", "activeCardRevision": first["activeCardRevision"]},
        {"type": "approval-required"},
    )

    assert first["activeCardRevision"] == 9
    assert awaiting_approval["activeCardRevision"] == 10
    assert first["visibleOutcome"] == {
        "kind": "plan",
        "cardRevision": 9,
        "title": "תכנון היום",
        "options": [{"taskName": "משימה לדוגמה"}],
    }


def test_adapter_failure_becomes_one_safe_recovery_outcome() -> None:
    failed = apply_turn_event(
        {"phase": "planning", "visibleOutcomeCount": 0},
        {
            "type": "turn-failed",
            "code": "session-not-found",
            "rawMessage": "Prompt failed: session not found",
        },
    )

    assert failed["phase"] == "recoverable-failure"
    assert failed["visibleOutcomeKind"] == "recovery"
    assert failed["visibleOutcomeCount"] == 1
    assert failed["rawErrorVisible"] is False
    assert failed["visibleOutcome"] == {
        "kind": "recovery",
        "code": "session-not-found",
    }
    assert "Prompt failed" not in str(failed["visibleOutcome"])


def test_publish_effect_contains_the_exact_persisted_safe_outcome() -> None:
    previous = {"phase": "planning", "activeCardRevision": 4}
    event = {
        "type": "plan-ready",
        "recommendationCount": 1,
        "outcome": {
            "title": "תכנון היום",
            "options": [{"taskName": "משימה לדוגמה"}],
        },
    }
    current = apply_turn_event(previous, event)

    assert decide_turn_effects(previous, current, event) == [
        {
            "kind": "publish-plan",
            "payload": {"outcome": current["visibleOutcome"]},
        }
    ]


def test_adapter_effects_carry_stable_session_identity() -> None:
    initial = {
        "phase": "idle",
        "durableSessionId": "assistant-home",
        "lineageRootId": "assistant-lineage",
    }
    submit = {"type": "submit", "submissionId": "turn-1"}
    submitted = apply_turn_event(initial, submit)

    assert decide_turn_effects(initial, submitted, submit) == [
        {
            "kind": "evaluate-context",
            "payload": {
                "durableSessionId": "assistant-home",
                "lineageRootId": "assistant-lineage",
                "submissionId": "turn-1",
            },
        }
    ]

    context_event = {"type": "context-evaluated", "stale": False}
    evaluated = apply_turn_event(submitted, context_event)
    assert decide_turn_effects(submitted, evaluated, context_event) == [
        {
            "kind": "reconcile-sources",
            "payload": {
                "durableSessionId": "assistant-home",
                "lineageRootId": "assistant-lineage",
                "submissionId": "turn-1",
            },
        }
    ]


@pytest.mark.parametrize(
    ("state", "event", "expected_kind"),
    [
        (
            {"phase": "idle"},
            {"type": "submit", "submissionId": "turn-1"},
            "evaluate-context",
        ),
        (
            {"phase": "submitting", "runtimeSessionId": "dead"},
            {"type": "runtime-rejected", "runtimeSessionId": "dead"},
            "recover-runtime",
        ),
        (
            {"phase": "submitting"},
            {"type": "context-evaluated", "stale": True},
            "publish-progress-question",
        ),
        (
            {"phase": "submitting"},
            {"type": "context-evaluated", "stale": False},
            "reconcile-sources",
        ),
        (
            {"phase": "planning", "activeCardRevision": 2},
            {
                "type": "plan-ready",
                "recommendationCount": 1,
                "outcome": {"options": [{"taskName": "משימה לדוגמה"}]},
            },
            "publish-plan",
        ),
        (
            {"phase": "planning"},
            {"type": "turn-failed", "rawMessage": "session not found"},
            "publish-recovery",
        ),
    ],
)
def test_turn_events_request_one_typed_effect(state, event, expected_kind) -> None:
    next_state = apply_turn_event(state, event)
    effects = decide_turn_effects(state, next_state, event)

    assert effects == [{"kind": expected_kind, "payload": effects[0]["payload"]}]
    assert "rawMessage" not in effects[0]["payload"]
    assert "session not found" not in str(effects[0])


def test_stale_card_action_requests_refresh_without_dispatching() -> None:
    current = {
        "phase": "completed",
        "activeCardRevision": 8,
        "visibleOutcomeKind": "plan",
        "visibleOutcomeCount": 1,
        "visibleOutcome": {
            "kind": "plan",
            "cardRevision": 8,
            "options": [{"taskName": "משימה קיימת"}],
        },
    }
    event = {"type": "card-action", "cardRevision": 7, "actionId": "choose"}
    next_state = apply_turn_event(current, event)

    assert decide_turn_effects(current, next_state, event) == [
            {
                "kind": "refresh-current-outcome",
                "payload": {
                    "activeCardRevision": 8,
                    "visibleOutcomeKind": "plan",
                    "outcome": current["visibleOutcome"],
                },
            }
        ]


def test_cancel_is_terminal_and_idempotent() -> None:
    canceled = apply_turn_event(
        {"phase": "planning", "visibleOutcomeCount": 0},
        {"type": "cancel"},
    )

    assert canceled["phase"] == "canceled"
    assert canceled["visibleOutcomeCount"] == 1
    assert apply_turn_event(canceled, {"type": "cancel"}) == canceled


@pytest.mark.parametrize(
    ("state", "event"),
    [
        (
            {"phase": "planning", "submissionId": "active"},
            {"type": "submit", "submissionId": "different"},
        ),
        (
            {"phase": "idle"},
            {"type": "runtime-rejected", "runtimeSessionId": "runtime-1"},
        ),
        (
            {"phase": "completed"},
            {"type": "context-evaluated", "stale": True},
        ),
        (
            {"phase": "submitting"},
            {"type": "plan-ready"},
        ),
        (
            {"phase": "planning"},
            {"type": "card-action", "cardRevision": 1, "actionId": "choose"},
        ),
        (
            {"phase": "idle"},
            {"type": "turn-completed", "submissionId": "missing"},
        ),
    ],
)
def test_impossible_transitions_are_rejected(state, event) -> None:
    with pytest.raises(ValueError, match="cannot handle"):
        apply_turn_event(state, event)


def test_turn_state_events_are_durable_and_idempotent(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    first = store.apply_turn_event(
        expected_revision=0,
        event_id="event-submit",
        event={"type": "submit", "submissionId": "stable-turn"},
    )

    reopened = PersonalAssistantStateStore(tmp_path)
    assert reopened.get_active_turn()["phase"] == "submitting"
    assert first["turn"]["turnRevision"] == 1
    assert first["duplicate"] is False
    assert first["effects"][0]["kind"] == "evaluate-context"
    assert reopened.get_active_turn()["pendingEffects"] == first["effects"]

    duplicate = reopened.apply_turn_event(
        expected_revision=0,
        event_id="event-submit",
        event={"type": "submit", "submissionId": "stable-turn"},
    )

    assert duplicate["duplicate"] is True
    assert duplicate["turn"] == first["turn"]
    assert duplicate["stateVersion"] == first["stateVersion"]
    assert duplicate["effects"] == first["effects"]


def test_turn_event_identity_collision_is_rejected(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    store.apply_turn_event(
        expected_revision=0,
        event_id="event-submit",
        event={"type": "submit", "submissionId": "stable-turn"},
    )

    with pytest.raises(ValueError, match="different payload"):
        store.apply_turn_event(
            expected_revision=1,
            event_id="event-submit",
            event={"type": "submit", "submissionId": "different-turn"},
        )


def test_turn_state_compare_and_swap_rejects_stale_writers(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    store.apply_turn_event(
        expected_revision=0,
        event_id="event-submit",
        event={"type": "submit", "submissionId": "stable-turn"},
    )

    with pytest.raises(TurnRevisionConflict) as error:
        store.apply_turn_event(
            expected_revision=0,
            event_id="event-context",
            event={"type": "context-evaluated", "stale": True},
        )

    assert error.value.current_revision == 1
    assert error.value.turn["phase"] == "submitting"


def test_concurrent_turn_writers_cannot_accept_two_submissions(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)

    def submit(index: int):
        try:
            return store.apply_turn_event(
                expected_revision=0,
                event_id=f"event-{index}",
                event={"type": "submit", "submissionId": f"turn-{index}"},
            )
        except TurnRevisionConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))

    assert sum(isinstance(result, TurnRevisionConflict) for result in results) == 1
    active = store.get_active_turn()
    assert active["turnRevision"] == 1
    assert active["acceptedSubmissionCount"] == 1
    assert active["transcriptUserMessageCount"] == 1


def test_effect_claim_survives_restart_and_expired_lease_is_recovered(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    applied = store.apply_turn_event(
        expected_revision=0,
        event_id="event-submit",
        event={"type": "submit", "submissionId": "stable-turn"},
    )
    effect_id = applied["effects"][0]["effectId"]
    now = datetime(2026, 7, 26, 18, 0, tzinfo=timezone.utc)

    first = store.claim_next_turn_effect(
        worker_id="worker-1",
        now=now,
        lease_seconds=30,
    )
    assert first["effect"]["effectId"] == effect_id
    assert first["effect"]["status"] == "processing"
    assert first["effect"]["attemptCount"] == 1
    assert store.claim_next_turn_effect(
        worker_id="worker-2",
        now=now + timedelta(seconds=29),
        lease_seconds=30,
    ) is None

    reopened = PersonalAssistantStateStore(tmp_path)
    reclaimed = reopened.claim_next_turn_effect(
        worker_id="worker-2",
        now=now + timedelta(seconds=31),
        lease_seconds=30,
    )

    assert reclaimed["effect"]["effectId"] == effect_id
    assert reclaimed["effect"]["workerId"] == "worker-2"
    assert reclaimed["effect"]["attemptCount"] == 2


def test_only_the_current_effect_lease_owner_can_complete_delivery(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    applied = store.apply_turn_event(
        expected_revision=0,
        event_id="event-submit",
        event={"type": "submit", "submissionId": "stable-turn"},
    )
    effect_id = applied["effects"][0]["effectId"]
    now = datetime(2026, 7, 26, 18, 0, tzinfo=timezone.utc)
    store.claim_next_turn_effect(
        worker_id="worker-1",
        now=now,
        lease_seconds=1,
    )
    store.claim_next_turn_effect(
        worker_id="worker-2",
        now=now + timedelta(seconds=2),
        lease_seconds=30,
    )

    with pytest.raises(ValueError, match="lease owner"):
        store.complete_turn_effect(
            effect_id=effect_id,
            worker_id="worker-1",
            now=now + timedelta(seconds=3),
        )

    completed = store.complete_turn_effect(
        effect_id=effect_id,
        worker_id="worker-2",
        now=now + timedelta(seconds=3),
    )
    duplicate = store.complete_turn_effect(
        effect_id=effect_id,
        worker_id="worker-2",
        now=now + timedelta(seconds=4),
    )

    assert completed["effect"]["status"] == "delivered"
    assert completed["duplicate"] is False
    assert duplicate["effect"] == completed["effect"]
    assert duplicate["stateVersion"] == completed["stateVersion"]
    assert duplicate["duplicate"] is True


def test_concurrent_workers_cannot_claim_the_same_effect(tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    store.apply_turn_event(
        expected_revision=0,
        event_id="event-submit",
        event={"type": "submit", "submissionId": "stable-turn"},
    )
    now = datetime(2026, 7, 26, 18, 0, tzinfo=timezone.utc)

    def claim(index: int):
        return store.claim_next_turn_effect(
            worker_id=f"worker-{index}",
            now=now,
            lease_seconds=30,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))

    assert sum(result is not None for result in results) == 1
