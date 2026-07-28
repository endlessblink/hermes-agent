"""Pre-persistence output checks for Personal Assistant planning turns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import re
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```hermes-ui\s*\n(?P<body>.*?)\n```", re.DOTALL)
_PLANNING_ARTIFACT_TYPES = {
    "day-timeline",
    "week-planner",
    "task-table",
    "mini-kanban",
}
_DAY_TIMELINE_KEYS = {
    "actions", "blocks", "currentTime", "date", "description", "direction", "id", "title", "type"
}
_DAY_TIMELINE_BLOCK_KEYS = {
    "actions", "confidence", "doneEnough", "durationMinutes", "endTime", "id", "kind",
    "label", "startTime", "status", "taskId"
}
_MAX_DAY_TIMELINE_BLOCKS = 12


def _human_due_label(value: str) -> str:
    try:
        due = datetime.fromisoformat(value).date()
    except (TypeError, ValueError):
        return ""

    today = datetime.now(ZoneInfo("Asia/Jerusalem")).date()
    days = (due - today).days
    if days < 0:
        return "באיחור"
    if days == 0:
        return "להיום"
    if days == 1:
        return "למחר"
    if days <= 7:
        return "להמשך השבוע"
    return "להמשך"


def _normalized_planning_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def _task_duration_minutes(task: Mapping[str, Any]) -> int | None:
    duration = task.get("estimatedDuration")
    if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
        return duration

    due_date = str(task.get("dueDate") or "").strip()
    instances = task.get("instances")
    if not isinstance(instances, list):
        return None
    for instance in instances:
        if not isinstance(instance, Mapping):
            continue
        if due_date and str(instance.get("scheduledDate") or "").strip() != due_date:
            continue
        candidate = instance.get("duration")
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate > 0:
            return candidate
    return None


_DAY_TIMELINE_KINDS = {
    "break",
    "buffer",
    "calendar",
    "fixed",
    "floating",
    "focus",
    "short-task",
    "task",
}
_DAY_TIMELINE_STATUSES = {
    "candidate",
    "confirmed",
    "doing",
    "done",
    "dropped",
    "overdue",
    "planned",
    "proposed",
    "recommended",
    "scheduled",
}
_TASK_PROFILE_FIELD_LIMIT = 12
_TASK_PROFILE_QUESTION_TYPES = {
    "single-choice",
    "multi-choice",
    "short-text",
    "long-text",
}
_QUESTION_MARK_RE = re.compile(r"[?؟]")
_MISSING_PLANNING_INPUT_RE = re.compile(
    r"(?:חסר\s+לי(?:\s+רק)?\s+(?:נתון\s+)?(?:הקיבולת|האנרגיה|שעת\s+הסיום)|"
    r"(?:i\s+)?(?:still\s+)?need\s+(?:your\s+)?(?:capacity|energy|end[- ]of[- ]day))",
    re.IGNORECASE,
)
_MARKDOWN_TABLE_DIVIDER_RE = re.compile(
    r"(?m)^\s*\|?(?:\s*:?-{3,}:?\s*\|){2,}\s*$"
)
_CALENDAR_RECEIPT_UNSET = object()
_STATIC_PLAN_HEADING_RE = re.compile(
    r"(?im)^\s{0,3}(?:#{1,6}\s*)?(?:today(?:'s)?\s+(?:plan|priorities)|"
    r"daily\s+plan|week(?:ly)?\s+plan|תכנית\s+(?:להיום|יומית|שבועית)|"
    r"סדר\s+עדיפויות|העדיפויות\s+להיום)\s*:?.*$"
)
_NUMBERED_ITEM_RE = re.compile(r"(?m)^\s*\d+[.)]\s+\S+")
_INTERNAL_COVERAGE_JARGON_RE = re.compile(
    r"(?:מגבלת\s+כיסוי|פריטי?\s+הגנה|(?:עדכון\s+)?ה?הגנה|"
    r"coverage\s+(?:limit|receipt)|protected\s+items?)",
    re.IGNORECASE,
)


def _baseline_task_priority_score(record: Mapping[str, Any], planning_date: str) -> int:
    """Score only authoritative urgency signals that must not be silently ignored."""

    score = {"high": 40, "medium": 20, "low": 0}.get(
        str(record.get("priority") or "").strip().lower(), 0
    )
    due_date = str(record.get("dueDate") or "").strip()
    if planning_date and due_date:
        try:
            planned_day = datetime.fromisoformat(planning_date[:10]).date()
            due_day = datetime.fromisoformat(due_date[:10]).date()
            days_overdue = (planned_day - due_day).days
            if days_overdue >= 0:
                score += 30 + min(7, days_overdue * 7)
        except ValueError:
            if due_date <= planning_date:
                score += 30
    duration = record.get("estimatedDuration")
    if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
        score += 5
    return score
_EXPLICIT_DURABLE_UPDATE_PATTERNS = (
    re.compile(r"\b(?:please\s+)?remember\s+(?:that|to)\b", re.IGNORECASE),
    re.compile(r"\balways\s+remember\b", re.IGNORECASE),
    re.compile(r"\bfrom\s+now\s+on\b", re.IGNORECASE),
    re.compile(r"\bi\s+want\s+you\s+to\s+(?:always|never)\b", re.IGNORECASE),
    re.compile(
        r"(?:מעכשיו|תזכ(?:ור|רי)\s+(?:ש|את\s+זה|אותה|אותו)|"
        r"אני\s+רוצה\s+ש(?:תמיד|לעולם))"
    ),
)
_EXPLICIT_TASK_FACT_UPDATE_PATTERNS = (
    re.compile(
        r"(?:המשימה|משימת)\s+.{1,160}?(?:לוקח(?:ת)?|דורש(?:ת)?|משך)\s*.{0,40}?\d+\s*דקות",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:המשימה|משימת)\s+.{1,160}?(?:בעדיפות|דחיפות)\s+(?:גבוהה|בינונית|נמוכה)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:the\s+)?task\s+.{1,160}?(?:takes?|duration\s+is)\s*.{0,40}?\d+\s*(?:minutes?|mins?)\b",
        re.IGNORECASE | re.DOTALL,
    ),
)
_DURABLE_CAPTURE_APPLIED_CLAIM_RE = re.compile(
    r"(?:\b(?:saved|stored|remembered|updated\s+(?:my\s+)?(?:memory|preference))\b|"
    r"(?:נשמר(?:ה|ו)?|שמרתי|זכרתי|אזכור|עדכנתי)(?:\s|:|[.!]))",
    re.IGNORECASE,
)
_DURABLE_CAPTURE_PENDING_EXPLANATION_RE = re.compile(
    r"(?:\b(?:proposal|proposed).{0,160}\b(?:awaiting|pending|needs?)\b.{0,80}\bapproval\b|"
    r"\b(?:awaiting|pending)\b.{0,80}\bapproval\b|"
    r"(?:הצעתי|הצעה).{0,160}(?:ממתינ|מחכ|אישור)|"
    r"(?:ממתינ|מחכ).{0,80}אישור|(?:טרם|לא)\s+נשמר)",
    re.IGNORECASE | re.DOTALL,
)


def _has_unnegated_durable_capture_applied_claim(text: str) -> bool:
    for match in _DURABLE_CAPTURE_APPLIED_CLAIM_RE.finditer(text):
        prefix = text[max(0, match.start() - 24) : match.start()]
        if re.search(
            r"(?:(?:טרם|לא)\s+|\b(?:not|never|hasn't|hasnt|wasn't|wasnt)\s+)$",
            prefix,
            re.IGNORECASE,
        ):
            continue
        return True
    return False
_REMAINING_TODAY_RE = re.compile(
    r"(?:שאר\s+היום|להמשך\s+היום|remaining\s+(?:time\s+)?today|rest\s+of\s+(?:my\s+)?day)",
    re.IGNORECASE,
)
_THREE_DAY_OPTIONS_RE = re.compile(
    r"(?:\b3\s+(?:practical\s+)?(?:day\s+)?(?:plans?|options?)\b|"
    r"\bthree\s+(?:practical\s+)?(?:day\s+)?(?:plans?|options?)\b|"
    r"(?:3|שלוש|שלושה)\s+אפשרויות)",
    re.IGNORECASE,
)
_EXPLICIT_FULL_SCHEDULE_COMPARISON_RE = re.compile(
    r"(?:full\s+(?:day\s+)?schedules?|complete\s+(?:day\s+)?plans?|"
    r"לוחות?\s+זמנים?\s+מלאים?|תוכניות?\s+(?:יום\s+)?מלאות?)",
    re.IGNORECASE,
)
_FUTURE_ONLY_TIMING_RE = re.compile(
    r"(?:מחר|ביום\s+(?:ראשון|שני|שלישי|רביעי|חמישי|שישי|שבת)|tomorrow|next\s+(?:day|week|morning|afternoon|evening))",
    re.IGNORECASE,
)
_INTERNAL_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_PLAN_ADJUSTMENT_RE = re.compile(
    r"(?:adjust|revise|reorder|replace|alternative|time|energy|plan|"
    r"התאמ|שנ[הי]|סדר|החלפ|חלופ|זמן|אנרג|תכנ)",
    re.IGNORECASE,
)
_PLAN_AROUND_TASK_RE = re.compile(
    r"(?:plan|תכנ).{0,80}(?:remaining\s+(?:(?:time\s+)?today|day)|rest\s+of\s+(?:the\s+)?day|"
    r"שאר\s+היום|המשך\s+היום)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OutputGateDecision:
    accepted: bool
    reason: str = "accepted"
    retry_instruction: str = ""


def _direct_user_request_text(user_message: Any) -> str:
    """Exclude injected runtime facts when evaluating explicit user instructions."""

    text = str(user_message or "").strip()
    intent_marker = "My request or current intent:"
    facts_marker = "Available runtime facts"
    if intent_marker in text and facts_marker in text:
        text = text.split(intent_marker, 1)[1].split(facts_marker, 1)[0].strip()
    return text


def explicit_durable_update_requested(user_message: Any) -> bool:
    """Recognize direct requests to retain a correction beyond the current turn."""

    text = _direct_user_request_text(user_message)
    return bool(text) and any(
        pattern.search(text) for pattern in _EXPLICIT_DURABLE_UPDATE_PATTERNS
    )


def explicit_task_fact_update_requested(user_message: Any) -> bool:
    """Recognize a named connected-task fact that must update FlowState first."""

    text = _direct_user_request_text(user_message)
    return bool(text) and any(
        pattern.search(text) for pattern in _EXPLICIT_TASK_FACT_UPDATE_PATTERNS
    )


def contains_personal_assistant_plan(response: Any) -> bool:
    """Return whether a response contains a complete supported planning artifact."""

    text = str(response or "")
    for fence in _FENCE_RE.finditer(text):
        try:
            artifact = json.loads(fence.group("body"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(artifact, dict) and artifact.get("type") in _PLANNING_ARTIFACT_TYPES:
            return True
    return False


def extract_personal_assistant_recommendations(response: Any) -> list[dict[str, str]]:
    """Extract stable recommended task identities from supported planning artifacts."""

    recommendations: list[dict[str, str]] = []
    seen: set[str] = set()

    def append(task_id: Any, title: Any, surface: str) -> None:
        safe_id = str(task_id or "").strip()
        safe_title = str(title or "").strip()
        if not safe_id or safe_id in seen:
            return
        seen.add(safe_id)
        recommendations.append(
            {"taskId": safe_id[:500], "title": safe_title[:1_000], "surface": surface}
        )

    for fence in _FENCE_RE.finditer(str(response or "")):
        try:
            artifact = json.loads(fence.group("body"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(artifact, dict):
            continue
        surface = str(artifact.get("type") or "")
        if surface == "day-timeline":
            for block in artifact.get("blocks") or []:
                if isinstance(block, dict) and str(block.get("kind") or "focus") not in {
                    "break",
                    "buffer",
                }:
                    append(block.get("taskId"), block.get("label"), surface)
        elif surface == "week-planner":
            for day in artifact.get("days") or []:
                if not isinstance(day, dict):
                    continue
                for block in day.get("blocks") or []:
                    if isinstance(block, dict) and str(block.get("kind") or "focus") not in {
                        "break",
                        "buffer",
                    }:
                        append(block.get("taskId"), block.get("label"), surface)
        elif surface == "task-table":
            for row in artifact.get("rows") or []:
                if isinstance(row, dict):
                    append(row.get("id"), row.get("title"), surface)
        elif surface == "mini-kanban":
            for lane in artifact.get("lanes") or []:
                if not isinstance(lane, dict):
                    continue
                for task in lane.get("tasks") or []:
                    if isinstance(task, dict):
                        append(task.get("id"), task.get("title"), surface)
        if len(recommendations) >= 500:
            break
    return recommendations[:500]


def _interview_value(interview: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in interview:
            return interview[key]
    return None


def _interview_cursor_value(interview: Mapping[str, Any], *keys: str) -> Any:
    value = _interview_value(interview, *keys)
    if value is not None:
        return value
    cursor = interview.get("cursor")
    if not isinstance(cursor, Mapping):
        return None
    cursor_key = "taskId" if any("Task" in key or "task" in key for key in keys) else "questionId"
    return cursor.get(cursor_key)


def _reject(reason: str, instruction: str) -> OutputGateDecision:
    return OutputGateDecision(False, reason, instruction)


def _calendar_events_in_progress(receipt: Any) -> list[Mapping[str, Any]]:
    if not isinstance(receipt, Mapping):
        return []
    try:
        captured_at = datetime.fromisoformat(str(receipt.get("capturedAt") or ""))
    except ValueError:
        return []
    current_events = []
    for event in receipt.get("events") or []:
        if not isinstance(event, Mapping):
            continue
        start = event.get("start")
        end = event.get("end")
        if not isinstance(start, Mapping) or not isinstance(end, Mapping):
            continue
        try:
            starts_at = datetime.fromisoformat(str(start.get("dateTime") or ""))
            ends_at = datetime.fromisoformat(str(end.get("dateTime") or ""))
        except ValueError:
            continue
        if starts_at <= captured_at < ends_at:
            current_events.append(event)
    return current_events


def _event_occupies_rest_of_day(event: Mapping[str, Any], receipt: Any) -> bool:
    if not isinstance(receipt, Mapping):
        return False
    end = event.get("end")
    receipt_range = receipt.get("range")
    if not isinstance(end, Mapping) or not isinstance(receipt_range, Mapping):
        return False
    try:
        ends_at = datetime.fromisoformat(str(end.get("dateTime") or ""))
        day_boundary = datetime.fromisoformat(str(receipt_range.get("endDate") or ""))
        timezone = ZoneInfo(str(receipt.get("timezone") or "UTC"))
    except (ValueError, ZoneInfoNotFoundError):
        return False
    return ends_at.astimezone(timezone) >= day_boundary.replace(tzinfo=timezone)


_ELAPSED_WINDOW_RE = re.compile(
    r"(?:\b(?:before|by|until)\b|(?:עד|לפני))\s*(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)",
    re.IGNORECASE,
)


def _recommendation_window_already_elapsed(value: Any, receipt: Any) -> bool:
    if not isinstance(value, str) or not isinstance(receipt, Mapping):
        return False
    match = _ELAPSED_WINDOW_RE.search(value)
    if not match:
        return False
    try:
        captured_at = datetime.fromisoformat(str(receipt.get("capturedAt") or ""))
        timezone = ZoneInfo(str(receipt.get("timezone") or "UTC"))
    except (ValueError, ZoneInfoNotFoundError):
        return False
    local_now = captured_at.astimezone(timezone)
    cutoff_minutes = int(match.group("hour")) * 60 + int(match.group("minute"))
    return cutoff_minutes < local_now.hour * 60 + local_now.minute


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _task_profile_review_contract_error(artifact: Mapping[str, Any]) -> str | None:
    """Return why Desktop and Telegram cannot safely render this review card."""

    if not _nonempty_text(artifact.get("interviewId")):
        return "interviewId is required"
    revision = artifact.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return "revision must be a non-negative integer"

    task = artifact.get("task")
    if not isinstance(task, Mapping):
        return "task is required"
    if not _nonempty_text(task.get("id")) or not _nonempty_text(task.get("title")):
        return "task id and title are required"

    progress = artifact.get("progress")
    if not isinstance(progress, Mapping):
        return "progress is required"
    current = progress.get("current")
    total = progress.get("total")
    if (
        isinstance(current, bool)
        or isinstance(total, bool)
        or not isinstance(current, int)
        or not isinstance(total, int)
        or current < 1
        or current > total
    ):
        return "progress must identify one current task within the total"

    profile_fields = artifact.get("profileFields")
    if not isinstance(profile_fields, list) or not 1 <= len(profile_fields) <= _TASK_PROFILE_FIELD_LIMIT:
        return f"profileFields must contain 1-{_TASK_PROFILE_FIELD_LIMIT} fields"
    field_ids: set[str] = set()
    for field in profile_fields:
        if not isinstance(field, Mapping):
            return "each profile field must be an object"
        if any(key not in {"id", "label", "value"} for key in field):
            return "profile fields may contain only id, label, and value"
        field_id = field.get("id")
        if (
            not _nonempty_text(field_id)
            or not _nonempty_text(field.get("label"))
            or field_id in field_ids
        ):
            return "profile field ids and labels must be non-empty and unique"
        value = field.get("value")
        if "value" in field and not (
            isinstance(value, str)
            or (isinstance(value, list) and all(isinstance(item, str) for item in value))
        ):
            return "profile field values must be text or text lists"
        field_ids.add(field_id)

    question = artifact.get("question")
    if not isinstance(question, Mapping):
        return "question is required"
    allowed_question_keys = {
        "allowCustomAnswer",
        "customAnswerLabel",
        "default",
        "description",
        "id",
        "label",
        "options",
        "placeholder",
        "profileFieldId",
        "required",
        "type",
    }
    if any(key not in allowed_question_keys for key in question):
        return "question contains unsupported properties"
    question_id = question.get("id")
    profile_field_id = question.get("profileFieldId")
    if not _nonempty_text(question_id) or not _nonempty_text(profile_field_id):
        return "question id and profileFieldId are required"
    if question_id != profile_field_id or profile_field_id not in field_ids:
        return "question must reference its matching profile field"
    if not _nonempty_text(question.get("label")):
        return "question label is required"
    question_type = question.get("type")
    if question_type not in _TASK_PROFILE_QUESTION_TYPES:
        return "question type is unsupported"
    if question.get("required") is not None and not isinstance(question.get("required"), bool):
        return "question required must be a boolean"
    if question.get("allowCustomAnswer") is not None and not isinstance(
        question.get("allowCustomAnswer"), bool
    ):
        return "allowCustomAnswer must be a boolean"

    options = question.get("options")
    if question_type in {"single-choice", "multi-choice"}:
        if not isinstance(options, list) or not 1 <= len(options) <= 12:
            return "choice questions must contain 1-12 options"
        option_values: set[str] = set()
        for option in options:
            if isinstance(option, str):
                value = label = option
            elif isinstance(option, Mapping):
                if any(key not in {"label", "value"} for key in option):
                    return "choice option objects may contain only value and label"
                value = option.get("value")
                label = option.get("label")
            else:
                return "choice options must be text or value-label objects"
            if not _nonempty_text(value) or not _nonempty_text(label) or value in option_values:
                return "choice option values and labels must be non-empty and unique"
            option_values.add(value)
    elif options is not None:
        return "text questions cannot contain options"
    return None


def build_safe_interview_fallback(interview: Mapping[str, Any] | None) -> str:
    """Return a bounded current-question card when private retries are exhausted."""

    if not isinstance(interview, Mapping):
        return (
            "I could not finish checking the planning inputs safely, so no plan was applied. "
            "Please retry; Hermes still needs to complete the source checks or ask the missing "
            "same-day planning question."
        )
    interview_id = str(
        _interview_value(interview, "interviewId", "interview_id") or ""
    )
    revision = _interview_value(
        interview, "interviewRevision", "interview_revision", "revision"
    )
    task_id = str(
        _interview_cursor_value(interview, "currentTaskId", "current_task_id") or ""
    )
    question_id = str(
        _interview_cursor_value(
            interview, "currentQuestionId", "current_question_id"
        ) or ""
    )
    if not interview_id or not task_id or not question_id:
        return (
            "I could not finish checking the planning inputs safely, so no plan was applied. "
            "Please retry; Hermes still needs to complete the source checks or ask the missing "
            "same-day planning question."
        )
    title = "Current task"
    tasks = interview.get("tasks")
    current_index = 0
    total = len(tasks) if isinstance(tasks, list) else 1
    if isinstance(tasks, list):
        for index, task in enumerate(tasks):
            if isinstance(task, Mapping) and str(
                task.get("taskId") or task.get("id") or ""
            ) == task_id:
                title = str(task.get("title") or title)[:200]
                current_index = index
                break
    profile = {}
    daily_grounding = interview.get("mode") == "daily-grounding"
    if daily_grounding:
        daily_questions = tuple(
            interview.get("questionOrder") or ("energy", "workBoundary", "hardCommitments", "location")
        )
        total = len(daily_questions)
        current_index = (
            daily_questions.index(question_id) if question_id in daily_questions else 0
        )
    if isinstance(tasks, list) and current_index < len(tasks):
        candidate = tasks[current_index]
        if isinstance(candidate, Mapping) and isinstance(candidate.get("profile"), Mapping):
            profile = dict(candidate["profile"])
    labels = {
        "urgency": "How urgent is this, considering real consequences and deadlines?",
        "importance": "How important is this relative to your other commitments?",
        "outcome": "What concrete outcome do you want from this task?",
        "dependencies": "What does this depend on? Select everything that applies.",
        "effort": "How much effort does this realistically need?",
        "energy": "What kind of energy or focus does this require?",
        "timing": "When can or must this happen?",
        "risks": "What could make this fall through the cracks? Select everything that applies.",
        "doneEnough": "What would count as enough progress for the next step?",
    }
    if daily_grounding:
        labels.update(
            {
                "energy": "כמה אנרגיה יש לך להמשך היום?",
                "workBoundary": "עד איזו שעה מתאים לך לעבוד היום?",
                "hardCommitments": "יש היום התחייבות שלא מופיעה ביומן?",
                "location": "איפה תהיה בזמן העבודה שנותר?",
                "availability": "באילו שעות אתה רוצה שאשתמש לתכנון מחר, מעבר להתחייבויות שכבר ביומן?",
                "progressReview": (
                    "מה כבר הושלם מאז הבדיקה האחרונה? "
                    "כתוב שמות משימות, או ״שום דבר״."
                ),
            }
        )
    field_labels = {
        "urgency": "Urgency",
        "importance": "Importance",
        "outcome": "Desired outcome",
        "dependencies": "Dependencies",
        "effort": "Effort",
        "energy": "Energy",
        "timing": "Timing",
        "risks": "Risks",
        "doneEnough": "Done enough",
        "notes": "Notes",
        "context": "Context",
        "confidence": "Confidence",
        "evidence": "Evidence",
        "constraints": "Constraints",
        "workBoundary": "שעת סיום",
        "hardCommitments": "התחייבויות נוספות",
                "location": "מיקום",
                "availability": "זמינות מחר",
                "progressReview": "מה הושלם",
    }
    options = {
        "urgency": [
            {"value": "critical", "label": "Critical — harm or deadline risk"},
            {"value": "high", "label": "High — needs attention soon"},
            {"value": "medium", "label": "Medium — important but movable"},
            {"value": "low", "label": "Low — safely deferable"},
            {"value": "unknown", "label": "Not enough context yet"},
        ],
        "importance": [
            {"value": "protected", "label": "Protected commitment"},
            {"value": "high", "label": "High leverage"},
            {"value": "supporting", "label": "Supports another outcome"},
            {"value": "optional", "label": "Optional"},
            {"value": "unknown", "label": "Not sure yet"},
        ],
        "dependencies": [
            {"value": "person", "label": "Another person"},
            {"value": "information", "label": "Missing information"},
            {"value": "decision", "label": "A decision"},
            {"value": "prior-task", "label": "Another task first"},
            {"value": "none", "label": "No known dependency"},
        ],
        "energy": [
            {"value": "deep", "label": "Deep focus"},
            {"value": "normal", "label": "Normal focus"},
            {"value": "light", "label": "Low-energy friendly"},
            {"value": "social", "label": "Conversation or coordination"},
        ],
        "risks": [
            {"value": "deadline", "label": "Deadline or consequence"},
            {"value": "blocked", "label": "Blocked by someone or something"},
            {"value": "too-large", "label": "Too large or vague"},
            {"value": "forgotten", "label": "Easy to forget"},
            {"value": "none", "label": "No known risk"},
        ],
    }
    if daily_grounding:
        options.update(
            {
                "energy": [
                    {"value": "low", "label": "נמוכה — רק דברים קלים וקצרים"},
                    {"value": "medium", "label": "בינונית — בלוק מרכזי אחד"},
                    {"value": "high", "label": "גבוהה — אפשר שני בלוקים משמעותיים"},
                ],
                "workBoundary": [
                    {"value": "18:00", "label": "18:00"},
                    {"value": "20:00", "label": "20:00"},
                    {"value": "22:00", "label": "22:00"},
                ],
                "hardCommitments": [
                    {"value": "calendar-only", "label": "לא — רק מה שכבר ביומן"},
                    {"value": "yes", "label": "כן — אוסיף בקצרה"},
                ],
                "location": [
                    {"value": "home", "label": "בבית"},
                    {"value": "outside", "label": "מחוץ לבית"},
                    {"value": "travel", "label": "בתנועה או עם נסיעות"},
                ],
                "availability": [
                    {"value": "09:00-21:00", "label": "חלון עבודה 09:00–21:00 סביב היומן"},
                    {"value": "10:00-20:00", "label": "חלון עבודה 10:00–20:00 סביב היומן"},
                    {"value": "09:00-21:00-buffered", "label": "09:00–21:00 עם מרווחים נדיבים"},
                ],
            }
        )
    question_type = "multi-choice" if question_id in {"dependencies", "risks"} else (
        "single-choice" if question_id in options else "long-text"
    )
    profile_fields = [
        {
            "id": field,
            "label": field_labels.get(field, field),
            **({"value": value} if value is not None else {}),
        }
        for field, value in profile.items()
    ]
    if question_id and not any(field["id"] == question_id for field in profile_fields):
        profile_fields.append(
            {"id": question_id, "label": field_labels.get(question_id, question_id)}
        )
    if not profile_fields:
        fallback_field_id = question_id or "context"
        profile_fields = [
            {
                "id": fallback_field_id,
                "label": field_labels.get(fallback_field_id, "Context"),
            }
        ]
    if len(profile_fields) > _TASK_PROFILE_FIELD_LIMIT:
        current_field = next(
            (field for field in profile_fields if field["id"] == question_id),
            profile_fields[-1],
        )
        profile_fields = [
            field for field in profile_fields if field["id"] != current_field["id"]
        ][:_TASK_PROFILE_FIELD_LIMIT - 1] + [current_field]

    artifact = {
        "type": "task-profile-review",
        "id": f"resume-{question_id or task_id or 'interview'}"[:200],
        "interviewId": interview_id,
        "revision": revision,
        "title": title,
        "task": {"id": task_id, "title": title},
        "progress": {"current": min(current_index + 1, max(total, 1)), "total": max(total, 1)},
        "profileFields": profile_fields,
        "question": {
            "id": question_id,
            "profileFieldId": question_id,
            "label": labels.get(
                question_id,
                "What should Hermes understand before moving forward?",
            ),
            "type": question_type,
            **({"options": options[question_id]} if question_id in options else {}),
            "required": True,
            "allowCustomAnswer": True,
            "customAnswerLabel": "תשובה אחרת" if daily_grounding else "Write your answer",
        },
    }
    return "```hermes-ui\n" + json.dumps(
        artifact, ensure_ascii=False, separators=(",", ":")
    ) + "\n```"


def build_safe_interview_artifact(
    interview: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Project the current durable interview question into its Desktop artifact."""

    rendered = build_safe_interview_fallback(interview)
    prefix = "```hermes-ui\n"
    suffix = "\n```"
    if not rendered.startswith(prefix) or not rendered.endswith(suffix):
        return None
    try:
        artifact = json.loads(rendered[len(prefix) : -len(suffix)])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(artifact, dict) or artifact.get("type") != "task-profile-review":
        return None
    return artifact


