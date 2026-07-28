"""Narrow effect adapters for the Personal Assistant turn core."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class SessionPort(Protocol):
    def recover_runtime(
        self,
        *,
        effect_id: str,
        durable_session_id: str,
        rejected_runtime_ids: tuple[str, ...],
    ) -> str | None: ...


class ContextPort(Protocol):
    def is_stale(
        self,
        *,
        effect_id: str,
        durable_session_id: str,
        submission_id: str,
    ) -> bool: ...


class SourcePort(Protocol):
    def reconcile(
        self,
        *,
        effect_id: str,
        durable_session_id: str,
        submission_id: str,
        user_intent: str,
    ) -> Mapping[str, Any]: ...


class RendererPort(Protocol):
    def publish(
        self,
        *,
        effect_id: str,
        outcome: Mapping[str, Any],
    ) -> Mapping[str, Any] | None: ...


class ActionPort(Protocol):
    def dispatch(
        self,
        *,
        effect_id: str,
        action_id: str,
        card_revision: int,
        input: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


_PUBLISH_EFFECTS = {
    "publish-progress-question",
    "publish-plan",
    "publish-approval",
    "publish-recovery",
    "publish-cancellation",
    "publish-result",
    "refresh-current-outcome",
}


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Personal Assistant effect requires {key}")
    return value


def _normalized_adapter_event(
    value: Mapping[str, Any],
    *,
    allowed: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} adapter must return an event")
    event_type = str(value.get("type") or "")
    if event_type not in allowed:
        raise ValueError(f"unsupported {label} adapter event: {event_type}")
    if event_type == "turn-failed":
        return {
            "type": "turn-failed",
            "code": str(value.get("code") or f"{label}-failed"),
        }
    if event_type == "runtime-rejected":
        return {
            "type": "runtime-rejected",
            "runtimeSessionId": _required_text(value, "runtimeSessionId"),
        }
    if event_type == "turn-completed":
        result = {
            "type": "turn-completed",
            "submissionId": str(value.get("submissionId") or ""),
        }
        if isinstance(value.get("outcome"), Mapping):
            result["outcome"] = copy.deepcopy(dict(value["outcome"]))
        return result
    return copy.deepcopy(dict(value))


@dataclass(frozen=True)
class PersonalAssistantEffectRouter:
    session: SessionPort
    context: ContextPort
    sources: SourcePort
    renderer: RendererPort
    actions: ActionPort

    def execute(self, effect: Mapping[str, Any]) -> Mapping[str, Any] | None:
        effect_id = _required_text(effect, "effectId")
        kind = _required_text(effect, "kind")
        raw_payload = effect.get("payload")
        payload = raw_payload if isinstance(raw_payload, Mapping) else {}

        if kind == "recover-runtime":
            rejected = tuple(
                value
                for value in payload.get("rejectedRuntimeIds") or ()
                if isinstance(value, str) and value
            )
            runtime_session_id = self.session.recover_runtime(
                effect_id=effect_id,
                durable_session_id=_required_text(payload, "durableSessionId"),
                rejected_runtime_ids=rejected,
            )
            if not runtime_session_id:
                return {"type": "turn-failed", "code": "session-unavailable"}
            if runtime_session_id in rejected:
                return {
                    "type": "turn-failed",
                    "code": "session-recovery-returned-rejected-runtime",
                }
            return {
                "type": "runtime-recovered",
                "runtimeSessionId": runtime_session_id,
            }

        if kind == "evaluate-context":
            stale = self.context.is_stale(
                effect_id=effect_id,
                durable_session_id=_required_text(payload, "durableSessionId"),
                submission_id=_required_text(payload, "submissionId"),
            )
            if not isinstance(stale, bool):
                raise ValueError("context adapter must return a boolean")
            return {"type": "context-evaluated", "stale": stale}

        if kind == "reconcile-sources":
            result = self.sources.reconcile(
                effect_id=effect_id,
                durable_session_id=_required_text(payload, "durableSessionId"),
                submission_id=_required_text(payload, "submissionId"),
                user_intent=_required_text(payload, "userIntent"),
            )
            return _normalized_adapter_event(
                result,
                allowed={"plan-ready", "approval-required", "turn-failed"},
                label="source",
            )

        if kind == "dispatch-card-action":
            action_input = payload.get("input")
            if not isinstance(action_input, Mapping):
                raise ValueError("action effect requires input")
            result = self.actions.dispatch(
                effect_id=effect_id,
                action_id=_required_text(payload, "actionId"),
                card_revision=int(payload.get("cardRevision") or 0),
                input=copy.deepcopy(dict(action_input)),
            )
            return _normalized_adapter_event(
                result,
                allowed={
                    "context-recorded",
                    "runtime-rejected",
                    "turn-completed",
                    "turn-failed",
                },
                label="action",
            )

        if kind in _PUBLISH_EFFECTS:
            outcome = payload.get("outcome")
            if not isinstance(outcome, Mapping):
                raise ValueError("renderer effect requires a persisted outcome")
            result = self.renderer.publish(
                effect_id=effect_id,
                outcome=copy.deepcopy(dict(outcome)),
            )
            if result is not None:
                return _normalized_adapter_event(
                    result,
                    allowed={"runtime-rejected", "turn-failed"},
                    label="renderer",
                )
            return None

        raise ValueError(f"unsupported Personal Assistant effect: {kind}")
