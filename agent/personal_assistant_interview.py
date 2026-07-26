"""Shared deterministic controller for Personal Assistant interview responses."""

from __future__ import annotations

from typing import Any

from agent.personal_assistant_state import (
    INTERVIEW_REQUIRED_PROFILE_FIELDS,
    InterviewRevisionConflict,
    PersonalAssistantStateStore,
    interview_question_order,
)


QUESTION_ORDER = INTERVIEW_REQUIRED_PROFILE_FIELDS
_MULTI_VALUE_QUESTIONS = {"dependencies", "risks", "constraints"}


def _required_text(value: Any, label: str, *, limit: int = 300) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{label} must be a non-empty trimmed string")
    if len(value) > limit:
        raise ValueError(f"{label} is too long")
    return value


def _selected_values(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("selectedValues must be a list of at most 32 values")
    return [_required_text(item, "selected value", limit=2_000) for item in value]


class PlanningInterviewController:
    """Translate client-neutral UI responses into one atomic store transaction."""

    def __init__(self, store: PersonalAssistantStateStore):
        self.store = store

    def respond(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("planning interview response must be an object")
        interview_id = _required_text(payload.get("interviewId"), "interviewId")
        request_id = _required_text(payload.get("requestId"), "requestId")
        expected_revision = payload.get("expectedRevision")
        if not isinstance(expected_revision, int) or isinstance(
            expected_revision, bool
        ):
            raise ValueError("expectedRevision must be an integer")
        response = payload.get("response")
        if not isinstance(response, dict):
            raise ValueError("response must be an object")
        action = str(response.get("action") or "answer").strip()
        task_id = _required_text(payload.get("taskId"), "taskId")
        question_id = _required_text(payload.get("questionId"), "questionId")

        operations: list[dict[str, Any]] = []
        current = self.store.get_planning_interview()
        if current is None or current.get("interviewId") != interview_id:
            raise ValueError("planning interview is not active")
        question_order = interview_question_order(current)
        if question_id not in question_order:
            raise ValueError(f"unsupported planning interview question: {question_id}")
        current_revision = int(current.get("interviewRevision") or 0)
        if expected_revision != current_revision:
            raise InterviewRevisionConflict(interview_id, current_revision, current)
        cursor = current.get("cursor") or {}
        if (
            action in {"answer", "back", "confirm", "defer"}
            and cursor.get("taskId") != task_id
        ):
            raise ValueError("response does not target the current task")
        if action in {"answer", "back"} and cursor.get("questionId") != question_id:
            raise ValueError("response does not target the current question")

        field_edits = response.get("fieldEdits") or {}
        if not isinstance(field_edits, dict):
            raise ValueError("fieldEdits must be an object")
        if field_edits:
            operations.append({
                "op": "patch-task",
                "taskId": task_id,
                "fieldEdits": field_edits,
            })
        if action == "confirm":
            operations.append({"op": "confirm-task", "taskId": task_id})
        elif action in {"pause", "resume", "cancel"}:
            if field_edits:
                raise ValueError(f"{action} cannot include fieldEdits")
            operations.append({"op": action})
        elif action == "defer":
            custom = _required_text(
                response.get("customAnswer"), "customAnswer", limit=2_000
            )
            operations.append({"op": "defer-task", "taskId": task_id, "reason": custom})
        elif action == "back":
            if field_edits:
                raise ValueError("back cannot include fieldEdits")
            index = question_order.index(question_id)
            previous = question_order[index - 1] if index > 0 else question_order[0]
            operations.append({
                "op": "set-cursor",
                "taskId": task_id,
                "questionId": previous,
            })
        elif action != "answer":
            raise ValueError(
                f"unsupported planning interview response action: {action}"
            )

        if action != "answer":
            return self.store.patch_planning_interview(
                interview_id=interview_id,
                expected_revision=expected_revision,
                request_id=request_id,
                operations=operations,
            )

        selected = _selected_values(response.get("selectedValues"))
        custom = response.get("customAnswer")
        if custom is not None:
            custom = _required_text(custom, "customAnswer", limit=2_000)
        if not selected and custom is None:
            raise ValueError("an answer requires selectedValues or customAnswer")
        if question_id in _MULTI_VALUE_QUESTIONS:
            answer: Any = list(
                dict.fromkeys([*selected, *([custom] if custom else [])])
            )
        else:
            if len(selected) > 1:
                raise ValueError(f"{question_id} accepts only one selected value")
            answer = custom if custom is not None else selected[0]

        answer_operation = {
            "op": "patch-task",
            "taskId": task_id,
            "fieldEdits": {question_id: answer, **field_edits},
        }
        if field_edits:
            operations[0] = answer_operation
        else:
            operations.append(answer_operation)
        index = question_order.index(question_id)
        next_question = (
            question_order[index + 1] if index + 1 < len(question_order) else None
        )
        if next_question is None:
            operations.append({"op": "confirm-task", "taskId": task_id})
            if current.get("mode") == "daily-grounding":
                operations.append({"op": "approve-readiness"})
        else:
            operations.append({
                "op": "set-cursor",
                "taskId": task_id,
                "questionId": next_question,
            })
        return self.store.patch_planning_interview(
            interview_id=interview_id,
            expected_revision=expected_revision,
            request_id=request_id,
            operations=operations,
        )
