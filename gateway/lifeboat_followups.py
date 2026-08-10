"""Small durable follow-up queue for the Life-Boat Telegram profile."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterator, Mapping
from zoneinfo import ZoneInfo

from gateway.config import GatewayConfig, Platform
from gateway.delivery import DeliveryRouter, DeliveryTarget
from gateway.lifeboat_psychology import (
    LifeBoatTrajectory,
    build_signal_guidance,
    classify_lifeboat_signals,
    record_lifeboat_trajectory,
    select_lifeboat_turn_policy,
)


FIRST_DELAY = timedelta(hours=2)
SECOND_DELAY = timedelta(days=1)
ACHIEVEMENT_DELAY = timedelta(days=2)
RETRY_DELAY = timedelta(minutes=10)
QUIET_START = 23
QUIET_END = 8
MAX_ATTEMPTS = 3
PROACTIVE_FOLLOWUPS_ENV = "LIFEBOAT_PROACTIVE_FOLLOWUPS"
JERUSALEM = ZoneInfo("Asia/Jerusalem")
_QUESTION_RE = re.compile(r"[?？]")
_REQUEST_RE = re.compile(
    r"(?:please|could you|can you|send me|tell me|choose|reply|let me know|"
    r"תשלח|תן לי|תגיד|בחר|ענה|תעדכן|אשמח לדעת)",
    re.IGNORECASE,
)
_ERROR_RE = re.compile(r"(?:error|failed|exception|שגיאה|נכשל)", re.IGNORECASE)
_WIN_RE = re.compile(
    r"(?:you (?:did|made|finished|completed|followed through|took a step|made progress)|"
    r"that(?:'s| is) (?:a|your) (?:win|milestone|progress)|כל הכבוד|הצלחת|התקדמת|סיימת)",
    re.IGNORECASE,
)
_HEBREW_RE = re.compile(r"[\u0590-\u05ff]")
_SENTENCE_RE = re.compile(r".+?(?:[.!?؟]|$)")
_CLOSURE_RE = re.compile(
    r"(?:לסיכום|מכאן ש|זה אומר ש|אין פלא|לא פלא|הדבר החשוב הוא|"
    r"in conclusion|this means|the important thing is|therefore)",
    re.IGNORECASE,
)
_WRAP_RE = re.compile(
    r"(?:תודה|זה עזר|נעצור|עוצר(?:ת)?|להיום|מספיק|לסיים|סיימנו|"
    r"thank(?:s| you)|that helped|stop here|enough for today|done for today|"
    r"wrap up|end here)",
    re.IGNORECASE,
)


def _state_path(profile_home: Path) -> Path:
    return Path(profile_home) / "state" / "lifeboat-followups.json"


@contextmanager
def _locked(profile_home: Path) -> Iterator[Path]:
    path = _state_path(profile_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "items": {}}
    return value if isinstance(value, dict) else {"version": 1, "items": {}}


def _save(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(path)


def proactive_followups_enabled() -> bool:
    """Delayed Life-Boat contact is opt-in; open-ended replies remain default."""
    return os.environ.get(PROACTIVE_FOLLOWUPS_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_lifeboat_source(source: Any) -> bool:
    """Recognize Life-Boat without depending on a particular process profile."""
    if getattr(getattr(source, "platform", None), "value", source.platform) != Platform.TELEGRAM.value:
        return False
    profile = str(getattr(source, "profile", "") or "").strip().lower()
    chat_id = str(getattr(source, "chat_id", "") or "").strip()
    thread_id = str(getattr(source, "thread_id", "") or "").strip()
    # Thread IDs are only unique inside one Telegram chat; never classify an
    # unrelated profile's topic 2 as the user's private support instance.
    return profile == "life-advisor" or (chat_id == "-1004230590253" and thread_id == "2")


def filter_lifeboat_toolsets(source: Any, toolsets: Any) -> list[str]:
    """Keep Telegram coaching turns non-blocking by removing interactive clarify."""
    values = [str(value) for value in (toolsets or [])]
    if not is_lifeboat_source(source):
        return values
    return [value for value in values if value != "clarify"]


def _language_and_context(response: str) -> tuple[str, str] | None:
    text = " ".join(str(response or "").split()).strip()
    if not text or len(text) > 2500 or _ERROR_RE.search(text):
        return None
    if not (_QUESTION_RE.search(text) or _REQUEST_RE.search(text)):
        return None
    language = "he" if len(_HEBREW_RE.findall(text)) >= 2 else "en"
    sentence = next((match.group(0).strip() for match in _SENTENCE_RE.finditer(text) if match.group(0).strip()), text)
    return language, sentence[:220].rstrip()


def _followup_text(response: str) -> str | None:
    result = _language_and_context(response)
    return result[1] if result is not None else None


def lifeboat_response_issues(response: str, user_text: str = "") -> tuple[str, ...]:
    """Find only high-confidence failures of the Life-Boat reply contract."""
    text = " ".join(str(response or "").split()).strip()
    if not text:
        return ("empty",)
    policy = select_lifeboat_turn_policy(user_text)
    issues: list[str] = []
    if len(text) > policy.max_chars:
        issues.append("too_long")
    sentence_count = len([part for part in _SENTENCE_RE.findall(text) if part.strip()])
    if sentence_count > policy.max_sentences:
        issues.append("too_many_sentences")
    question_count = len(_QUESTION_RE.findall(text))
    if question_count > 1:
        issues.append("too_many_questions")
    has_open_door = bool(
        _QUESTION_RE.search(text)
        or re.search(r"מעניין אם|איך זה פוגש|what happens next|I wonder if|notice what", text, re.IGNORECASE)
    )
    if policy.ask_one_open_question and not has_open_door:
        issues.append("closed")
    if _CLOSURE_RE.search(text) and not has_open_door:
        issues.append("premature_conclusion")
    return tuple(issues)


def _lifeboat_open_door(user_text: str) -> str:
    signals = classify_lifeboat_signals(user_text)
    if signals.possible_crisis:
        return "אתה בטוח שאתה בטוח עכשיו, או שיש סכנה שתפעל על זה?"
    if signals.thought_loop:
        return "מה הלופ הזה מנסה לפתור או למנוע כרגע?"
    if signals.self_criticism:
        return "איזה סטנדרט או פחד יושב מתחת למשפט הזה על עצמך?"
    if signals.depressive_thoughts:
        return "מה הכי כבד או הכי נוכח אצלך עכשיו?"
    if select_lifeboat_turn_policy(user_text).mode == "act-or-clarify":
        return "רוצה שנחשוב על צעד אחד קטן, או שעדיף להישאר רגע עם מה שזה מעורר?"
    return "מה הכי חי אצלך עכשיו, אם בכלל?"


def ensure_lifeboat_open_response(response: str, user_text: str = "") -> str:
    """Bound a failed draft without inventing a summary or a support menu."""
    text = " ".join(str(response or "").split()).strip()
    issues = lifeboat_response_issues(text, user_text)
    if not issues:
        return text
    policy = select_lifeboat_turn_policy(user_text)
    if policy.mode == "user-led-close":
        return text[: policy.max_chars].rstrip()
    sentences = [part.strip() for part in _SENTENCE_RE.findall(text) if part.strip()]
    usable_sentences = [part for part in sentences if not _CLOSURE_RE.search(part)]
    base = " ".join(usable_sentences[: max(1, policy.max_sentences - 1)]).strip()
    if not base:
        base = "אני איתך בזה לרגע."
    door = _lifeboat_open_door(user_text)
    budget = max(120, policy.max_chars - len(door) - 2)
    return f"{base[:budget].rstrip()}\n\n{door}"


def _now(value: datetime | None = None) -> datetime:
    result = value or datetime.now(timezone.utc)
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return _now(value).isoformat()


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def cancel_followup(profile_home: Path, session_key: str) -> bool:
    with _locked(profile_home) as path:
        state = _load(path)
        items = state.setdefault("items", {})
        removed = items.pop(str(session_key), None) is not None
        removed = items.pop(f"achievement:{session_key}", None) is not None or removed
        if removed:
            _save(path, state)
        return removed


def consume_followup_context(profile_home: Path, session_key: str) -> dict[str, str] | None:
    """Consume a pending reminder and return its bounded context for the next turn."""
    with _locked(profile_home) as path:
        state = _load(path)
        items = state.setdefault("items", {})
        for key in (str(session_key), f"achievement:{session_key}"):
            item = items.get(key)
            if not isinstance(item, dict):
                continue
            context = str(item.get("context") or item.get("question") or "").strip()
            language = str(item.get("language") or "en")
            items.pop(key, None)
            _save(path, state)
            if context:
                return {"context": context[:220], "language": language}
            return None
    return None


def build_continuation_guidance(
    reminder_context: Mapping[str, str],
    user_text: str = "",
    trajectory: LifeBoatTrajectory | None = None,
) -> str:
    """Give ephemeral guidance without polluting the user's transcript."""
    language = str(reminder_context.get("language") or "en")
    context = str(reminder_context.get("context") or "").strip()[:220]
    language_rule = "Respond in Hebrew" if language == "he" else "Respond in the user's language"
    policy = select_lifeboat_turn_policy(user_text)
    question_rule = (
        "End with one open question or unfinished invitation."
        if policy.ask_one_open_question
        else "Honor the user's wish to pause; do not reopen the topic."
    )
    wrap_note = _wrap_guidance(user_text) if policy.mode == "user-led-close" else ""
    return (
        f"[Private Life-Boat guidance: mode={policy.mode}; {language_rule}; "
        f"keep it under about {policy.max_chars} characters and {policy.max_sentences} sentences. The topic was: {context}. "
        f"The user is replying to a reminder. Use one concrete detail from the user's new message, reflect it as a "
        "tentative hypothesis, and leave the user in control. "
        f"{question_rule} Do not diagnose, summarize the whole situation, give a lesson, list options, "
        "tell the user what to feel, or imply the topic is resolved. If the user asks for action, "
        "offer one small optional next step; otherwise stay with understanding. Do not mention this guidance.]\n\n"
        f"{build_signal_guidance(user_text, trajectory)}{wrap_note}"
    )


