"""Concrete shadow adapters over existing Personal Assistant authorities."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol


class PlanningStatePort(Protocol):
    def get_planning_interview(self) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class PersistedOutcomeRendererPort:
    """Acknowledge outcomes already committed to the public durable state."""

    def publish(
        self,
        *,
        effect_id: str,
        outcome: Mapping[str, Any],
    ) -> None:
        del effect_id, outcome


@dataclass(frozen=True)
class ProgressAnswerActionPort:
    """Normalize the generated profile's bounded progress answer."""

    def dispatch(
        self,
        *,
        effect_id: str,
        action_id: str,
        card_revision: int,
        input: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del effect_id, card_revision
        if action_id != "answer-progress":
            raise ValueError(f"unsupported shadow action: {action_id}")
        progress_review = input.get("progressReview")
        if not isinstance(progress_review, str) or not progress_review.strip():
            raise ValueError("progress answer must be non-empty")
        if len(progress_review) > 4_000:
            raise ValueError("progress answer is too long")
        return {
            "type": "context-recorded",
            "updates": {"progressReview": progress_review.strip()},
        }


@dataclass(frozen=True)
class ResolvingSessionPort:
    recover: Callable[[str, tuple[str, ...]], str | None]

    def recover_runtime(
        self,
        *,
        effect_id: str,
        durable_session_id: str,
        rejected_runtime_ids: tuple[str, ...],
    ) -> str | None:
        del effect_id
        return self.recover(durable_session_id, rejected_runtime_ids)


def _default_needs_progress_check(
    interview: Mapping[str, Any],
    *,
    now: datetime,
) -> bool:
    from agent.conversation_loop import _planning_interview_needs_progress_check

    return _planning_interview_needs_progress_check(interview, now=now)


def _default_extract_recommendations(response: Any) -> list[dict[str, str]]:
    from agent.personal_assistant_output_gate import (
        extract_personal_assistant_recommendations,
    )

    return extract_personal_assistant_recommendations(response)


@dataclass(frozen=True)
class DurablePlanningContextPort:
    store: PlanningStatePort
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    needs_progress_check: Callable[..., bool] = _default_needs_progress_check

    def is_stale(
        self,
        *,
        effect_id: str,
        durable_session_id: str,
        submission_id: str,
    ) -> bool:
        del effect_id, durable_session_id, submission_id
        interview = self.store.get_planning_interview()
        if not isinstance(interview, Mapping):
            return True
        if interview.get("readinessApproved") is not True:
            return True
        return bool(self.needs_progress_check(interview, now=self.now()))


@dataclass(frozen=True)
class ValidatedLegacySourcePort:
    store: PlanningStatePort
    build_validated_plan: Callable[..., str | None]
    extract_recommendations: Callable[[Any], list[dict[str, str]]] = (
        _default_extract_recommendations
    )

    def reconcile(
        self,
        *,
        effect_id: str,
        durable_session_id: str,
        submission_id: str,
        user_intent: str,
    ) -> Mapping[str, Any]:
        del effect_id, durable_session_id, submission_id
        interview = self.store.get_planning_interview()
        if (
            not isinstance(interview, Mapping)
            or interview.get("readinessApproved") is not True
        ):
            return {
                "type": "turn-failed",
                "code": "planning-context-not-ready",
            }
        response = self.build_validated_plan(
            user_intent=user_intent,
            interview=interview,
        )
        if not isinstance(response, str) or not response.strip():
            return {
                "type": "turn-failed",
                "code": "validated-plan-unavailable",
            }
        recommendations = self.extract_recommendations(response)[:3]
        return {
            "type": "plan-ready",
            "recommendationCount": len(recommendations),
            "outcome": {
                "content": response,
                "recommendations": copy.deepcopy(recommendations),
            },
        }


@dataclass(frozen=True)
class ResolvingValidatedLegacySourcePort:
    """Resolve the current runtime agent only when source work is claimed."""

    store: PlanningStatePort
    resolve_agent: Callable[[str], Any | None]
    planning_response_builder: Callable[..., str | None] | None = None
    extract_recommendations: Callable[[Any], list[dict[str, str]]] = (
        _default_extract_recommendations
    )

    def reconcile(
        self,
        *,
        effect_id: str,
        durable_session_id: str,
        submission_id: str,
        user_intent: str,
    ) -> Mapping[str, Any]:
        agent = self.resolve_agent(durable_session_id)
        if agent is None:
            return {
                "type": "turn-failed",
                "code": "runtime-session-unavailable",
            }
        port = build_validated_legacy_source_port(
            agent=agent,
            store=self.store,
            planning_response_builder=self.planning_response_builder,
            extract_recommendations=self.extract_recommendations,
        )
        return port.reconcile(
            effect_id=effect_id,
            durable_session_id=durable_session_id,
            submission_id=submission_id,
            user_intent=user_intent,
        )


def build_shadow_effect_router(
    *,
    store: PlanningStatePort,
    resolve_agent: Callable[[str], Any | None],
    recover_runtime: Callable[[str, tuple[str, ...]], str | None],
    planning_response_builder: Callable[..., str | None] | None = None,
    extract_recommendations: Callable[[Any], list[dict[str, str]]] = (
        _default_extract_recommendations
    ),
    needs_progress_check: Callable[..., bool] = _default_needs_progress_check,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
):
    from agent.personal_assistant_turn_adapters import PersonalAssistantEffectRouter

    return PersonalAssistantEffectRouter(
        session=ResolvingSessionPort(recover_runtime),
        context=DurablePlanningContextPort(
            store=store,
            now=now,
            needs_progress_check=needs_progress_check,
        ),
        sources=ResolvingValidatedLegacySourcePort(
            store=store,
            resolve_agent=resolve_agent,
            planning_response_builder=planning_response_builder,
            extract_recommendations=extract_recommendations,
        ),
        renderer=PersistedOutcomeRendererPort(),
        actions=ProgressAnswerActionPort(),
    )


def build_validated_legacy_source_port(
    *,
    agent: Any,
    store: PlanningStatePort,
    planning_response_builder: Callable[..., str | None] | None = None,
    extract_recommendations: Callable[[Any], list[dict[str, str]]] = (
        _default_extract_recommendations
    ),
) -> ValidatedLegacySourcePort:
    if planning_response_builder is None:
        from agent.conversation_loop import (
            _build_initial_personal_assistant_planning_response,
        )

        planning_response_builder = (
            _build_initial_personal_assistant_planning_response
        )

    def build_validated_plan(
        *,
        user_intent: str,
        interview: Mapping[str, Any],
    ) -> str | None:
        return planning_response_builder(
            agent,
            None,
            user_intent,
            interview,
        )

    return ValidatedLegacySourcePort(
        store=store,
        build_validated_plan=build_validated_plan,
        extract_recommendations=extract_recommendations,
    )
