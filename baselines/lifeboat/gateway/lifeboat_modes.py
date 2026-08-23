"""Which kind of conversation Life-Boat is currently having.

Life-Boat is used for emotional support, for time management, and for hands-on
work, and it has to handle safety separately from all three. Those need
different replies: support stays short and open, work is allowed to be a
checklist, and a coaching question appended to a bug report is noise. The mode
is decided here, once, and the reply contract follows from it.

Switching is sticky on purpose. A single ambiguous sentence must not flip the
conversation, so an inferred change needs two consecutive messages agreeing.
Anything the user says explicitly wins immediately, and a safety signal
overrides everything.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from gateway.lifeboat_psychology import classify_lifeboat_signals


SUPPORT = "support"
TIME = "time"
WORK = "work"
CRISIS = "crisis"
PAUSED = "paused"

MODES = (SUPPORT, TIME, WORK, CRISIS, PAUSED)

#: Modes in which Life-Boat may start a conversation the user did not.
_PROACTIVE_MODES = frozenset({SUPPORT, TIME})

#: How many consecutive messages must agree before an inferred switch happens.
_SWITCH_STREAK = 2

_COMMANDS = {
    "/support": SUPPORT,
    "/time": TIME,
    "/work": WORK,
    "/pause": PAUSED,
    "/stop": PAUSED,
}

_EXPLICIT_RE = (
    (WORK, re.compile(r"(?:מצב עבודה|נעבור לעבודה|בוא נעבוד|switch to work|work mode)", re.I)),
    (TIME, re.compile(r"(?:מצב תכנון|נתכנן|בוא נתכנן|planning mode|time mode)", re.I)),
    # A lane switch: stop the technical track. Deliberately not a topic
    # selection -- see BUG-21. Resuming queued work says so in its own words
    # ("נמשיך מאיפה שעצרנו") and is left to the ordinary inference path.
    (SUPPORT, re.compile(
        r"(?:מצב תמיכה|בוא נדבר|support mode|let's talk"
        r"|(?:ל?חזור|לעבור|נעבור|נחזור)\s+(?:חזרה\s+)?ל(?:אפיק\s+ה)?רגשי"
        r"|back to (?:the )?emotional(?: track| lane)?"
        r"|יש (?:לי )?דברים חדשים)",
        re.I,
    )),
    (PAUSED, re.compile(r"(?:עצור לרגע|תפסיק|נעצור להיום|pause for now|stop for today)", re.I)),
)

_ALL_CLEAR_RE = re.compile(
    r"(?:אני בסדר(?: עכשיו)?|זה עבר|אני בטוח|לא בסכנה|"
    r"i'?m ok(?:ay)? now|it passed|i'?m safe|not in danger)",
    re.I,
)

_WORK_RE = re.compile(
    r"(?:באג|תקלה|קוד|טסט|בדיק|לתקן|שגיא|קומיט|דיפלוי|לוג|"
    r"\bbug\b|\bfix\b|\bcode\b|\btest\b|\bdeploy\b|\bcommit\b|\berror\b|\blog\b|\bpatch\b)",
    re.I,
)

_TIME_RE = re.compile(
    r"(?:לתכנן|תכנון|יומן|פגיש|לו\"ז|לוח זמנים|דדליין|סדר יום|להספיק|"
    r"\bschedule\b|\bcalendar\b|\bplan(?:ning)?\b|\bdeadline\b|\bmeeting\b|\bagenda\b)",
    re.I,
)

_SUPPORT_RE = re.compile(
    r"(?:מרגיש|מרגישה|כבד|עצוב|חרד|לבד|קשה לי|נמאס|מתוסכל|פוחד|כואב|יושב עלי|"
    r"\bfeel\b|\bfeeling\b|\blonely\b|\banxious\b|\bsad\b|\bhurts?\b|\btired of\b)",
    re.I,
)


@dataclass(frozen=True)
class ModeState:
    """The conversation's mode plus the evidence for a pending change."""

    mode: str = SUPPORT
    candidate: str | None = None
    candidate_streak: int = 0

    @property
    def proactive_allowed(self) -> bool:
        return self.mode in _PROACTIVE_MODES

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "candidate": self.candidate,
            "candidate_streak": self.candidate_streak,
            "version": 1,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "ModeState":
        data = value or {}
        mode = str(data.get("mode") or SUPPORT)
        if mode not in MODES:
            mode = SUPPORT
        candidate = data.get("candidate")
        if candidate is not None and str(candidate) not in MODES:
            candidate = None
        try:
            streak = int(data.get("candidate_streak") or 0)
        except (TypeError, ValueError):
            streak = 0
        return cls(mode=mode, candidate=candidate, candidate_streak=max(0, streak))