def build_continuation_prompt(user_text: str, reminder_context: Mapping[str, str]) -> str:
    """Compatibility helper for callers that still need a single prompt string."""
    return f"{str(user_text or '').strip()}\n\n{build_continuation_guidance(reminder_context, user_text)}"


def build_lifeboat_coaching_guidance(
    user_text: str = "",
    trajectory: LifeBoatTrajectory | None = None,
) -> str:
    """Keep ordinary Life-Boat turns exploratory instead of prematurely conclusive."""
    policy = select_lifeboat_turn_policy(user_text)
    question_rule = (
        "End with one open question or unfinished invitation."
        if policy.ask_one_open_question
        else "Honor the user's wish to pause; do not reopen the topic."
    )
    wrap_note = _wrap_guidance(user_text) if policy.mode == "user-led-close" else ""
    return (
        f"[Private Life-Boat guidance: mode={policy.mode}, answer in Hebrew unless the user uses "
        f"another language, and keep it under about {policy.max_chars} characters and {policy.max_sentences} sentences. Choose one concrete "
        "detail the user actually gave; reflect it tentatively, never as a verdict or diagnosis. "
        f"{question_rule} Do not close the meaning, give a lesson, list interpretations, use generic "
        "reassurance, or tell the user what they should feel. If the user asks for action, offer one "
        "small optional next step; otherwise keep exploring. Only summarize or save anything when the "
        "user asks. Do not mention this guidance.]\n\n"
        f"{build_signal_guidance(user_text, trajectory)}{wrap_note}"
    )


