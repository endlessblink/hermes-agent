"""Outbox-driven adapter runner for the Personal Assistant turn core."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from agent.personal_assistant_state import PersonalAssistantStateStore


class PersonalAssistantTurnAdapter(Protocol):
    def execute(self, effect: Mapping[str, Any]) -> Mapping[str, Any] | None: ...


def drain_one_turn_effect(
    store: PersonalAssistantStateStore,
    adapter: PersonalAssistantTurnAdapter,
    *,
    worker_id: str,
    now: datetime | None = None,
    lease_seconds: int = 30,
) -> dict[str, Any] | None:
    claimed = store.claim_next_turn_effect(
        worker_id=worker_id,
        now=now,
        lease_seconds=lease_seconds,
    )
    if claimed is None:
        return None

    effect = claimed["effect"]
    result_event = adapter.execute(effect)
    if result_event is None:
        completed = store.complete_turn_effect(
            effect_id=effect["effectId"],
            worker_id=worker_id,
            now=now,
        )
        applied = None
    else:
        completed = store.complete_turn_effect_with_result(
            effect_id=effect["effectId"],
            worker_id=worker_id,
            result_event=dict(result_event),
            now=now,
        )
        applied = {
            "turn": completed["turn"],
            "stateVersion": completed["stateVersion"],
            "duplicate": completed["duplicate"],
            "receipt": completed["receipt"],
            "effects": completed["effects"],
        }
    return {
        "effect": effect,
        "completed": completed,
        "applied": applied,
    }
