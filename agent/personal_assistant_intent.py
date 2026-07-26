"""Context-bound intent resolution for Personal Assistant workflow turns.

The resolver deliberately handles only actions that can be made deterministic
from authoritative workflow or UI state. Open-ended requests remain ordinary
model queries; they are not reclassified as planning merely because a workflow
is paused in the background.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import re
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


_ADVANCE_PATTERNS = (
    r"(?:ok(?:ay)?[,.]?\s*)?(?:so\s+)?what(?:'s| is)? next\??",
    r"(?:ok(?:ay)?[,.]?\s*)?(?:let'?s\s+)?(?:continue|move on|next)\.?",
    r"(?:אוקיי[,.]?\s*)?(?:אז\s+)?(?:מה\s+(?:הדבר\s+)?הבא|מה\s+עכשיו|מה\s+הלאה)\??",
    r"(?:בוא(?:ו)?\s+)?(?:נמשיך|נתקדם)\.?",
)
_COMMAND_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("workflow.advance", _ADVANCE_PATTERNS),
    ("workflow.pause", (r"pause(?: this)?\.?", r"עצור|השהה")),
    ("workflow.resume", (r"resume(?: this)?\.?", r"תמשיך|המשך")),
    ("workflow.back", (r"go back\.?", r"back\.?", r"חזור|אחורה")),
    ("workflow.skip", (r"skip(?: this)?\.?", r"דלג|תדלג")),
    ("workflow.edit", (r"change that\.?", r"edit that\.?", r"שנה את זה|ערוך את זה")),
    ("workflow.complete", (r"done\.?", r"finished\.?", r"סיימתי|בוצע")),
)
_APPROVAL_SHORTHAND = (
    r"(?:yes[,.]?\s*)?(?:go ahead|do it|apply it|send it)\.?",
    r"(?:כן[,.]?\s*)?(?:תעשה|תבצע|שלח|קדימה)\.?",
)
_START_TASK_TIMER_PATTERNS = (
    r"(?:please\s+)?(?:start|activate|run)(?:\s+(?:this|that|the))?\s+(?:task|timer|it)\.?",
    r"(?:תתחיל|התחל|תפעיל|הפעל)(?:\s+(?:את\s+)?(?:המשימה|הטיימר|אותה|אותו|זה))?\.?",
)
_STOP_TASK_TIMER_PATTERNS = (
    r"(?:please\s+)?(?:stop|end)(?:\s+(?:this|that|the|current))?\s+(?:task|timer)\.?",
    r"(?:עצור|תעצור|סיים|תסיים)(?:\s+(?:את\s+)?)?(?:המשימה|הטיימר)(?:\s+(?:הנוכחית|הנוכחי))?\.?",
)
_NAMED_START_TASK_RE = re.compile(
    r"^(?:תתחיל|התחל|תפעיל|הפעל)(?:\s+עכשיו)?\s+(?:את\s+)?(?:המשימה|הטיימר)\s+(.+?)[.!?]?$",
    flags=re.IGNORECASE,
)
_PLANNING_QUERY_PATTERNS = (
    r".*\b(?:plan|replan|schedule|reprioriti[sz]e)\b.*",
    r".*(?:תכנן|תכנון|לתכנן|שאר\s+היום|נשאר\s+היום|כדאי\s+לי\s+לעשות|"
    r"תזמן|לתזמן|תעדף\s+מחדש|סדר\s+עדיפויות).*$",
)
_NON_DAILY_PLANNING_HORIZON_RE = re.compile(
    r"(?:השבוע|שבוע(?:\s+הבא)?|החודש|חודש(?:\s+הבא)?|"
    r"\b(?:this|next)?\s*(?:week|month|quarter|year)\b)",
    flags=re.IGNORECASE,
)
_CONSEQUENTIAL = {
    "external-message",
    "purchase",
    "delete",
    "sensitive-disclosure",
    "major-schedule-change",
    "external-commitment",
}


def _normalized(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def _requested_planning_date(user_message: Any, *, today: datetime) -> str:
    """Resolve the date needed by the pre-tool grounding gate."""

    text = _normalized(user_message)
    explicit_date = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if explicit_date:
        return explicit_date.group(1)
    if "מחר" in text or re.search(r"\btomorrow\b", text):
        return (today.date() + timedelta(days=1)).isoformat()
    return today.date().isoformat()


def _supports_daily_grounding(user_message: Any) -> bool:
    """Return whether the deterministic one-day interview matches the request horizon."""

    return _NON_DAILY_PLANNING_HORIZON_RE.search(_normalized(user_message)) is None


def _matches(value: str, patterns: Iterable[str]) -> bool:
    return any(re.fullmatch(pattern, value, flags=re.IGNORECASE) for pattern in patterns)


@dataclass(frozen=True)
class TurnIntention:
    action: str
    workflow_id: str | None = None
    requires_clarification: bool = False
    requires_confirmation: bool = False
    candidates: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    preserve_workflow_ids: tuple[str, ...] = ()
    pending_action_id: str | None = None
    answer: dict[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


def _workflow_id(workflow: Mapping[str, Any]) -> str:
    return str(workflow.get("workflowId") or workflow.get("interviewId") or "").strip()


def resolve_turn_intention(
    user_message: Any,
    *,
    workflows: Iterable[Mapping[str, Any]] = (),
    ui_binding: Mapping[str, Any] | None = None,
    pending_actions: Iterable[Mapping[str, Any]] = (),
) -> TurnIntention:
    """Resolve deterministic Personal Assistant actions from current state."""

    original = re.sub(r"\s+", " ", str(user_message or "").strip())
    normalized = _normalized(user_message)
    workflow_list = [item for item in workflows if isinstance(item, Mapping)]
    active = [item for item in workflow_list if str(item.get("status") or "active") == "active"]
    active_ids = tuple(filter(None, (_workflow_id(item) for item in active)))

    binding = ui_binding if isinstance(ui_binding, Mapping) else {}
    selected_values = binding.get("selectedValues")
    bound_workflow_id = str(binding.get("workflowId") or "").strip()
    if original.startswith("Continue personal-assistant interview ") and " after committed " in original:
        return TurnIntention(
            action="planning.query",
            preserve_workflow_ids=active_ids,
            evidence=("committed-interview-continuation",),
        )
    if _matches(normalized, _STOP_TASK_TIMER_PATTERNS):
        return TurnIntention(
            action="task.timer.stop",
            preserve_workflow_ids=active_ids,
            evidence=("explicit-timer-stop",),
        )
    named_start = _NAMED_START_TASK_RE.fullmatch(original)
    if named_start:
        return TurnIntention(
            action="task.timer.start.lookup",
            preserve_workflow_ids=active_ids,
            evidence=("explicit-task-title",),
            metadata={"taskQuery": named_start.group(1).strip()},
        )
    if _matches(normalized, _START_TASK_TIMER_PATTERNS):
        bound_task_id = str(binding.get("taskId") or "").strip()
        current_task_ids = tuple(
            dict.fromkeys(
                str(item.get("currentTaskId") or "").strip()
                for item in active
                if str(item.get("currentTaskId") or "").strip()
            )
        )
        task_id = bound_task_id or (current_task_ids[0] if len(current_task_ids) == 1 else "")
        if task_id:
            return TurnIntention(
                action="task.timer.start",
                workflow_id=bound_workflow_id or (active_ids[0] if len(active_ids) == 1 else None),
                evidence=("current-ui-binding",) if bound_task_id else ("only-current-task",),
                metadata={"taskId": task_id},
            )
        return TurnIntention(
            action="task.timer.start.ambiguous",
            requires_clarification=True,
            candidates=current_task_ids,
            preserve_workflow_ids=active_ids,
            evidence=("no-unambiguous-task-reference",),
        )
    if _matches(normalized, _PLANNING_QUERY_PATTERNS):
        return TurnIntention(
            action="planning.query",
            preserve_workflow_ids=active_ids,
            evidence=("explicit-planning-request",),
        )
    if isinstance(selected_values, list) and selected_values and bound_workflow_id in active_ids:
        return TurnIntention(
            action="workflow.answer",
            workflow_id=bound_workflow_id,
            evidence=("active-workflow", "current-ui-binding"),
            answer={"selectedValues": list(selected_values)},
        )

    for pending in pending_actions:
        if not isinstance(pending, Mapping):
            continue
        consequence = str(pending.get("consequence") or "").strip()
        if (
            consequence in _CONSEQUENTIAL
            and not pending.get("approvedScope")
            and _matches(normalized, _APPROVAL_SHORTHAND)
        ):
            return TurnIntention(
                action="confirmation.required",
                requires_confirmation=True,
                pending_action_id=str(pending.get("id") or "") or None,
                preserve_workflow_ids=active_ids,
                evidence=("pending-consequential-action", "missing-approved-scope"),
            )

    command: str | None = None
    for action, patterns in _COMMAND_PATTERNS:
        if _matches(normalized, patterns):
            command = action
            break

    if command is not None:
        candidates = active
        if command == "workflow.resume" and not candidates:
            candidates = [
                item for item in workflow_list if str(item.get("status") or "") == "paused"
            ]
        candidate_ids = tuple(filter(None, (_workflow_id(item) for item in candidates)))
        if len(candidate_ids) > 1:
            return TurnIntention(
                action="workflow.ambiguous",
                requires_clarification=True,
                candidates=candidate_ids,
                preserve_workflow_ids=active_ids,
                evidence=("multiple-workflow-candidates",),
            )
        if len(candidate_ids) == 1:
            evidence = ["active-workflow"]
            if bound_workflow_id == candidate_ids[0]:
                evidence.append("current-ui-binding")
            return TurnIntention(
                action=command,
                workflow_id=candidate_ids[0],
                evidence=tuple(evidence),
            )

    return TurnIntention(
        action="general.query",
        preserve_workflow_ids=active_ids,
        evidence=("no-deterministic-workflow-command",),
    )


__all__ = ["TurnIntention", "resolve_turn_intention"]


def _bounded_items(value: Any, *, limit: int = 25) -> list[Any]:
    if not isinstance(value, list):
        return []
    return list(value[:limit])


def _bounded_intent_metadata(value: Mapping[str, Any]) -> dict[str, str]:
    bounded: dict[str, str] = {}
    for key in ("taskId", "taskQuery"):
        item = value.get(key)
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item:
            bounded[key] = item[:300]
    return bounded


def _public_assistant_state(store: Any) -> dict[str, Any]:
    try:
        state = store.public()
    except Exception:
        return {}
    if not isinstance(state, Mapping):
        return {}
    return {
        "version": state.get("version"),
        "outcomes": _bounded_items(state.get("outcomes")),
        "commitments": _bounded_items(state.get("commitments")),
        "capacity": state.get("capacity"),
        "focus": state.get("focus"),
        "blockers": _bounded_items(state.get("blockers")),
        "deferred": _bounded_items(state.get("deferred")),
        "preferences": _bounded_items(state.get("preferences")),
        "pendingApprovals": _bounded_items(state.get("pendingApprovals")),
        "protectedItems": _bounded_items(state.get("protectedItems"), limit=100),
        "latestCoverageReceipt": state.get("latestCoverageReceipt"),
    }


def _get_interview(store: Any) -> dict[str, Any] | None:
    try:
        interview = store.get_planning_interview()
    except Exception:
        return None
    return dict(interview) if isinstance(interview, Mapping) else None


def _workflow_projection(interview: Mapping[str, Any]) -> dict[str, Any]:
    workflow_id = str(
        interview.get("workflowId")
        or interview.get("interviewId")
        or interview.get("interview_id")
        or ""
    )
    cursor = interview.get("cursor")
    cursor = cursor if isinstance(cursor, Mapping) else {}
    return {
        "workflowId": workflow_id,
        "type": "planning-interview",
        "status": interview.get("status") or "active",
        "revision": interview.get("interviewRevision", interview.get("revision")),
        "currentTaskId": interview.get(
            "currentTaskId", interview.get("current_task_id", cursor.get("taskId"))
        ),
        "currentQuestionId": interview.get(
            "currentQuestionId",
            interview.get("current_question_id", cursor.get("questionId")),
        ),
    }


def build_personal_assistant_turn_context(
    agent: Any,
    user_message: Any,
) -> tuple[str, TurnIntention | None, dict[str, Any] | None]:
    """Build API-only authoritative context without mutating prompt history."""

    from agent.personal_assistant_calendar_gate import begin_calendar_first_planning_turn
    from agent.personal_assistant_output_gate import explicit_task_fact_update_requested

    if not bool(getattr(agent, "personal_assistant_mode", False)):
        begin_calendar_first_planning_turn(required=False)
        return "", None, None
    store = getattr(agent, "personal_assistant_state_store", None)
    if store is None:
        return "", None, None

    interview = _get_interview(store)
    workflows = [_workflow_projection(interview)] if interview else []
    ui_binding = getattr(agent, "personal_assistant_ui_binding", None)
    pending_actions = getattr(agent, "personal_assistant_pending_actions", ())
    resolution = resolve_turn_intention(
        user_message,
        workflows=workflows,
        ui_binding=ui_binding if isinstance(ui_binding, Mapping) else None,
        pending_actions=pending_actions if isinstance(pending_actions, Iterable) else (),
    )
    planning_query = resolution.action == "planning.query"
    task_fact_correction = explicit_task_fact_update_requested(user_message)
    now = datetime.now(ZoneInfo("Asia/Jerusalem"))
    requested_planning_date = _requested_planning_date(user_message, today=now)
    planning_grounding_ready = bool(
        interview
        and interview.get("planningDate") == requested_planning_date
        and interview.get("readinessApproved") is True
    )
    begin_calendar_first_planning_turn(
        required=planning_query,
        same_day_grounding_required=planning_query and not planning_grounding_ready,
        task_fact_correction_required=planning_query and task_fact_correction,
    )

    current_task: dict[str, Any] | None = None
    if interview:
        current_task_id = str(_workflow_projection(interview).get("currentTaskId") or "")
        for task in _bounded_items(interview.get("tasks"), limit=500):
            if isinstance(task, Mapping) and str(
                task.get("taskId") or task.get("id") or ""
            ) == current_task_id:
                current_task = dict(task)
                break

    payload = {
        "intent": {
            "action": resolution.action,
            "workflowId": resolution.workflow_id,
            "requiresClarification": resolution.requires_clarification,
            "requiresConfirmation": resolution.requires_confirmation,
            "candidates": list(resolution.candidates),
            "evidence": list(resolution.evidence),
            "preserveWorkflowIds": list(resolution.preserve_workflow_ids),
            "pendingActionId": resolution.pending_action_id,
            "answer": resolution.answer,
            "metadata": _bounded_intent_metadata(resolution.metadata),
        },
        "activeWorkflow": (
            {
                **_workflow_projection(interview),
                "readinessApproved": interview.get(
                    "readinessApproved", interview.get("readiness_approved", False)
                ),
                "taskCount": len(_bounded_items(interview.get("tasks"), limit=500)),
                "currentTask": current_task,
                "sourceSnapshot": interview.get(
                    "sourceSnapshot", interview.get("source_snapshot")
                ),
            }
            if interview
            else None
        ),
        "uiBinding": dict(ui_binding) if isinstance(ui_binding, Mapping) else None,
        "personalAssistant": _public_assistant_state(store),
    }
    context = (
        "# Authoritative Personal Assistant turn context\n"
        "Use this versioned context to resolve references and workflow actions. "
        "Do not reinterpret a canonical workflow action as a new planning request, "
        "and do not silently override uncertainty, stale data, or confirmation requirements. "
        "When intent.action starts with task.timer.start, you must call flowstate_start_timer. "
        "Resolve the exact task first from intent.metadata.taskId or taskQuery; a prose-only "
        "response cannot complete this canonical action. When intent.action is task.timer.stop, "
        "call flowstate_get_current_timer and then flowstate_stop_timer with the exact active "
        "session and revision; a prose-only response cannot complete the stop action. "
        "For every branch that could create, restore, start, stop, move, complete, reprioritize, "
        "or otherwise mutate state, an empty or skipped clarify answer cancels that branch. "
        "Do not call another mutation tool, prepare another mutation preview, or infer the first "
        "choice after a dismissed clarification. Report the cancellation briefly and continue "
        "only with a later explicit user command.\n"
        "```json\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        + "\n```"
    )
    return context, resolution, interview


__all__ = [
    "TurnIntention",
    "build_personal_assistant_turn_context",
    "resolve_turn_intention",
]