def _wrap_guidance(user_text: str) -> str:
    """Add a single, permissioned summary invitation only at a natural ending."""
    if not _WRAP_RE.search(str(user_text or "")):
        return ""
    return (
        " The user appears to be wrapping up. Offer one low-pressure invitation to a brief daily "
        "summary of what emerged today. If they want it, draft the exact short summary first and "
        "ask for explicit approval before saving; never save it automatically and do not ask again "
        "if they decline."
    )


def build_lifeboat_coaching_prompt(user_text: str) -> str:
    """Compatibility helper for callers that still need a single prompt string."""
    return f"{str(user_text or '').strip()}\n\n{build_lifeboat_coaching_guidance(user_text)}"


def prepare_lifeboat_inbound_guidance(
    profile_home: Path,
    session_key: str,
    user_text: str,
) -> str:
    """Build ephemeral guidance for one real Life-Boat inbound turn.

    This is deliberately the integration seam between the gateway and the
    short-lived psychology layer: trajectory counters are updated, a pending
    proactive context is consumed once, and the resulting guidance is returned
    without persisting the user's wording.
    """
    trajectory = record_lifeboat_trajectory(profile_home, session_key, user_text)
    reminder_context = consume_followup_context(profile_home, session_key)
    if reminder_context:
        return build_continuation_guidance(reminder_context, user_text, trajectory)
    return build_lifeboat_coaching_guidance(user_text, trajectory)