def build_grounded_plan_fallback(
    *,
    task_inventory_records: Mapping[str, Mapping[str, Any]] | None,
    task_details: Mapping[str, Mapping[str, Any]] | None,
    candidate_records: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    recent_recommendations: Sequence[Mapping[str, Any]] | None = None,
    protected_items: Sequence[Mapping[str, Any]] | None = None,
    calendar_receipt: Mapping[str, Any] | None = None,
    availability: str = "",
    planning_date: str = "",
    preferred_task_title: str = "",
    excluded_task_titles: Sequence[str] | None = None,
    user_message: Any,
) -> str | None:
    """Build a compact plan from this turn's authoritative task reads only."""

    del user_message
    inventory = task_inventory_records or {}
    details = task_details or {}
    all_sources = candidate_records or {}
    preferred_title = str(preferred_task_title or "").strip().casefold()
    excluded_titles = {
        str(title or "").strip().casefold()
        for title in (excluded_task_titles or ())
        if str(title or "").strip()
    }
    recent_ids = {
        str(item.get("taskId") or "").strip()
        for item in (recent_recommendations or [])
        if isinstance(item, Mapping) and str(item.get("taskId") or "").strip()
    }
    protected_by_identity: dict[tuple[str, str], Mapping[str, Any]] = {}
    protected_by_title: dict[tuple[str, str], Mapping[str, Any]] = {}
    for item in protected_items or []:
        if not isinstance(item, Mapping):
            continue
        source_id = str(item.get("sourceId") or "").strip()
        item_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip().casefold()
        if source_id and item_id:
            protected_by_identity[(source_id, item_id)] = item
        if source_id and title:
            protected_by_title[(source_id, title)] = item
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    closed_statuses = {"done", "completed", "cancelled", "canceled", "archived"}

    normalized_sources = [
        records
        for records in all_sources.values()
        if isinstance(records, Mapping)
    ]
    preferred_task_ids = {
        str(task_id)
        for source in (details, inventory, *normalized_sources)
        for task_id, raw_task in source.items()
        if preferred_title
        and isinstance(raw_task, Mapping)
        and str(raw_task.get("title") or "").strip().casefold() == preferred_title
    }
    for source in (details, inventory, *normalized_sources):
        for task_id, raw_task in source.items():
            if task_id in seen or not isinstance(raw_task, Mapping):
                continue
            title = str(raw_task.get("title") or "").strip()
            status = str(raw_task.get("status") or "todo").strip().lower()
            if (
                not title
                or status in closed_statuses
                or title.casefold() in excluded_titles
            ):
                continue
            source_id = str(raw_task.get("sourceId") or "flowstate").strip()
            protected = protected_by_identity.get(
                (source_id, str(task_id))
            ) or protected_by_title.get((source_id, title.casefold()))
            disposition = (
                str(protected.get("disposition") or "").strip().lower()
                if isinstance(protected, Mapping)
                else ""
            )
            if disposition in {"completed", "cancelled"}:
                continue
            seen.add(task_id)
            candidates.append(
                {
                    "id": str(task_id),
                    "title": title,
                    "priority": str(raw_task.get("priority") or "").strip().lower(),
                    "dueDate": str(raw_task.get("dueDate") or "").strip(),
                    "estimatedDuration": _task_duration_minutes(raw_task),
                    "fromDetails": task_id in details,
                    "sourceId": source_id,
                    "recentlySuggested": task_id in recent_ids,
                    "disposition": disposition,
                }
            )

    if len(candidates) < 3:
        return None

    priority_rank = {"urgent": 0, "high": 0, "medium": 1, "low": 2, "": 3}
    candidates.sort(
        key=lambda task: (
            -(
                _baseline_task_priority_score(task, planning_date)
                - (15 if task["recentlySuggested"] else 0)
                 + (10 if task["disposition"] == "actionable" else 0)
                + (
                    100
                    if preferred_title
                    and (
                        task["title"].casefold() == preferred_title
                        or task["id"] in preferred_task_ids
                    )
                    else 0
                )
                - (
                    35
                    if task["disposition"] in {"waiting", "deferred", "needs_context"}
                    else 0
                )
            ),
            priority_rank.get(task["priority"], 3),
            task["dueDate"] or "9999-12-31",
            0 if task["fromDetails"] else 1,
            len(task["title"]),
            task["title"],
        )
    )

    rows = []
    today = datetime.now(ZoneInfo("Asia/Jerusalem")).date().isoformat()
    planning_scope = "מחר" if planning_date and planning_date > today else "היום"
    priority_labels = {"urgent": "דחופה", "high": "גבוהה", "medium": "בינונית", "low": "נמוכה"}
    for task in candidates[:3]:
        facts = []
        if task["priority"]:
            facts.append(f"עדיפות {priority_labels.get(task['priority'], task['priority'])}")
        due_label = _human_due_label(task["dueDate"])
        if due_label:
            facts.append(due_label)
        duration = task.get("estimatedDuration")
        if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
            facts.append(f"{duration} דק׳")
        title = task["title"]
        rows.append(
            {
                "id": task["id"],
                "title": title,
                "cells": {"reason": " · ".join(facts) or "משימה פתוחה שנבדקה עכשיו"},
                "actions": [
                    {
                        "id": f"plan-{task['id']}",
                        "label": f"לבחור: {title}"[:80],
                        "submitText": (
                            f"בחר באפשרות {title} לתכנון {planning_scope} "
                            "ובנה סביבה תוכנית גמישה."
                        ),
                    },
                ],
            }
        )

    source_count = len(
        [
            records
            for records in all_sources.values()
            if isinstance(records, Mapping)
        ]
    )
    candidate_count = sum(
        len(records)
        for records in all_sources.values()
        if isinstance(records, Mapping)
    )
    coverage = (
        calendar_receipt.get("coverage")
        if isinstance(calendar_receipt, Mapping)
        else None
    )
    calendar_count = (
        coverage.get("calendarCount")
        if isinstance(coverage, Mapping)
        and isinstance(coverage.get("calendarCount"), int)
        else None
    )
    basis_parts = []
    if source_count:
        basis_parts.append(
            f"{candidate_count} משימות מכל {source_count} מקורות"
        )
    if calendar_count is not None:
        basis_parts.append(f"{calendar_count} יומנים")
    if availability:
        basis_parts.append(f"זמינות {availability}")
    description = (
        "נבדקו " + " · ".join(basis_parts) + "."
        if basis_parts
        else "מבוסס על המשימות הפתוחות שנבדקו עכשיו."
    )
    selected_title = next(
        (
            row["title"]
            for row in rows
            if preferred_title
            and (
                str(row.get("title") or "").casefold() == preferred_title
                or str(row.get("id") or "") in preferred_task_ids
            )
        ),
        "",
    )
    shown_titles = "، ".join(f"„{row['title']}”" for row in rows)
    artifact = {
        "type": "task-table",
        "direction": "rtl",
        "title": (
            f"תוכנית גמישה סביב {selected_title}"
            if selected_title
            else "שלוש אפשרויות מעשיות"
        ),
        "description": description,
        "columns": ["task", {"key": "reason", "label": "למה עכשיו"}],
        "rows": rows,
        "actions": [
            {
                "id": "adjust-day",
                "label": "שנה זמן או אנרגיה",
                "submitText": (
                    "לפני תכנון מחדש, שאל אותי שאלה אחת ממוקדת על זמן, אנרגיה, סדר "
                    "או חלופות; השתמש במה שכבר ידוע."
                ),
            },
            {
                "id": "show-alternatives",
                "label": "הצג אפשרויות אחרות",
                "submitText": (
                    f"הצג שלוש אפשרויות אחרות לתכנון {planning_scope}. "
                    f"אל תחזור על {shown_titles}."
                ),
            },
        ],
    }
    return "```hermes-ui\n" + json.dumps(
        artifact, ensure_ascii=False, separators=(",", ":")
    ) + "\n```"


