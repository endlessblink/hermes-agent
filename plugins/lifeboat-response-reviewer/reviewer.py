"""Fast, profile-scoped, fail-open review of emotional coaching replies.

The detector is deliberately local and conservative.  It never persists the
user message; ``pre_llm_call`` stores only a short-lived boolean scope marker.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, Optional


PLUGIN_ID = "lifeboat-response-reviewer"
PROFILE = "life-advisor"
DEFAULT_TIMEOUT = 0.8
MAX_TIMEOUT = 2.0
SCOPE_TTL = 180.0


@dataclass(frozen=True)
class RiskReport:
    flagged: bool
    reasons: tuple[str, ...] = ()
    scores: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Settings:
    enabled: bool = True
    timeout: float = DEFAULT_TIMEOUT
    dry_run: bool = False
    reviewer_mode: str = "rewrite"

    @classmethod
    def from_host(cls) -> "Settings":
        try:
            from hermes_cli.config import load_config

            cfg = load_config() or {}
            entry = ((cfg.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) or {}
            raw = entry.get("reviewer") if isinstance(entry, dict) else {}
            raw = raw if isinstance(raw, dict) else {}
            timeout = float(raw.get("timeout", DEFAULT_TIMEOUT))
            return cls(
                enabled=bool(raw.get("enabled", True)),
                timeout=max(0.05, min(timeout, MAX_TIMEOUT)),
                dry_run=bool(raw.get("dry_run", False)),
                reviewer_mode=str(raw.get("reviewer_mode", "rewrite")).lower(),
            )
        except Exception:
            return cls()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _has_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


_NEGATIVE = (
    "מבאס", "מתסכל", "עלוב", "דוחה", "דחוי", "שונא", "שנאה", "בודד", "בושה",
    "כואב", "קשה", "לא שווה", "לא רצוי", "מביך", "נכשל", "נכשלתי", "מרירות",
    "disappoint", "rejected", "lonely", "ashamed", "worthless", "hurt", "pain",
    "embarrass", "bitter", "failure",
)
_EMOTIONAL_SCOPE = (
    *_NEGATIVE,
    "רגש", "ערך עצמי", "הערך שלי", "הבוליון", "בולי", "bully", "self-worth",
    "self worth", "dating", "דייט", "דייטינג", "קנאה", "מקנאה", "rejection",
    "relationship", "יחסים", "loneliness", "בדידות", "what does this say about me",
    "מה זה אומר עליי", "עבודה רגשית", "לעבד", "לעיבוד", "תעזור לי להבין",
)
_OPERATIONAL_ONLY = (
    "מיפוי", "מפה", "משימה", "פרויקט", "קוד", "קובץ", "תיקייה", "flowstate", "api",
    "mapping", "task", "project", "code", "file", "folder", "calendar", "schedule",
)


def is_emotional_scope(user_message: str, profile_name: str) -> bool:
    """Return true only for life-advisor emotional/meta-coaching turns."""
    if profile_name != PROFILE:
        return False
    text = _normalize(user_message)
    if not text or not _has_any(text, _EMOTIONAL_SCOPE):
        return False
    # A practical request that merely mentions a project is not coaching.
    if _has_any(text, _OPERATIONAL_ONLY) and not _has_any(text, _NEGATIVE + ("ערך עצמי", "רגש", "emotion")):
        return False
    return True


_CLOSURE = re.compile(
    r"(?:אולי יש בך|זה אומר ש|מכאן|בסופו של דבר|המשמעות היא|יש כאן|לכן אתה|אתה יכול לראות|"
    r"this shows|what this means|in the end|you are showing|you can see that).{0,100}"
    r"(?:נכונות|אומץ|צמיחה|הזדמנות|גדילה|progress|growth|resilience|strength)", re.I,
)
_REASSURANCE = re.compile(
    r"(?:זה לא אומר ש|זה לא אומר שאתה|הכול יהיה בסדר|הכל יהיה בסדר|אתה בסדר|אין סיבה לדאוג|"
    r"you(?:'re| are) okay|everything will be okay|you are not broken|it will be fine|nothing is wrong with you)", re.I,
)
_PRAISE = re.compile(
    r"(?:כל הכבוד|אני גאה בך|אמיץ|אמיצה|בוגר|בוגרת|נכונות לתת לעצמך|צמיחה|גדילה|"
    r"good job|proud of you|brave|courageous|growth|resilient|you should be proud)", re.I,
)
_AFFECT_MIRROR = re.compile(
    r"(?:נשמע|זה נשמע|זה באמת|ממש)\s+(?:מבאס|מתסכל|כואב|עלוב|מביך|קשה|דוחה)|"
    r"(?:that sounds|it sounds|that must be)\s+(?:disappointing|painful|awful|lonely|humiliating|hard)", re.I,
)
_BINARY = re.compile(
    r"(?:האם|רוצה|להמשיך|תרצה|do you want|would you rather|is it|האם זה).{0,80}\?",
    re.I,
)
_RECEIPT = re.compile(
    r"(?:עדכנתי|שמתי לב|שמרתי|תיעדתי|הוספתי|הכלל עודכן|הזיכרון עודכן|הוספתי לתור|"
    r"updated the rule|saved|noted|added to the queue|memory updated)", re.I,
)


def classify(text: str) -> RiskReport:
    """Classify final text in one bounded pass; no I/O, model, or state."""
    normalized = _normalize(text)
    if not normalized:
        return RiskReport(False)
    scores: Dict[str, int] = {}
    if _CLOSURE.search(normalized):
        scores["polished_closure"] = 1
    if _AFFECT_MIRROR.search(normalized) and _has_any(normalized, _NEGATIVE):
        scores["amplified_negative_affect"] = 1
    if _REASSURANCE.search(normalized):
        scores["unsolicited_reassurance"] = 1
    if _PRAISE.search(normalized):
        scores["praise_growth_assignment"] = 1
    if _BINARY.search(normalized) and re.search(r"\b(?:כן|לא|yes|no|או|or)\b", normalized, re.I):
        scores["forced_binary_question"] = 1
    # A receipt is risky only when it is attached to emotional material;
    # ordinary project/status language must remain outside this classifier.
    if (
        _RECEIPT.search(normalized)
        and len(normalized) < 260
        and (_has_any(normalized, _NEGATIVE) or _has_any(normalized, ("רגש", "עבודה רגשית", "self-worth", "bully")))
    ):
        scores["operational_receipt"] = 1
    return RiskReport(bool(scores), tuple(scores), scores)


def _live_surface(text: str) -> bool:
    """Reject obvious model/meta wrappers and empty rewrites."""
    lowered = _normalize(text)
    return bool(lowered) and not lowered.startswith(("here is the revised", "revised response:", "sure, here"))


class Reviewer:
    def __init__(self, llm_factory: Optional[Callable[[], Any]] = None) -> None:
        self._llm_factory = llm_factory
        self._scopes: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._local = threading.local()

    def mark_scope(self, session_id: str, relevant: bool) -> None:
        if not session_id:
            return
        with self._lock:
            if relevant:
                self._scopes[session_id] = time.monotonic() + SCOPE_TTL
            else:
                self._scopes.pop(session_id, None)

    def _in_scope(self, session_id: str) -> bool:
        with self._lock:
            expiry = self._scopes.get(session_id, 0.0)
            if expiry <= time.monotonic():
                self._scopes.pop(session_id, None)
                return False
            return True

    def transform(self, text: str, *, session_id: str, profile_name: str, settings: Settings) -> str:
        if not settings.enabled or settings.reviewer_mode not in {"rewrite", "review"}:
            return ""
        if profile_name != PROFILE or not self._in_scope(session_id):
            return ""
        report = classify(text)
        if not report.flagged or settings.dry_run:
            return ""
        if getattr(self._local, "depth", 0):
            return ""
        if self._llm_factory is None:
            return ""
        self._local.depth = 1
        try:
            result = self._llm_factory().complete(
                messages=[
                    {"role": "system", "content": (
                        "You are a strict final-response editor for a Hebrew/English life-advisor. "
                        "Rewrite only when needed. Preserve factual content, language, brevity, agency, "
                        "and a live reply surface. Remove polished closure, amplified negative mirroring, "
                        "unasked reassurance, praise/growth assignments, forced binary questions, and "
                        "operational receipts. Do not add therapy jargon, advice, praise, reassurance, or "
                        "questions unless one concrete question is necessary. Return only the reply text."
                    )},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
                max_tokens=max(80, min(300, len(text) // 2 + 80)),
                timeout=settings.timeout,
                purpose="lifeboat_response_review",
            )
            candidate = getattr(result, "text", "")
            return candidate if isinstance(candidate, str) and _live_surface(candidate) else ""
        except Exception:
            return ""
        finally:
            self._local.depth = 0


_INSTANCE: Optional[Reviewer] = None
_PROFILE_NAME = ""
_SETTINGS = Settings()


def register(ctx: Any) -> None:
    global _INSTANCE, _PROFILE_NAME, _SETTINGS
    _PROFILE_NAME = str(ctx.profile_name)
    _SETTINGS = Settings.from_host()
    _INSTANCE = Reviewer(lambda: ctx.llm)

    def on_pre_llm_call(**kwargs: Any) -> None:
        if _INSTANCE is None:
            return
        _INSTANCE.mark_scope(
            str(kwargs.get("session_id") or ""),
            is_emotional_scope(str(kwargs.get("user_message") or ""), _PROFILE_NAME),
        )

    def on_transform(**kwargs: Any) -> str:
        if _INSTANCE is None:
            return ""
        return _INSTANCE.transform(
            str(kwargs.get("response_text") or ""),
            session_id=str(kwargs.get("session_id") or ""),
            profile_name=_PROFILE_NAME,
            settings=_SETTINGS,
        )

    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("transform_llm_output", on_transform)