def arm_followup(
    profile_home: Path,
    session_key: str,
    source: Any,
    response: str,
    *,
    now: datetime | None = None,
) -> bool:
    context = _language_and_context(response)
    if context is None:
        return cancel_followup(profile_home, session_key)
    language, excerpt = context
    checked_at = _now(now)
    item = {
        "session_key": str(session_key),
        "chat_id": str(getattr(source, "chat_id", "") or ""),
        "thread_id": getattr(source, "thread_id", None),
        "context": excerpt,
        "language": language,
        "stage": 0,
        "attempts": 0,
        "due_at": _iso(checked_at + FIRST_DELAY),
        "last_response_at": _iso(checked_at),
    }
    with _locked(profile_home) as path:
        state = _load(path)
        state.setdefault("items", {})[str(session_key)] = item
        _save(path, state)
    return True


def arm_achievement_prompt(
    profile_home: Path,
    session_key: str,
    source: Any,
    response: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Schedule a rare, opt-in nudge after a clearly positive moment."""
    text = " ".join(str(response or "").split()).strip()
    if not text or len(text) > 2500 or _ERROR_RE.search(text) or not _WIN_RE.search(text):
        return False
    checked_at = _now(now)
    language = "he" if len(_HEBREW_RE.findall(text)) >= 2 else "en"
    sentence = next((match.group(0).strip() for match in _SENTENCE_RE.finditer(text) if match.group(0).strip()), text)
    key = f"achievement:{session_key}"
    with _locked(profile_home) as path:
        state = _load(path)
        items = state.setdefault("items", {})
        if str(session_key) in items or key in items:
            return False
        last = _parse(state.get("achievement_last_suggested_at"))
        if last is not None and checked_at - last < ACHIEVEMENT_DELAY:
            return False
        items[key] = {
            "kind": "achievement",
            "session_key": str(session_key),
            "chat_id": str(getattr(source, "chat_id", "") or ""),
            "thread_id": getattr(source, "thread_id", None),
            "context": sentence[:220].rstrip(),
            "language": language,
            "due_at": _iso(checked_at + ACHIEVEMENT_DELAY),
            "attempts": 0,
        }
        state["achievement_last_suggested_at"] = _iso(checked_at)
        _save(path, state)
    return True


def arm_lifeboat_prompts(
    profile_home: Path,
    session_key: str,
    source: Any,
    response: str,
    *,
    user_text: str = "",
    now: datetime | None = None,
) -> dict[str, bool]:
    """Arm all proactive Life-Boat prompts and expose their outcomes for tests/logs."""
    if not is_lifeboat_source(source):
        return {"followup": False, "achievement": False}
    if not proactive_followups_enabled():
        # A normal answer may leave an open door, but it must not create a
        # future demand on the user. The separate, user-enabled morning cron
        # remains available without enabling these delayed nudges.
        return {"followup": False, "achievement": False}
    if classify_lifeboat_signals(user_text).possible_crisis:
        # A safety-sensitive turn must not fall back into a routine reminder
        # queue; any further contact should be an explicit safety decision.
        return {"followup": False, "achievement": False}
    return {
        "followup": arm_followup(profile_home, session_key, source, response, now=now),
        "achievement": arm_achievement_prompt(profile_home, session_key, source, response, now=now),
    }


def _quiet_hours(now: datetime) -> bool:
    hour = _now(now).astimezone(JERUSALEM).hour
    return hour >= QUIET_START or hour < QUIET_END


def _claim_due(profile_home: Path, *, now: datetime | None = None) -> dict[str, Any] | None:
    checked_at = _now(now)
    if _quiet_hours(checked_at):
        return None
    with _locked(profile_home) as path:
        state = _load(path)
        items = state.setdefault("items", {})
        for key, item in items.items():
            if not isinstance(item, dict):
                continue
            due_at = _parse(item.get("due_at"))
            if due_at is None or due_at > checked_at:
                continue
            if int(item.get("attempts", 0)) >= MAX_ATTEMPTS:
                items.pop(key, None)
                _save(path, state)
                return None
            item["attempts"] = int(item.get("attempts", 0)) + 1
            item["claimed_at"] = _iso(checked_at)
            _save(path, state)
            return dict(item)
    return None


def _delivery_message(item: Mapping[str, Any]) -> str:
    language = str(item.get("language") or "en")
    context = str(item.get("context") or item.get("question") or "").strip()
    if item.get("kind") == "achievement":
        if language == "he":
            return "זה נשמע כמו הישג קטן ששווה לשמור. רוצה להוסיף אותו לרשימת ההישגים שלך? אפשר גם לתת לו שם ביחד — רק אם מתאים לך."
        return (
            "That sounds like a real win worth keeping. Would you like to add it to your "
            "achievements list? I can help give it a name — only if you want to."
        )
    if language == "he":
        if int(item.get("stage", 0)) == 0:
            return f"רק חוזר/ת לזה בעדינות — רוצה להמשיך מכאן?\n\n{context}"
        return f"אפשר לחזור לזה מתי שנוח לך. רוצה להמשיך מכאן?\n\n{context}"
    if int(item.get("stage", 0)) == 0:
        return f"Just checking in — would you like to continue from here?\n\n{context}"
    return f"Happy to pick this back up whenever you’re ready. Want to continue from here?\n\n{context}"


def _finish_claim(profile_home: Path, item: Mapping[str, Any], *, delivered: bool, now: datetime) -> None:
    session_key = str(item.get("session_key") or "")
    key = f"achievement:{session_key}" if item.get("kind") == "achievement" else session_key
    with _locked(profile_home) as path:
        state = _load(path)
        current = state.setdefault("items", {}).get(key)
        if not isinstance(current, dict):
            return
        if not delivered:
            current["due_at"] = _iso(_now(now) + RETRY_DELAY)
            _save(path, state)
            return
        if current.get("kind") == "achievement":
            state["items"].pop(key, None)
        elif int(current.get("stage", 0)) == 0:
            current["stage"] = 1
            current["attempts"] = 0
            current["due_at"] = _iso(_now(now) + SECOND_DELAY)
            current.pop("claimed_at", None)
        else:
            state["items"].pop(key, None)
        _save(path, state)


class LifeBoatFollowupBridge:
    def __init__(self, profile_home: Path, delivery_router: DeliveryRouter, *, poll_interval: float = 30.0) -> None:
        self.profile_home = Path(profile_home)
        self.delivery_router = delivery_router
        self.poll_interval = max(5.0, float(poll_interval))

    async def deliver_once(self, *, now: datetime | None = None) -> bool:
        if not proactive_followups_enabled():
            # Drop stale queues created before the opt-in boundary so an
            # upgrade cannot suddenly replay old emotional prompts.
            with _locked(self.profile_home) as path:
                state = _load(path)
                if state.get("items"):
                    state["items"] = {}
                    _save(path, state)
            return False
        item = _claim_due(self.profile_home, now=now)
        if item is None:
            return False
        target = DeliveryTarget(
            platform=Platform.TELEGRAM,
            chat_id=str(item.get("chat_id") or ""),
            thread_id=str(item["thread_id"]) if item.get("thread_id") is not None else None,
            is_explicit=True,
        )
        result = await self.delivery_router.deliver(
            _delivery_message(item),
            [target],
            metadata={"lifeboat_followup": True, "session_key": item.get("session_key")},
        )
        delivered = bool(result.get(target.to_string(), {}).get("success"))
        _finish_claim(self.profile_home, item, delivered=delivered, now=_now(now))
        return delivered

    async def run(self, keep_running: Callable[[], bool]) -> None:
        while keep_running():
            try:
                await self.deliver_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A follow-up must never take down the gateway.
                pass
            await asyncio.sleep(self.poll_interval)
