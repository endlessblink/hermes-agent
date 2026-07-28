"""Pure conversational state transitions for the Personal Assistant."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping
from typing import Any


_TERMINAL_PHASES = {"completed", "canceled", "recoverable-failure"}
_ACTIVE_PHASES = {
    "submitting",
    "restoring",
    "awaiting-context",
    "planning",
    "awaiting-approval",
}
_SUBMITTABLE_PHASES = {
    "idle",
    "completed",
    "canceled",
    "recoverable-failure",
}


def _require_phase(
    state: Mapping[str, Any],
    event_type: str,
    allowed: set[str],
) -> None:
    phase = str(state.get("phase") or "idle")
    if phase not in allowed:
        raise ValueError(f"{phase} cannot handle Personal Assistant event {event_type}")


def _initial_turn_state(value: Mapping[str, Any]) -> dict[str, Any]:
    state = copy.deepcopy(dict(value))
    phase = str(state.get("phase") or "idle")
    active_submission_id = str(state.get("submissionId") or "")
    state["phase"] = phase
    state.setdefault("acceptedSubmissionCount", 1 if active_submission_id else 0)
    state.setdefault(
        "acceptedSubmissionIds",
        [active_submission_id] if active_submission_id else [],
    )
    state.setdefault("acceptedActionCount", 0)
    state.setdefault("transcriptUserMessageCount", 1 if active_submission_id else 0)
    state.setdefault("visibleOutcomeCount", 1 if phase in _TERMINAL_PHASES else 0)
    state.setdefault("visibleOutcomeKind", None)
    state.setdefault("recommendationCount", 0)
    state.setdefault("rejectedRuntimeIds", [])
    state.setdefault("rawErrorVisible", False)
    return state


def _publish_outcome(
    state: dict[str, Any],
    *,
    phase: str,
    kind: str,
    recommendation_count: int = 0,
    content: Mapping[str, Any] | None = None,
) -> None:
    outcome = {"kind": kind}
    if isinstance(state.get("activeCardRevision"), int):
        outcome["cardRevision"] = state["activeCardRevision"]
    if content:
        safe_content = copy.deepcopy(dict(content))
        safe_content.pop("kind", None)
        safe_content.pop("cardRevision", None)
        encoded = json.dumps(safe_content, ensure_ascii=False, sort_keys=True)
        if len(encoded.encode("utf-8")) > 131_072:
            raise ValueError("visible outcome is too large")
        forbidden_keys = {"rawMessage", "stack", "traceback"}
        if forbidden_keys.intersection(safe_content):
            raise ValueError("visible outcome contains raw internal fields")
        outcome.update(safe_content)
    state["phase"] = phase
    state["visibleOutcomeCount"] = 1
    state["visibleOutcomeKind"] = kind
    state["visibleOutcome"] = outcome
    state["recommendationCount"] = recommendation_count


def _advance_card_revision(state: dict[str, Any]) -> None:
    current = state.get("activeCardRevision")
    state["activeCardRevision"] = (
        current + 1
        if isinstance(current, int) and not isinstance(current, bool)
        else 1
    )


def _identity_payload(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: state.get(key)
        for key in (
            "durableSessionId",
            "lineageRootId",
            "submissionId",
            "userIntent",
        )
        if state.get(key) is not None
    }


def _phase_for_visible_outcome(state: Mapping[str, Any]) -> str:
    return {
        "approval": "awaiting-approval",
        "plan": "completed",
        "progress-question": "awaiting-context",
        "recovery": "recoverable-failure",
    }.get(str(state.get("visibleOutcomeKind") or ""), "planning")


def apply_turn_event(
    current: Mapping[str, Any],
    event: Mapping[str, Any],
) -> dict[str, Any]:
    state = _initial_turn_state(current)
    event_type = str(event.get("type") or "")

    if event_type == "submit":
        submission_id = str(event.get("submissionId") or "")
        if not submission_id:
            raise ValueError("submit requires submissionId")
        accepted = set(state["acceptedSubmissionIds"])
        if submission_id in accepted:
            return state
        _require_phase(state, event_type, _SUBMITTABLE_PHASES)
        accepted.add(submission_id)
        state["acceptedSubmissionIds"] = sorted(accepted)
        state["acceptedSubmissionCount"] += 1
        state["transcriptUserMessageCount"] += 1
        for identity_key in ("durableSessionId", "lineageRootId"):
            identity_value = event.get(identity_key)
            if identity_value is not None:
                if not isinstance(identity_value, str) or not identity_value.strip():
                    raise ValueError(f"submit requires a valid {identity_key}")
                state[identity_key] = identity_value
        user_intent = event.get("userIntent")
        if user_intent is not None:
            if not isinstance(user_intent, str) or not user_intent.strip():
                raise ValueError("submit requires a valid userIntent")
            if len(user_intent) > 4_000:
                raise ValueError("submit userIntent is too long")
            state["userIntent"] = user_intent
        state["submissionId"] = submission_id
        state["phase"] = "submitting"
        state["visibleOutcomeCount"] = 0
        state["visibleOutcomeKind"] = None
        state["visibleOutcome"] = None
        state["recommendationCount"] = 0
        return state

    if event_type == "runtime-rejected":
        _require_phase(
            state,
            event_type,
            {"submitting", "planning", "awaiting-approval", "completed"},
        )
        runtime_session_id = str(event.get("runtimeSessionId") or "")
        if not runtime_session_id:
            raise ValueError("runtime-rejected requires runtimeSessionId")
        rejected = set(state["rejectedRuntimeIds"])
        rejected.add(runtime_session_id)
        state["rejectedRuntimeIds"] = sorted(rejected)
        if state.get("runtimeSessionId") == runtime_session_id:
            state["runtimeSessionId"] = None
        state["phase"] = "restoring"
        return state

    if event_type == "runtime-recovered":
        _require_phase(state, event_type, {"restoring"})
        runtime_session_id = str(event.get("runtimeSessionId") or "")
        if not runtime_session_id:
            raise ValueError("runtime-recovered requires runtimeSessionId")
        if runtime_session_id in state["rejectedRuntimeIds"]:
            raise ValueError("a rejected runtime cannot be recovered")
        state["runtimeSessionId"] = runtime_session_id
        state["phase"] = _phase_for_visible_outcome(state)
        return state

    if event_type == "context-evaluated":
        _require_phase(state, event_type, {"submitting"})
        if event.get("stale") is True:
            _advance_card_revision(state)
            _publish_outcome(
                state,
                phase="awaiting-context",
                kind="progress-question",
                content={"questionId": "progressReview"},
            )
        else:
            state["phase"] = "planning"
            state["visibleOutcomeCount"] = 0
            state["visibleOutcomeKind"] = None
            state["visibleOutcome"] = None
        return state

    if event_type == "context-recorded":
        pending_action = state.get("pendingAction")
        if (
            state["phase"] != "submitting"
            or not isinstance(pending_action, Mapping)
            or pending_action.get("kind") != "progress-question"
        ):
            raise ValueError("context-recorded requires a submitted progress answer")
        updates = event.get("updates")
        if not isinstance(updates, Mapping) or not updates:
            raise ValueError("context-recorded requires non-empty updates")
        confirmed_context = state.get("confirmedContext")
        if not isinstance(confirmed_context, Mapping):
            confirmed_context = {}
        state["confirmedContext"] = {
            **copy.deepcopy(dict(confirmed_context)),
            **copy.deepcopy(dict(updates)),
        }
        state["pendingAction"] = None
        state["phase"] = "planning"
        state["visibleOutcomeCount"] = 0
        state["visibleOutcomeKind"] = None
        state["visibleOutcome"] = None
        return state

    if event_type == "plan-ready":
        _require_phase(state, event_type, {"planning"})
        outcome = event.get("outcome")
        if not isinstance(outcome, Mapping) or not outcome:
            raise ValueError("plan-ready requires a non-empty outcome")
        _advance_card_revision(state)
        _publish_outcome(
            state,
            phase="completed",
            kind="plan",
            recommendation_count=int(event.get("recommendationCount") or 0),
            content=outcome,
        )
        return state

    if event_type == "approval-required":
        _require_phase(state, event_type, {"planning"})
        _advance_card_revision(state)
        outcome = event.get("outcome")
        _publish_outcome(
            state,
            phase="awaiting-approval",
            kind="approval",
            content=outcome if isinstance(outcome, Mapping) else None,
        )
        return state

    if event_type == "card-action":
        _require_phase(
            state,
            event_type,
            {"awaiting-context", "completed", "awaiting-approval"},
        )
        if event.get("cardRevision") != state.get("activeCardRevision"):
            return state
        action_input = event.get("input", {})
        if not isinstance(action_input, Mapping):
            raise ValueError("card-action input must be an object")
        state["pendingAction"] = {
            "kind": state.get("visibleOutcomeKind"),
            "actionId": event.get("actionId"),
            "input": copy.deepcopy(dict(action_input)),
        }
        state["acceptedActionCount"] += 1
        state["phase"] = "submitting"
        state["visibleOutcomeCount"] = 0
        state["visibleOutcomeKind"] = None
        state["visibleOutcome"] = None
        return state

    if event_type == "turn-failed":
        _require_phase(state, event_type, _ACTIVE_PHASES)
        _publish_outcome(
            state,
            phase="recoverable-failure",
            kind="recovery",
            content={"code": str(event.get("code") or "unknown")},
        )
        state["rawErrorVisible"] = False
        return state

    if event_type == "cancel":
        if state["phase"] in _ACTIVE_PHASES:
            _publish_outcome(state, phase="canceled", kind="canceled")
        return state

    if event_type == "turn-completed":
        _require_phase(state, event_type, _ACTIVE_PHASES | {"completed"})
        submission_id = str(event.get("submissionId") or "")
        if submission_id and submission_id != state.get("submissionId"):
            return state
        if state["phase"] != "completed":
            outcome = event.get("outcome")
            _publish_outcome(
                state,
                phase="completed",
                kind="result",
                content=outcome if isinstance(outcome, Mapping) else None,
            )
        return state

    raise ValueError(f"unsupported Personal Assistant turn event: {event_type}")


def decide_turn_effects(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    event: Mapping[str, Any],
) -> list[dict[str, Any]]:
    event_type = str(event.get("type") or "")
    if (
        event_type == "card-action"
        and event.get("cardRevision") != current.get("activeCardRevision")
    ):
        return [
            {
                "kind": "refresh-current-outcome",
                "payload": {
                    "activeCardRevision": current.get("activeCardRevision"),
                    "visibleOutcomeKind": current.get("visibleOutcomeKind"),
                    "outcome": copy.deepcopy(current.get("visibleOutcome")),
                },
            }
        ]
    if dict(previous) == dict(current):
        return []

    if event_type == "submit":
        kind = "evaluate-context"
        payload = _identity_payload(current)
    elif event_type == "runtime-rejected":
        kind = "recover-runtime"
        payload = {
            **_identity_payload(current),
            "rejectedRuntimeIds": list(current.get("rejectedRuntimeIds") or []),
        }
    elif event_type == "runtime-recovered":
        publish_kinds = {
            "approval": "publish-approval",
            "plan": "publish-plan",
            "progress-question": "publish-progress-question",
            "recovery": "publish-recovery",
        }
        kind = publish_kinds.get(str(current.get("visibleOutcomeKind") or ""))
        if kind is None:
            return []
        payload = {"outcome": copy.deepcopy(current.get("visibleOutcome"))}
    elif event_type == "context-evaluated":
        if current.get("phase") == "awaiting-context":
            kind = "publish-progress-question"
            payload = {"outcome": copy.deepcopy(current.get("visibleOutcome"))}
        else:
            kind = "reconcile-sources"
            payload = _identity_payload(current)
    elif event_type == "context-recorded":
        kind = "reconcile-sources"
        payload = _identity_payload(current)
    elif event_type == "plan-ready":
        kind = "publish-plan"
        payload = {"outcome": copy.deepcopy(current.get("visibleOutcome"))}
    elif event_type == "approval-required":
        kind = "publish-approval"
        payload = {"outcome": copy.deepcopy(current.get("visibleOutcome"))}
    elif event_type == "card-action":
        kind = "dispatch-card-action"
        payload = {
            **_identity_payload(current),
            "actionId": event.get("actionId"),
            "cardRevision": event.get("cardRevision"),
            "input": copy.deepcopy(dict(event.get("input") or {})),
        }
    elif event_type == "turn-failed":
        kind = "publish-recovery"
        payload = {"outcome": copy.deepcopy(current.get("visibleOutcome"))}
    elif event_type == "cancel":
        kind = "publish-cancellation"
        payload = {"outcome": copy.deepcopy(current.get("visibleOutcome"))}
    elif event_type == "turn-completed":
        kind = "publish-result"
        payload = {"outcome": copy.deepcopy(current.get("visibleOutcome"))}
    else:
        return []
    return [{"kind": kind, "payload": payload}]


def replay_turn_events(
    initial_state: Mapping[str, Any],
    events: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    state = _initial_turn_state(initial_state)
    for event in events:
        state = apply_turn_event(state, event)
    state.pop("acceptedSubmissionIds", None)
    state["finalPhase"] = state["phase"]
    return state