def initial_mode_state() -> ModeState:
    """A fresh conversation is a support conversation until shown otherwise."""
    return ModeState()


def _explicit_mode(text: str) -> str | None:
    stripped = text.strip().casefold()
    for command, mode in _COMMANDS.items():
        if stripped == command or stripped.startswith(f"{command} "):
            return mode
    for mode, pattern in _EXPLICIT_RE:
        if pattern.search(text):
            return mode
    return None


def _inferred_mode(text: str) -> str | None:
    """Best guess for one message, or None when it says nothing either way."""
    scores = {
        WORK: len(_WORK_RE.findall(text)),
        TIME: len(_TIME_RE.findall(text)),
        SUPPORT: len(_SUPPORT_RE.findall(text)),
    }
    best = max(scores, key=lambda mode: scores[mode])
    if scores[best] == 0:
        return None
    # A tie is not evidence; treat it as an uninformative turn.
    if list(scores.values()).count(scores[best]) > 1:
        return None
    return best


def advance_mode(state: ModeState, user_text: str | None) -> tuple[ModeState, str]:
    """Return the mode after this message, plus why it is what it is.

    Pure and total: the same state and message always give the same answer, so
    the whole machine can be tested exhaustively.
    """
    text = str(user_text or "")

    explicit = _explicit_mode(text)
    if explicit is not None:
        return ModeState(mode=explicit, candidate=None, candidate_streak=0), "explicit"

    # Safety outranks every other reading of the message.  classify_lifeboat_signals
    # strips quoted material first, so a pasted process dump is not a disclosure.
    if classify_lifeboat_signals(text).possible_crisis:
        return ModeState(mode=CRISIS, candidate=None, candidate_streak=0), "safety"

    if state.mode == CRISIS:
        if _ALL_CLEAR_RE.search(text):
            return ModeState(mode=SUPPORT, candidate=None, candidate_streak=0), "all-clear"
        # Crisis does not lapse just because the next message sounds ordinary.
        return state, "crisis-held"

    inferred = _inferred_mode(text)
    if inferred is None:
        # An "ok" or a "hmm" is not evidence against a pending change; keep the
        # candidate so a shift can accumulate across a real conversation.
        return state, "uninformative"
    if inferred == state.mode:
        return ModeState(mode=state.mode, candidate=None, candidate_streak=0), "unchanged"

    streak = state.candidate_streak + 1 if state.candidate == inferred else 1
    if streak >= _SWITCH_STREAK:
        return ModeState(mode=inferred, candidate=None, candidate_streak=0), "inferred"
    return ModeState(mode=state.mode, candidate=inferred, candidate_streak=streak), "pending"


# --- Persistence -----------------------------------------------------------
#
# The mode is per conversation, so it lives beside the other Life-Boat session
# state rather than in memory: a gateway restart must not silently drop a user
# back into support in the middle of a working session.

import json  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Iterator  # noqa: E402
import fcntl  # noqa: E402
import os  # noqa: E402


def _modes_path(profile_home: Path) -> Path:
    return Path(profile_home) / "state" / "lifeboat-modes.json"


@contextmanager
def _locked(profile_home: Path) -> Iterator[Path]:
    path = _modes_path(profile_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    with open(lock, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield path
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _load_all(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sessions": {}, "version": 1}
    if not isinstance(value, dict):
        return {"sessions": {}, "version": 1}
    value.setdefault("sessions", {})
    return value


def _save_all(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_mode_state(profile_home: Path, session_key: str) -> ModeState:
    """Return the stored mode for this conversation, or a fresh support state."""
    with _locked(profile_home) as path:
        sessions = _load_all(path).get("sessions", {})
    record = sessions.get(str(session_key)) if isinstance(sessions, dict) else None
    return ModeState.from_dict(record if isinstance(record, dict) else None)


def save_mode_state(profile_home: Path, session_key: str, state: ModeState) -> None:
    """Persist this conversation's mode."""
    with _locked(profile_home) as path:
        stored = _load_all(path)
        stored.setdefault("sessions", {})[str(session_key)] = state.to_dict()
        _save_all(path, stored)


def clear_mode_state(profile_home: Path, session_key: str) -> bool:
    """Forget this conversation's mode, for a user-requested reset."""
    with _locked(profile_home) as path:
        stored = _load_all(path)
        removed = stored.get("sessions", {}).pop(str(session_key), None) is not None
        if removed:
            _save_all(path, stored)
    return removed
