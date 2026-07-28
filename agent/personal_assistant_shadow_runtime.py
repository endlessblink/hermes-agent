"""Generated-profile runtime boundary for Personal Assistant shadow turns."""

from __future__ import annotations

from collections.abc import Mapping
from threading import Lock
from typing import Any

from agent.personal_assistant_shadow_worker import (
    PersonalAssistantShadowWorkerLifecycle,
)
from agent.personal_assistant_state import (
    PersonalAssistantStateStore,
    TurnRevisionConflict,
)


class PersonalAssistantShadowRuntime:
    def __init__(
        self,
        *,
        store: PersonalAssistantStateStore,
        lifecycle: PersonalAssistantShadowWorkerLifecycle,
    ) -> None:
        self._store = store
        self._lifecycle = lifecycle
        self._lock = Lock()

    def start(self) -> bool:
        return self._lifecycle.start()

    def submit(
        self,
        *,
        event_id: str,
        durable_session_id: str,
        submission_id: str,
        user_intent: str,
        lineage_root_id: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "type": "submit",
            "durableSessionId": durable_session_id,
            "submissionId": submission_id,
            "userIntent": user_intent,
        }
        if lineage_root_id:
            event["lineageRootId"] = lineage_root_id
        return self._apply(event_id=event_id, event=event)

    def submit_card_action(
        self,
        *,
        event_id: str,
        action_id: str,
        card_revision: int,
        input: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._apply(
            event_id=event_id,
            event={
                "type": "card-action",
                "actionId": action_id,
                "cardRevision": card_revision,
                "input": dict(input),
            },
        )

    def wait_until_idle(self, *, timeout: float) -> bool:
        return self._lifecycle.wait_until_idle(timeout=timeout)

    def close(self, *, timeout: float) -> None:
        with self._lock:
            self._lifecycle.stop(timeout=timeout)

    def _apply(self, *, event_id: str, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if not self._lifecycle.active and not self._lifecycle.start():
                raise RuntimeError("Personal Assistant shadow runtime is disabled")
            for attempt in range(3):
                current = self._store.get_active_turn() or {}
                try:
                    result = self._store.apply_turn_event(
                        expected_revision=int(current.get("turnRevision") or 0),
                        event_id=event_id,
                        event=event,
                    )
                    break
                except TurnRevisionConflict:
                    if attempt == 2:
                        raise
            self._lifecycle.wake()
            return result


def build_personal_assistant_shadow_runtime(
    *,
    store: PersonalAssistantStateStore,
    lifecycle: PersonalAssistantShadowWorkerLifecycle,
) -> PersonalAssistantShadowRuntime:
    return PersonalAssistantShadowRuntime(store=store, lifecycle=lifecycle)
