"""Plain-Hebrew projection for authorized Life-Boat technical deliveries."""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any


_TECHNICAL_MARKERS = re.compile(
    r"\b(?:commit|proc[_-]?\w+|pid\b|heartbeat|watchdog|worker|runtime|"
    r"canary|py_compile|pytest|installed|restart|reload|recovery|audit|receipt|"
    r"final|delivered|completed?)\b|/home/|/tmp/|\b(?:חסום|חסימה|אימות)\b",
    re.IGNORECASE,
)
_QUEUE_MARKERS = re.compile(r"\b(?:queued|queue|processing)\b|\b(?:תור|עיבוד)\b", re.IGNORECASE)
_TELEGRAM_MARKERS = re.compile(
    r"\b(?:telegram|authenticated telegram|telegram canary|verification)\b|"
    r"\b(?:אימות(?: אמיתי)?(?: ב-?Telegram)?)\b",
    re.IGNORECASE,
)
_RESTART_MARKERS = re.compile(r"\b(?:restart|restarted|reload|reloaded)\b|\b(?:הפעלה מחדש|טעינה מחדש)\b", re.IGNORECASE)
_BLOCKER_MARKERS = re.compile(
    r"\b(?:blocked|missing|open|remaining|requires?|need|unverified)\b|"
    r"\b(?:חסום|חסימה|חסר|נדרש|ממתין)\b",
    re.IGNORECASE,
)
_COMPLETED_MARKERS = re.compile(
    r"\b(?:green|passed|pass|complete|completed|installed|ready|matched|ok|"
    r"verified|final|delivered|recovery|canary)\b|\b(?:עבר|מוכן|הושלם|תקין|מאומת|נמסר)\b",
    re.IGNORECASE,
)
_LTR_TOKEN = re.compile(r"(?<![`\w])(?:https?://|/)[^\s)]+|\b(?:[A-Za-z][\w./:-]*\d[\w./:-]*|[A-Z][A-Za-z0-9_-]{2,})\b")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _isolated(value: str) -> str:
    return f"\u2066{value}\u2069"


def _render_section(label: str, lines: Iterable[str]) -> str:
    values = [_clean(line) for line in lines if _clean(line)]
    return "\n".join((label, *values)) if values else label


def render_lifeboat_technical_status(
    *,
    completed: Iterable[str] = (),
    blocked: Iterable[str] = (),
    next_steps: Iterable[str] = (),
    technical_detail: Iterable[str] = (),
) -> str:
    done = tuple(_clean(value) for value in completed if _clean(value))
    blocked_values = tuple(_clean(value) for value in blocked if _clean(value))
    upcoming = tuple(_clean(value) for value in next_steps if _clean(value))
    detail = tuple(_clean(value) for value in technical_detail if _clean(value))
    bottom_line = "נדרשת פעולה נוספת." if blocked_values else "ההכנה הושלמה ואין חסימה." if done else "הבדיקה עדיין מתקדמת."
    if done and any("Telegram" in value for value in done):
        bottom_line = "אימות Telegram הושלם; אין צורך בפעולה נוספת."
    sections = [
        "שורה תחתונה: " + bottom_line,
        _render_section("הושלם", done or ("אין עדיין השלמה לדווח.",)),
    ]
    if blocked_values:
        sections.append(_render_section("חסום", blocked_values))
    if upcoming:
        sections.append(_render_section("הצעד הבא", upcoming))
    if detail:
        sections.append(_render_section("פרטים טכניים", tuple(_isolated(value) for value in detail)))
    return "\n\n".join(sections)


def _technical_detail_lines(raw: str) -> tuple[str, ...]:
    return tuple(_isolated(match.group(0)) for match in _LTR_TOKEN.finditer(raw))


def format_lifeboat_technical_status(raw: str, *, technical_detail: bool = False) -> str | None:
    """Format only technical Life-Boat output; ordinary conversation is untouched."""
    text = _clean(raw)
    if not text or (not _TECHNICAL_MARKERS.search(text) and not _QUEUE_MARKERS.search(text)):
        return None

    has_queue = bool(_QUEUE_MARKERS.search(text))
    has_completed = bool(_COMPLETED_MARKERS.search(text))
    has_telegram = bool(_TELEGRAM_MARKERS.search(text))
    has_restart = bool(_RESTART_MARKERS.search(text))
    has_blocker = bool(_BLOCKER_MARKERS.search(text)) and not has_telegram
    if has_restart:
        has_blocker = True

    # Queue language is valid only when no final, completed, recovery, or
    # verification content shares the payload.
    if has_queue and not (has_completed or has_telegram or has_restart or has_blocker):
        return "התור נקלט; העיבוד יתחיל בקרוב."

    completed: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    if has_telegram and (has_completed or not has_restart):
        completed = ("אימות Telegram הושלם במסירת ההודעה הזו.",)
    elif has_completed:
        completed = ("הקוד והבדיקות הבסיסיות מוכנים.",)
    if has_blocker:
        blocked = ("נדרשת הפעלה מחדש בלבד.",) if has_restart else ("נדרשת בדיקה נוספת.",)
        next_steps = ("יש לבצע את הפעולה החסרה.",)
    else:
        blocked = ()
    detail = _technical_detail_lines(text) if technical_detail else ()
    return render_lifeboat_technical_status(
        completed=completed,
        blocked=blocked,
        next_steps=next_steps,
        technical_detail=detail,
    )