def should_build_grounded_plan_fallback(
    *,
    reason: str,
    task_inventory_complete: bool,
    task_details_count: int,
) -> bool:
    return (
        task_inventory_complete
        and task_details_count >= 3
        and reason
        in {
            "excessive_planning_detail",
            "invalid_day_timeline_contract",
            "internal_coverage_jargon_exposed",
            "priority_ranking_required",
            "task_duration_fidelity_required",
        }
    )


def evaluate_personal_assistant_output(
    response: Any,
    *,
    interview: Mapping[str, Any] | None,
    intent_action: str | None = None,
    user_message: Any = None,
    calendar_receipt: Mapping[str, Any] | None | object = _CALENDAR_RECEIPT_UNSET,
    timer_action_executed: bool = False,
    durable_capture_required: bool = False,
    durable_capture_executed: bool = False,
    task_inventory_complete: bool | None = None,
    task_inventory_ids: frozenset[str] | set[str] | None = None,
    task_inventory_records: Mapping[str, Mapping[str, Any]] | None = None,
    task_details: Mapping[str, Mapping[str, Any]] | None = None,
    expected_task_source_ids: frozenset[str] | set[str] | None = None,
    coverage_recorded: bool = False,
    coverage_receipt: Mapping[str, Any] | None = None,
    planning_interview_required: bool = False,
    task_fact_update_required: bool = False,
    task_fact_update_executed: bool = False,
) -> OutputGateDecision:
    """Accept output only when it matches the authoritative interview state."""

    text = str(response or "")
    visible_text = _FENCE_RE.sub("", text)
    if (
        str(intent_action or "") == "planning.query"
        and _INTERNAL_COVERAGE_JARGON_RE.search(visible_text)
    ):
        return _reject(
            "internal_coverage_jargon_exposed",
            "Do not expose safety-review, protected-item, or coverage-receipt jargon. If every "
            "configured source and required protected item was reviewed, return the compact plan. "
            "If coverage is incomplete, finish the missing reads or show one plain source problem "
            "with a useful retry action.",
        )
    if task_fact_update_required and not task_fact_update_executed:
        return _reject(
            "canonical_task_fact_update_required",
            "The user corrected a named FlowState task fact. Before asking planning or preference "
            "questions, call flowstate_get_task for that named task and preview one "
            "flowstate_update_task patch using estimatedDuration and any other corrected fields. "
            "Do not call flowstate_resize_work_block for a date-only occurrence, do not store this "
            "as assistant memory, and do not claim it was remembered. Return the named canonical "
            "preview for one visible approval.",
        )
    if _MISSING_PLANNING_INPUT_RE.search(visible_text) and "```hermes-ui" not in text:
        return _reject(
            "missing_planning_input_interaction_required",
            "Do not merely state that planning capacity, energy, or the work boundary is missing. "
            "Call personal_assistant_interview_start for the requested planning date and render "
            "exactly one current interactive question, reusing every fact already known.",
        )
    if (
        str(intent_action or "").startswith(("task.", "planning.", "workflow."))
        and _INTERNAL_UUID_RE.search(visible_text)
    ):
        return _reject(
            "internal_identifier_exposed",
            "Remove internal task, timer-session, instance, and revision identifiers from the "
            "user-facing receipt. Confirm the result with the exact task name and only the useful "
            "state, duration, or next action. IDs may remain only in hidden tool or artifact routing fields.",
        )
    if durable_capture_required and not durable_capture_executed:
        return _reject(
            "durable_capture_required",
            "The user explicitly asked for a lasting correction or preference update. Call "
            "personal_assistant_propose_capture with the narrowest user-supported wording, "
            "then explain that it is awaiting visible approval. Do not merely promise to remember it.",
        )
    if (
        durable_capture_required
        and durable_capture_executed
        and _has_unnegated_durable_capture_applied_claim(visible_text)
    ):
        return _reject(
            "durable_capture_falsely_claimed_applied",
            "The capture tool created a proposal only; it did not save or accept the preference. "
            "Say that the proposal is awaiting visible user approval. Do not claim it was saved, "
            "remembered, or applied until a later approved apply action and authoritative readback.",
        )
    if (
        durable_capture_required
        and durable_capture_executed
        and not _DURABLE_CAPTURE_PENDING_EXPLANATION_RE.search(visible_text)
    ):
        return _reject(
            "durable_capture_approval_explanation_required",
            "Tell the user briefly that the correction is a proposal awaiting visible approval "
            "and has not been saved yet. Keep the plan useful, but do not hide the pending "
            "approval or imply that the durable preference already changed.",
        )
    if str(intent_action or "").startswith("task.timer.start") and not timer_action_executed:
        return _reject(
            "timer_action_not_executed",
            "Resolve the exact task first: use intent metadata taskId when present, otherwise "
            "search the complete FlowState inventory using intent metadata taskQuery and verify "
            "the selected task with flowstate_get_task. Then call flowstate_start_timer. The "
            "tool is available in this live session; do not claim it is unavailable based on "
            "earlier conversation history. Do not complete this action with a prose-only response.",
        )
    if str(intent_action or "") == "task.timer.stop" and not timer_action_executed:
        return _reject(
            "timer_action_not_executed",
            "Call flowstate_get_current_timer, use the exact active sessionId and canonicalRevision, "
            "then call flowstate_stop_timer. The tool is available in this live session; do not "
            "claim it is unavailable based on earlier conversation history. Do not complete this "
            "action with a prose-only response.",
        )
    fences = list(_FENCE_RE.finditer(text))
    if "```hermes-ui" in text and not fences:
        return _reject(
            "invalid_hermes_ui",
            "Return one complete valid hermes-ui artifact. Never expose raw or partial JSON.",
        )

    artifacts: list[dict[str, Any]] = []
    for fence in fences:
        try:
            parsed = json.loads(fence.group("body"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return _reject(
                "invalid_hermes_ui",
                "Return one complete valid hermes-ui artifact. Never expose raw or partial JSON.",
            )
        if not isinstance(parsed, dict) or not isinstance(parsed.get("type"), str):
            return _reject(
                "invalid_hermes_ui",
                "Return one complete valid hermes-ui artifact with a supported type.",
            )
        if parsed.get("type") == "task-table":
            rows = parsed.get("rows")
            if isinstance(rows, list) and any(
                isinstance(row, Mapping)
                and isinstance(row.get("cells"), Mapping)
                and "task" in row["cells"]
                for row in rows
            ):
                return _reject(
                    "invalid_task_table",
                    "Regenerate the task-table without cells.task. The task column is rendered "
                    "from each row's top-level title; cells may contain only other declared columns.",
                )
        if parsed.get("type") == "day-timeline":
            unsupported = sorted(set(parsed) - _DAY_TIMELINE_KEYS)
            blocks = parsed.get("blocks")
            unsupported_blocks = [
                (index, sorted(set(block) - _DAY_TIMELINE_BLOCK_KEYS))
                for index, block in enumerate(blocks if isinstance(blocks, list) else [])
                if isinstance(block, Mapping) and set(block) - _DAY_TIMELINE_BLOCK_KEYS
            ]
            if unsupported or unsupported_blocks:
                details = []
                if unsupported:
                    details.append("top-level: " + ", ".join(unsupported))
                details.extend(
                    f"blocks[{index}]: " + ", ".join(keys)
                    for index, keys in unsupported_blocks
                )
                return _reject(
                    "invalid_day_timeline_contract",
                    "Remove fields that Desktop cannot render from the day-timeline artifact ("
                    + "; ".join(details)
                    + "). Put rationale in doneEnough or description; do not invent fields.",
                )
            if isinstance(blocks, list) and len(blocks) > _MAX_DAY_TIMELINE_BLOCKS:
                return _reject(
                    "invalid_day_timeline_contract",
                    "Regenerate the day-timeline with at most 12 blocks so Desktop can render it. "
                    "Combine adjacent low-detail items or move optional work behind an adjustment action.",
                )
            for index, block in enumerate(blocks if isinstance(blocks, list) else []):
                if not isinstance(block, Mapping):
                    continue
                kind = block.get("kind")
                status = block.get("status")
                if "kind" in block and kind not in _DAY_TIMELINE_KINDS:
                    return _reject(
                        "invalid_day_timeline_contract",
                        f"Regenerate blocks[{index}].kind with a Desktop-supported value or omit kind.",
                    )
                if "status" in block and status not in _DAY_TIMELINE_STATUSES:
                    return _reject(
                        "invalid_day_timeline_contract",
                        f"Regenerate blocks[{index}].status with a Desktop-supported value or omit status.",
                    )
        artifacts.append(parsed)

    expected_sources = {
        str(source_id).strip()
        for source_id in (expected_task_source_ids or set())
        if str(source_id).strip()
    }
    is_interview_question = (
        len(artifacts) == 1 and artifacts[0].get("type") == "task-profile-review"
    )
    is_planning_output = any(
        artifact.get("type") in _PLANNING_ARTIFACT_TYPES for artifact in artifacts
    )
    if calendar_receipt is not _CALENDAR_RECEIPT_UNSET and is_planning_output:
        from datetime import date, timedelta
        from agent.personal_assistant_calendar_gate import (
            calendar_receipt_covers,
            calendar_receipt_is_fresh_complete,
        )

        if not calendar_receipt_is_fresh_complete(calendar_receipt):
            return _reject(
                "calendar_preflight_required",
                "Do not render a plan. First call personal_assistant_calendar_preflight for the "
                "exact requested range and timezone. Continue only when its receipt is complete "
                "and fresh; otherwise show only Retry, Repair Calendar, or Cancel choices.",
            )
        for artifact in artifacts:
            artifact_type = artifact.get("type")
            start_date = ""
            days = 0
            if artifact_type == "day-timeline":
                start_date = str(artifact.get("date") or "")
                days = 1
            elif artifact_type == "week-planner":
                start_date = str(artifact.get("weekStart") or "")
                days = 7
            if not start_date:
                continue
            try:
                end_date = (date.fromisoformat(start_date) + timedelta(days=days)).isoformat()
            except ValueError:
                continue
            if not calendar_receipt_covers(
                calendar_receipt,
                start_date=start_date,
                end_date=end_date,
            ):
                return _reject(
                    "calendar_preflight_scope_mismatch",
                    "Do not render this plan. Run personal_assistant_calendar_preflight again "
                    "for the plan's exact date range in Asia/Jerusalem, then regenerate it.",
                )
    correction_preview_only = bool(
        task_fact_update_required
        and task_fact_update_executed
        and not is_planning_output
    )
    planning_gate_required = (
        intent_action == "planning.query" or is_planning_output
    ) and not correction_preview_only
    interview_waiting_for_answer = bool(
        isinstance(interview, Mapping)
        and str(interview.get("status") or "active") in {"active", "paused"}
        and not bool(
            _interview_value(interview, "readinessApproved", "readiness_approved")
        )
        and planning_interview_required
    )
    if (
        planning_gate_required
        and expected_sources
        and not is_interview_question
        and not interview_waiting_for_answer
    ):
        covered_sources = {
            str(source.get("id") or "").strip()
            for source in ((coverage_receipt or {}).get("sources") or [])
            if isinstance(source, Mapping) and str(source.get("id") or "").strip()
        }
        if not coverage_recorded or not expected_sources.issubset(covered_sources):
            missing = sorted(expected_sources - covered_sources)
            mismatch = []
            if missing:
                mismatch.append("missing: " + ", ".join(missing))
            return _reject(
                "configured_task_source_coverage_required",
                "Before returning a plan, read and coverage-account for every configured task source "
                "from the durable manifest, then call personal_assistant_safety_review in this turn. "
                "Record unavailable or partial sources explicitly instead of omitting them. "
                + ("; ".join(mismatch) if mismatch else "No current-turn coverage receipt was recorded."),
            )
        source_statuses = {
            str(source.get("id") or "").strip(): str(source.get("status") or "").strip()
            for source in ((coverage_receipt or {}).get("sources") or [])
            if isinstance(source, Mapping) and str(source.get("id") or "").strip()
        }
        incomplete_sources = sorted(
            source_id
            for source_id in expected_sources
            if source_statuses.get(source_id) != "fresh"
        )
        if incomplete_sources:
            return _reject(
                "configured_task_source_coverage_incomplete",
                "Do not return recommendations from partial, stale, or unavailable configured task "
                "sources. Retry the affected reads once and record a fresh complete source receipt; "
                "if they remain unavailable, show one compact source-status action instead of a plan. "
                "Incomplete: " + ", ".join(incomplete_sources),
            )
        if isinstance(coverage_receipt, Mapping) and coverage_receipt.get("complete") is False:
            missing_items = [
                str(item_id).strip()
                for item_id in (coverage_receipt.get("missingItemIds") or [])
                if str(item_id).strip()
            ]
            return _reject(
                "protected_item_review_required",
                "The source reads are fresh, but the safety review is incomplete. Call "
                "flowstate_get_task for every missing protected item, then rerun "
                "personal_assistant_safety_review with those exact IDs in reviewedItemIds. "
                "Do not replace this with a capability audit or disclose IDs to the user. Missing: "
                + (", ".join(missing_items) if missing_items else "see the safety-review receipt"),
            )

    for artifact in artifacts:
        action_groups = [artifact.get("actions")]
        for collection_name in ("blocks", "rows", "items"):
            collection = artifact.get(collection_name)
            if isinstance(collection, list):
                action_groups.extend(
                    item.get("actions")
                    for item in collection
                    if isinstance(item, Mapping)
                )
        for actions in action_groups:
            if not isinstance(actions, list):
                continue
            for action in actions:
                if not isinstance(action, Mapping):
                    continue
                if len(str(action.get("label") or "").strip()) > 80:
                    return _reject(
                        "unrenderable_action_label",
                        "An action label exceeds Desktop's 80-character rendering limit. Use a short verb "
                        "such as Choose, Adjust, Start, or Details; keep task names and context in hidden "
                        "submitText rather than repeating them on the button.",
                    )

    if _THREE_DAY_OPTIONS_RE.search(str(user_message or "")):
        day_options = [
            artifact for artifact in artifacts if artifact.get("type") == "day-timeline"
        ]
        if len(day_options) == 3:
            has_block_actions = any(
                isinstance(block, Mapping) and bool(block.get("actions"))
                for artifact in day_options
                for block in (artifact.get("blocks") or [])
            )
            has_verbose_top_action = any(
                len(str(action.get("label") or "").strip()) > 32
                for artifact in day_options
                for action in (artifact.get("actions") or [])
                if isinstance(action, Mapping)
            )
            if has_block_actions or has_verbose_top_action:
                return _reject(
                    "compact_day_plan_options_required",
                    "Keep the three-plan comparison calm. Remove every per-block action and render only two "
                    "short top-level actions per timeline: Choose and Adjust. Do not repeat task names in "
                    "button labels; retain them only in submitText for exact routing.",
                )

    current_receipt_date = ""
    if isinstance(calendar_receipt, Mapping):
        receipt_range = calendar_receipt.get("range")
        if isinstance(receipt_range, Mapping):
            current_receipt_date = _normalized_planning_date(receipt_range.get("startDate"))
    current_interview_date = ""
    current_interview_active = False
    if isinstance(interview, Mapping):
        current_interview_active = str(interview.get("status") or "active") in {"active", "paused"}
        current_interview_date = _normalized_planning_date(interview.get("planningDate"))
        if not current_interview_date:
            source_snapshot = interview.get("sourceSnapshot")
            if isinstance(source_snapshot, Mapping):
                current_interview_date = _normalized_planning_date(source_snapshot.get("localDate"))
    interview_ready = bool(
        isinstance(interview, Mapping)
        and _interview_value(interview, "readinessApproved", "readiness_approved")
    )
    planning_recommendations_expected = (
        not current_interview_active
        or interview_ready
        or bool(
        current_interview_date
        and current_receipt_date
        and current_interview_date != current_receipt_date
        )
    )

    if planning_gate_required and planning_recommendations_expected:
        from agent.personal_assistant_calendar_gate import calendar_receipt_is_fresh_complete

        if not calendar_receipt_is_fresh_complete(
            None if calendar_receipt is _CALENDAR_RECEIPT_UNSET else calendar_receipt
        ):
            return _reject(
                "calendar_preflight_required",
                "Run a fresh complete personal_assistant_calendar_preflight for this planning "
                "request before returning recommendations.",
            )
        if task_inventory_complete is False:
            return _reject(
                "task_inventory_required",
                "First run personal_assistant_calendar_preflight for this planning turn, then read "
                "the complete fresh FlowState open-task inventory with flowstate_list_tasks "
                "mode=full. Do not rely on task or calendar data from conversation history.",
            )
        current_interview_matches_receipt = bool(
            current_interview_active
            and current_interview_date
            and current_receipt_date
            and current_interview_date == current_receipt_date
        )
        if planning_interview_required and not current_interview_matches_receipt:
            return _reject(
                "planning_interview_required",
                "Do not return recommendations yet. After the fresh Calendar and complete task-source reads, "
                "call personal_assistant_interview_start for the current planning date, reusing verified "
                "current-day facts so they are not asked again. Then render exactly one current interactive "
                "question. A prior-day interview or stale capacity cannot authorize today's plan.",
            )
        missing_canonical_task_reference = any(
            artifact.get("type") == "day-timeline"
            and any(
                isinstance(block, Mapping)
                and str(block.get("kind") or "focus") not in {"break", "buffer"}
                and not str(block.get("taskId") or "").strip()
                for block in (artifact.get("blocks") or [])
            )
            for artifact in artifacts
        )
        if missing_canonical_task_reference:
            return _reject(
                "canonical_task_reference_required",
                "Every executable day-timeline block must represent exactly one real task and carry "
                "that task's exact canonical taskId from the fresh inventory (or Calendar event ID "
                "for a fixed event). Never combine several tasks into one block. Use a separate block "
                "for each task, keep taskId hidden, and show only its exact task name in the label.",
            )
        recommendations = extract_personal_assistant_recommendations(text)
        calendar_events = calendar_receipt.get("events") or [] if isinstance(calendar_receipt, Mapping) else []
        calendar_event_ids = {
            str(event.get("id") or "").strip()
            for event in calendar_events
            if isinstance(event, Mapping) and event.get("id")
        }
        current_events = _calendar_events_in_progress(calendar_receipt)
        current_calendar_event_ids = {
            str(event.get("id") or "").strip() for event in current_events if event.get("id")
        }
        rest_of_day_occupied = bool(
            _REMAINING_TODAY_RE.search(str(user_message or ""))
            and any(_event_occupies_rest_of_day(event, calendar_receipt) for event in current_events)
        )
        honest_single_option = bool(
            rest_of_day_occupied
            and len(recommendations) == 1
            and recommendations[0]["taskId"] in current_calendar_event_ids
        )
        if (
            rest_of_day_occupied
            and any(item["taskId"] in current_calendar_event_ids for item in recommendations)
            and not honest_single_option
        ):
            return _reject(
                "rest_day_occupied_single_option_required",
                "A current calendar event occupies the entire rest of today. Return that exact "
                "event as the single honest option and state plainly that there are not three "
                "executable windows left today. Do not pad the answer with tomorrow's tasks or "
                "expired contingencies merely to reach three rows.",
            )
        if len(recommendations) < 3 and not honest_single_option:
            return _reject(
                "planning_recommendations_required",
                "Do not finish with a source-check status or prose-only acknowledgement. Return "
                "one supported hermes-ui planning artifact containing at least three distinct "
                "real task IDs from the exhaustive task read, ordered by priority, with concise "
                "fit reasons. Exception: when a current calendar event occupies the entire rest "
                "of today, return that exact event as the single honest option and explain that "
                "there are not three executable windows left.",
            )
        if task_inventory_ids is not None:
            authoritative_ids = set(task_inventory_ids) | calendar_event_ids
            if (
                isinstance(coverage_receipt, Mapping)
                and coverage_receipt.get("complete") is True
                and not (coverage_receipt.get("missingItemIds") or [])
            ):
                reviewed_ids = {
                    str(item_id or "").strip()
                    for item_id in (coverage_receipt.get("reviewedItemIds") or [])
                    if str(item_id or "").strip()
                }
                expected_ids = {
                    str(item_id or "").strip()
                    for item_id in (coverage_receipt.get("expectedItemIds") or [])
                    if str(item_id or "").strip()
                }
                if not expected_ids or expected_ids.issubset(reviewed_ids):
                    authoritative_ids.update(reviewed_ids)
            invented = [item["taskId"] for item in recommendations if item["taskId"] not in authoritative_ids]
            if invented:
                logger.info(
                    "Personal Assistant rejected non-authoritative recommendation IDs: invented=%s recommendations=%s authoritative_count=%s",
                    invented,
                    recommendations,
                    len(authoritative_ids),
                )
                return _reject(
                    "invented_task_recommendation",
                    "Every recommended taskId must exactly match a task ID from the complete "
                    "FlowState inventory or a Calendar event ID from this turn's complete receipt. "
                    "Remove invented activities and regenerate using only authoritative records.",
                )
        if task_details is not None and task_inventory_ids is not None:
            inventory_ids = set(task_inventory_ids)
            missing_details = [
                item["taskId"]
                for item in recommendations
                if item["taskId"] in inventory_ids and item["taskId"] not in task_details
            ]
            if missing_details:
                return _reject(
                    "task_details_required",
                    "Before final ranking, call flowstate_get_task for every shortlisted FlowState "
                    "task. Inspect its current instances, scheduled dates, duration, dependencies, "
                    "and status, then rank again from those fresh full records.",
                )
            for item in recommendations:
                detail = task_details.get(item["taskId"])
                if not isinstance(detail, Mapping):
                    continue
                instances = detail.get("instances")
                if not isinstance(instances, list):
                    continue
                active_dates = []
                for instance in instances:
                    if not isinstance(instance, Mapping):
                        continue
                    status = str(instance.get("status") or "").strip().lower()
                    if status in {"cancelled", "canceled", "completed", "deleted"}:
                        continue
                    scheduled_date = str(instance.get("scheduledDate") or "").strip()
                    try:
                        active_dates.append(datetime.fromisoformat(scheduled_date).date())
                    except ValueError:
                        continue
                try:
                    planning_date = datetime.fromisoformat(current_receipt_date).date()
                except ValueError:
                    planning_date = None
                if planning_date is not None and active_dates and min(active_dates) > planning_date:
                    return _reject(
                        "task_schedule_fidelity_required",
                        "A selected task is scheduled only after the day being planned. Do not move "
                        "it into today implicitly. Re-rank using tasks scheduled today, overdue, or "
                        "unscheduled; only propose early work when the response labels it explicitly "
                        "and gives a concrete reason grounded in the full task record.",
                    )
            actionable_task_ids: set[str] = set()
            task_name_action_ids: set[str] = set()
            richly_interactive_task_ids: set[str] = set()
            for artifact in artifacts:
                candidates: list[tuple[str, str, Any, Any]] = []
                artifact_type = artifact.get("type")
                global_actions = [
                    action
                    for action in (artifact.get("actions") or [])
                    if isinstance(action, Mapping)
                    and str(action.get("label") or "").strip()
                    and str(action.get("submitText") or "").strip()
                ]
                compact_table_interactions = (
                    artifact_type == "task-table"
                    and len(
                        {
                            str(action.get("submitText") or "").strip().casefold()
                            for action in global_actions
                        }
                    )
                    >= 2
                    and any(
                        _PLAN_ADJUSTMENT_RE.search(
                            f"{action.get('label') or ''} {action.get('submitText') or ''}"
                        )
                        for action in global_actions
                    )
                )
                if artifact_type == "task-table":
                    candidates.extend(
                        (
                            str(row.get("id") or "").strip(),
                            str(row.get("title") or "").strip(),
                            row.get("actions"),
                            {"title": row.get("title"), "cells": row.get("cells"), "actions": row.get("actions")},
                        )
                        for row in artifact.get("rows") or []
                        if isinstance(row, Mapping)
                    )
                elif artifact_type == "day-timeline":
                    timeline_actions = artifact.get("actions")
                    candidates.extend(
                        (
                            str(block.get("taskId") or "").strip(),
                            str(block.get("label") or "").strip(),
                            block.get("actions") or timeline_actions,
                            {
                                "label": block.get("label"),
                                "actions": block.get("actions") or timeline_actions,
                            },
                        )
                        for block in artifact.get("blocks") or []
                        if isinstance(block, Mapping)
                    )
                elif artifact_type == "week-planner":
                    for day in artifact.get("days") or []:
                        if not isinstance(day, Mapping):
                            continue
                        candidates.extend(
                            (
                                str(block.get("taskId") or "").strip(),
                                str(block.get("label") or "").strip(),
                                block.get("actions"),
                                {"label": block.get("label"), "actions": block.get("actions")},
                            )
                            for block in day.get("blocks") or []
                            if isinstance(block, Mapping)
                        )
                elif artifact_type == "mini-kanban":
                    for lane in artifact.get("lanes") or []:
                        if not isinstance(lane, Mapping):
                            continue
                        candidates.extend(
                            (
                                str(task.get("id") or "").strip(),
                                str(task.get("title") or "").strip(),
                                task.get("actions"),
                                {"title": task.get("title"), "actions": task.get("actions")},
                            )
                            for task in lane.get("tasks") or []
                            if isinstance(task, Mapping)
                        )
                for task_id, shown_title, actions, visible_payload in candidates:
                    source_detail = task_details.get(task_id)
                    source_title = (
                        str(source_detail.get("title") or "").strip()
                        if isinstance(source_detail, Mapping)
                        else shown_title
                    )
                    visible_text = json.dumps(visible_payload, ensure_ascii=False)
                    if len(task_id) >= 8 and task_id in visible_text:
                        return _reject(
                            "task_names_required",
                            "Use the task name everywhere the user can see or submit text. Keep the "
                            "task ID only in the artifact's hidden routing field (`id` or `taskId`); "
                            "never put it in columns, cells, titles, explanations, labels, or submitText.",
                        )
                    if isinstance(actions, list) and any(
                        isinstance(action, Mapping)
                        and str(action.get("label") or "").strip()
                        and str(action.get("submitText") or "").strip()
                        for action in actions
                    ):
                        actionable_task_ids.add(task_id)
                    if source_title and isinstance(actions, list) and any(
                        isinstance(action, Mapping)
                        and source_title.casefold()
                        in str(action.get("submitText") or "").casefold()
                        for action in actions
                    ):
                        task_name_action_ids.add(task_id)
                    valid_actions = [
                        action
                        for action in (actions or [])
                        if isinstance(action, Mapping)
                        and str(action.get("label") or "").strip()
                        and str(action.get("submitText") or "").strip()
                    ]
                    if (
                        source_title
                        and (
                            len(valid_actions) >= 2
                            or (compact_table_interactions and len(valid_actions) == 1)
                        )
                        and (
                            any(
                                source_title.casefold()
                                in str(action.get("label") or "").casefold()
                                for action in valid_actions
                            )
                            or (
                                artifact_type == "day-timeline"
                                and all(
                                    source_title.casefold()
                                    in str(action.get("submitText") or "").casefold()
                                    for action in valid_actions
                                )
                            )
                        )
                    ):
                        richly_interactive_task_ids.add(task_id)
            missing_actions = [
                item["taskId"]
                for item in recommendations
                if item["taskId"] in inventory_ids and item["taskId"] not in actionable_task_ids
            ]
            if missing_actions:
                return _reject(
                    "planning_actions_required",
                    "Make every recommended FlowState task directly useful. Add at least one visible "
                    "row or block action with a short label and submitText that uses the exact task "
                    "name, such as including, replacing, moving, or discussing it in the plan. Actions must route back "
                    "through Hermes so normal preview and approval rules still apply.",
                )
            missing_task_names = [
                item["taskId"]
                for item in recommendations
                if item["taskId"] in inventory_ids and item["taskId"] not in task_name_action_ids
            ]
            if missing_task_names:
                return _reject(
                    "task_names_required",
                    "Every action's submitText must use the exact current task name, never its "
                    "identifier. Keep IDs only in hidden routing fields so the user sees and sends "
                    "natural task names throughout the planning conversation.",
                )
            task_tables = [artifact for artifact in artifacts if artifact.get("type") == "task-table"]
            if any(len(artifact.get("columns") or []) > 3 for artifact in task_tables):
                return _reject(
                    "compact_daily_plan_required",
                    "This is a daily decision view, not an inventory report. Use no more than three "
                    "short visible columns and omit internal IDs, scoring evidence, duplicated due-date "
                    "details, and technical constraints. Keep only what changes the user's choice.",
                )
            if task_tables and not any(
                isinstance(actions, list)
                and any(
                    isinstance(action, Mapping)
                    and str(action.get("label") or "").strip()
                    and str(action.get("submitText") or "").strip()
                    and _PLAN_ADJUSTMENT_RE.search(
                        f"{action.get('label') or ''} {action.get('submitText') or ''}"
                    )
                    for action in actions
                )
                for actions in (artifact.get("actions") for artifact in task_tables)
            ):
                return _reject(
                    "planning_adjustment_required",
                    "The shortlist must support planning the whole day, not only starting one task. "
                    "Add a top-level task-table action that lets the user adjust time, energy, order, "
                    "or alternatives. Use natural task names in submitText and keep identifiers hidden.",
                )
            shallow_interactions = [
                item["taskId"]
                for item in recommendations
                if item["taskId"] in inventory_ids
                and item["taskId"] not in richly_interactive_task_ids
            ]
            if shallow_interactions:
                return _reject(
                    "planning_interactivity_required",
                    "A daily plan must support a real decision without repeating controls. Give each "
                    "recommended task one concise named choice action, then add two distinct whole-plan "
                    "actions for changing time or energy and showing alternatives. A richer timeline may "
                    "instead use two genuinely different task actions. Keep exact task names visible.",
                )
        if task_inventory_records is not None:
            planning_date = ""
            if isinstance(calendar_receipt, Mapping):
                covered_range = calendar_receipt.get("range")
                if isinstance(covered_range, Mapping):
                    planning_date = str(covered_range.get("startDate") or "").strip()
            scored_inventory = {
                task_id: _baseline_task_priority_score(record, planning_date)
                for task_id, record in task_inventory_records.items()
                if isinstance(record, Mapping)
            }
            strongest_score = max(scored_inventory.values(), default=0)
            first_task_id = next(
                (
                    item["taskId"]
                    for item in recommendations
                    if item["taskId"] in scored_inventory
                ),
                None,
            )
            if (
                strongest_score >= 65
                and first_task_id is not None
                and scored_inventory.get(first_task_id, 0) < strongest_score - 10
            ):
                return _reject(
                    "priority_ranking_required",
                    "The first recommendation ignores materially stronger authoritative urgency. "
                    "Re-rank the complete inventory so a high-priority task due by the planning "
                    "date is not displaced by a lower-priority overdue item without a visible "
                    "dependency or calendar reason. Then read every shortlisted task in full.",
                )
            for artifact in artifacts:
                if artifact.get("type") != "task-table":
                    continue
                for row in artifact.get("rows") or []:
                    if not isinstance(row, Mapping):
                        continue
                    record = task_inventory_records.get(str(row.get("id") or "").strip())
                    cells = row.get("cells")
                    if not isinstance(record, Mapping) or not isinstance(cells, Mapping):
                        continue
                    source_due = str(record.get("dueDate") or "").strip()
                    shown_due = str(cells.get("due") or "").strip()
                    if source_due and shown_due and source_due not in shown_due:
                        return _reject(
                            "task_source_fidelity_required",
                            "A recommended task's displayed due date disagrees with the fresh "
                            "FlowState inventory. Copy each title and due date from the current "
                            "inventory record exactly; do not replace a real date with 'no date'.",
                        )
                    duration_record = (
                        task_details.get(str(row.get("id") or "").strip())
                        if task_details is not None
                        else None
                    )
                    source_duration = _task_duration_minutes(
                        duration_record if isinstance(duration_record, Mapping) else record
                    )
                    visible_row = json.dumps(
                        {"title": row.get("title"), "cells": cells}, ensure_ascii=False
                    )
                    if (
                        isinstance(source_duration, int)
                        and not isinstance(source_duration, bool)
                        and source_duration > 0
                        and not re.search(rf"(?<!\d){source_duration}(?!\d)", visible_row)
                    ):
                        return _reject(
                            "task_duration_fidelity_required",
                            "A shortlisted task has a known canonical duration but the visible row omits it. "
                            "Show the verified duration or an honest range in the compact reason; do not make "
                            "the user open another view to understand whether it fits.",
                        )
        if (
            _REMAINING_TODAY_RE.search(str(user_message or ""))
            and calendar_event_ids
            and not any(item["taskId"] in calendar_event_ids for item in recommendations)
        ):
            event_titles = ", ".join(
                str(event.get("summary") or "calendar commitment").strip()
                for event in current_events
            )
            return _reject(
                "remaining_today_calendar_conflict_required",
                "A calendar commitment is in progress and must be represented as a real planning "
                f"constraint ({event_titles}). Include its exact calendar event ID as one option, "
                "then make any task options honest contingencies around that occupied time. Do not "
                "schedule work on top of the event.",
            )
        recommendation_timings = []
        recommendation_windows = []
        for artifact in artifacts:
            if artifact.get("type") != "task-table":
                continue
            for row in artifact.get("rows") or []:
                cells = row.get("cells") if isinstance(row, Mapping) else None
                if isinstance(cells, Mapping):
                    recommendation_timings.append(str(cells.get("timing") or "").strip())
                    recommendation_windows.append(
                        str(cells.get("window") or cells.get("timing") or "").strip()
                    )
        if (
            _REMAINING_TODAY_RE.search(str(user_message or ""))
            and any(
                _recommendation_window_already_elapsed(window, calendar_receipt)
                for window in recommendation_windows
            )
        ):
            return _reject(
                "remaining_today_window_elapsed",
                "One recommendation uses a time window that had already ended when the fresh "
                "calendar receipt was captured. Remove expired contingencies and regenerate the "
                "shortlist from the current local time. If a calendar event occupies the rest of "
                "today, say that plainly instead of inventing three executable task windows.",
            )
        if (
            _REMAINING_TODAY_RE.search(str(user_message or ""))
            and len(recommendation_timings) >= len(recommendations)
            and all(
                timing and _FUTURE_ONLY_TIMING_RE.search(timing)
                for timing in recommendation_timings
            )
        ):
            return _reject(
                "remaining_today_fit_required",
                "The user asked about the remaining time today, but every option is scheduled for "
                "tomorrow or later. Re-read the fresh calendar receipt and return three honest "
                "options that fit the time actually left today. Represent a current or upcoming "
                "calendar commitment instead of pretending there is free task time; use tomorrow "
                "only as a clearly labelled contingency.",
            )
        if _THREE_DAY_OPTIONS_RE.search(str(user_message or "")) and not honest_single_option:
            day_options = [
                artifact for artifact in artifacts if artifact.get("type") == "day-timeline"
            ]
            option_titles = {
                str(artifact.get("title") or "").strip().casefold()
                for artifact in day_options
                if str(artifact.get("title") or "").strip()
            }
            interactive_options = [
                artifact
                for artifact in day_options
                if len(
                    [
                        action
                        for action in (artifact.get("actions") or [])
                        if isinstance(action, Mapping)
                        and str(action.get("label") or "").strip()
                        and str(action.get("submitText") or "").strip()
                    ]
                )
                >= 2
            ]
            compact_option_tables = [
                artifact for artifact in artifacts if artifact.get("type") == "task-table"
            ]
            compact_plan_options = bool(
                len(compact_option_tables) == 1
                and len(compact_option_tables[0].get("rows") or []) == 3
                and len(
                    {
                        str(row.get("title") or "").strip().casefold()
                        for row in compact_option_tables[0].get("rows") or []
                        if isinstance(row, Mapping) and str(row.get("title") or "").strip()
                    }
                )
                == 3
                and all(
                    len(
                        [
                            action
                            for action in (row.get("actions") or [])
                            if isinstance(action, Mapping)
                            and str(action.get("label") or "").strip()
                            and str(action.get("submitText") or "").strip()
                        ]
                    )
                    >= 2
                    and any(
                        _PLAN_AROUND_TASK_RE.search(str(action.get("submitText") or ""))
                        for action in (row.get("actions") or [])
                        if isinstance(action, Mapping)
                    )
                    for row in compact_option_tables[0].get("rows") or []
                    if isinstance(row, Mapping)
                )
            )
            explicit_full_schedules = bool(
                _EXPLICIT_FULL_SCHEDULE_COMPARISON_RE.search(str(user_message or ""))
            )
            if not compact_plan_options and not explicit_full_schedules:
                return _reject(
                    "compact_day_plan_options_required",
                    "The user asked for three practical options, not three complete schedules. "
                    "Return one compact task-table with exactly three ranked real task options. "
                    "Each row must offer a plan-the-remaining-day-around-this action and one "
                    "distinct alternative. Add one short whole-day adjustment action and keep "
                    "internal IDs hidden.",
                )
            if not compact_plan_options and (
                len(day_options) != 3
                or len(option_titles) != 3
                or len(interactive_options) != 3
            ):
                return _reject(
                    "day_plan_options_required",
                    "The user explicitly asked to compare complete schedules. Return exactly "
                    "three concise day-timeline artifacts with distinct titles and two short "
                    "whole-plan actions each. Keep internal IDs hidden.",
                )

    if not isinstance(interview, Mapping):
        return OutputGateDecision(True)

    interview_active = str(interview.get("status") or "active") in {"active", "paused"}
    interview_date = _normalized_planning_date(interview.get("planningDate"))
    if not interview_date:
        source_snapshot = interview.get("sourceSnapshot")
        if isinstance(source_snapshot, Mapping):
            interview_date = _normalized_planning_date(source_snapshot.get("localDate"))
    if intent_action == "planning.query" and isinstance(calendar_receipt, Mapping):
        receipt_range = calendar_receipt.get("range")
        current_planning_date = (
            _normalized_planning_date(receipt_range.get("startDate"))
            if isinstance(receipt_range, Mapping)
            else ""
        )
        if interview_date and current_planning_date and interview_date != current_planning_date:
            interview_active = False
    if interview_active and interview_date:
        from datetime import date, timedelta

        dated_plan_ranges: list[tuple[date, date]] = []
        try:
            parsed_interview_date = date.fromisoformat(interview_date)
        except ValueError:
            parsed_interview_date = None
        for artifact in artifacts:
            artifact_type = artifact.get("type")
            raw_start = ""
            duration_days = 0
            if artifact_type == "day-timeline":
                raw_start = str(artifact.get("date") or "")
                duration_days = 1
            elif artifact_type == "week-planner":
                raw_start = str(artifact.get("weekStart") or "")
                duration_days = 7
            if not raw_start:
                continue
            try:
                parsed_start = date.fromisoformat(raw_start)
            except ValueError:
                continue
            dated_plan_ranges.append(
                (parsed_start, parsed_start + timedelta(days=duration_days))
            )
        if parsed_interview_date is not None and dated_plan_ranges:
            interview_active = any(
                start <= parsed_interview_date < end
                for start, end in dated_plan_ranges
            )
    readiness_approved = bool(
        _interview_value(interview, "readinessApproved", "readiness_approved")
    )
    if interview_active and not readiness_approved:
        for artifact in artifacts:
            if artifact.get("type") in _PLANNING_ARTIFACT_TYPES:
                return _reject(
                    "planning_interview_incomplete",
                    "Do not draft a plan yet. Return only one current task question as a "
                    "task-profile-review artifact matching the authoritative interview cursor.",
                )

        interaction_required = bool(
            str(intent_action or "").startswith("workflow.")
            or (
                intent_action == "planning.query"
                and planning_interview_required
            )
        )
        if interaction_required and not any(
            artifact.get("type") == "task-profile-review" for artifact in artifacts
        ):
            return _reject(
                "missing_current_interaction",
                "Return only one task-profile-review artifact for the authoritative current "
                "task and question. Do not answer this workflow action with prose or a plan.",
            )

        has_current_interaction = any(
            artifact.get("type") == "task-profile-review" for artifact in artifacts
        )
        question_count = len(_QUESTION_MARK_RE.findall(text))
        has_planning_table = bool(_MARKDOWN_TABLE_DIVIDER_RE.search(text))
        has_static_plan = bool(_STATIC_PLAN_HEADING_RE.search(text)) and len(
            _NUMBERED_ITEM_RE.findall(text)
        ) >= 2
        if not has_current_interaction and has_static_plan:
            return _reject(
                "static_plan_during_interview",
                "The planning interview is unfinished. Return only one current interactive "
                "task-profile-review question, not a static plan or priority list.",
            )
        if not has_current_interaction and (
            question_count >= 2 or (has_planning_table and question_count > 0)
        ):
            return _reject(
                "unrendered_interview_questions",
                "Return only one task-profile-review artifact for the authoritative current "
                "task and question. Do not ask multiple questions or present the planning "
                "review as prose or a Markdown table.",
            )

    expected_interview_id = str(
        _interview_value(interview, "interviewId", "interview_id") or ""
    )
    expected_revision = _interview_value(
        interview, "interviewRevision", "interview_revision", "revision"
    )
    expected_task_id = str(
        _interview_cursor_value(interview, "currentTaskId", "current_task_id") or ""
    )
    expected_question_id = str(
        _interview_cursor_value(
            interview, "currentQuestionId", "current_question_id"
        ) or ""
    )
    for artifact in artifacts:
        if artifact.get("type") != "task-profile-review":
            continue
        contract_error = _task_profile_review_contract_error(artifact)
        if contract_error:
            return _reject(
                "invalid_task_profile_review",
                "Return one complete renderable task-profile-review artifact using the "
                "canonical shared Desktop/Telegram contract. " + contract_error + ".",
            )
        task = artifact.get("task") if isinstance(artifact.get("task"), Mapping) else {}
        question = (
            artifact.get("question")
            if isinstance(artifact.get("question"), Mapping)
            else {}
        )
        projection = (
            str(artifact.get("interviewId") or ""),
            artifact.get("revision", artifact.get("interviewRevision")),
            str(task.get("id") or artifact.get("taskId") or ""),
            str(question.get("id") or artifact.get("questionId") or ""),
        )
        expected = (
            expected_interview_id,
            expected_revision,
            expected_task_id,
            expected_question_id,
        )
        if projection != expected:
            return _reject(
                "stale_interview_projection",
                "Refresh the interview state and render only its current task and question.",
            )

    return OutputGateDecision(True)


__all__ = [
    "OutputGateDecision",
    "build_safe_interview_fallback",
    "evaluate_personal_assistant_output",
]
