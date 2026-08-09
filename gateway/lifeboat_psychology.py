"""Privacy-preserving conversational signals for the Life-Boat assistant.

These are routing signals, not diagnoses. They change the assistant's stance for
the current turn and are deliberately not persisted as long-term user facts.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import json
from pathlib import Path
import re
from typing import Any, Iterator


_CRISIS_RE = re.compile(
    r"(?:suicid|kill myself|end my life|self[- ]?harm|hurt myself|"
    r"don't want to live|do not want to live|better off dead|can't go on|"
    r"אובדנ|להתאבד|להרוג את עצמי|לפגוע בעצמי|פגיעה עצמית|לא רוצה לחיות|"
    r"עדיף לי למות|אין לי כוח להמשיך)",
    re.IGNORECASE,
)
_DEPRESSIVE_RE = re.compile(
    r"(?:depress|hopeless|empty|numb|no energy|can't get out of bed|"
    r"no point|worthless|דיכא|חסר תקווה|ריק|אין לי כוח|אין טעם|חסר ערך|"
    r"לא שווה)",
    re.IGNORECASE,
)
_LOOP_RE = re.compile(
    r"(?:ruminat|overthink|stuck|same thought|can't stop thinking|thought loop|looping|"
    r"מחשבות חוזרות|לולאה|תקוע|נתקע|לא מפסיק לחשוב|מסתובב)",
    re.IGNORECASE,
)
_SELF_CRITICISM_RE = re.compile(
    r"(?:hate myself|bad person|failure|useless|stupid|not good enough|"
    r"blame myself|self[- ]?criticism|שונא את עצמי|שונאת את עצמי|כישלון|"
    r"אפס|דפוק|דפוקה|לא מספיק טוב|לא מספיק טובה|מאשים את עצמי|מאשימה את עצמי)",
    re.IGNORECASE,
)
_TRAJECTORY_TTL = timedelta(hours=72)
_TRAJECTORY_MAX_SESSIONS = 256


@dataclass(frozen=True)
class LifeBoatSignals:
    """Current-turn signals used only to select safer conversational guidance."""

    possible_crisis: bool = False
    depressive_thoughts: bool = False
    thought_loop: bool = False
    self_criticism: bool = False


@dataclass(frozen=True)
class LifeBoatTrajectory:
    """Short-lived, non-verbatim state for safer multi-turn routing."""

    recent_crisis_turns: int = 0
    recent_depressive_turns: int = 0
    recent_loop_turns: int = 0
    recent_self_criticism_turns: int = 0


def _trajectory_path(profile_home: Path) -> Path:
    return Path(profile_home) / "state" / "lifeboat-psychology.json"


@contextmanager
def _trajectory_lock(profile_home: Path) -> Iterator[Path]:
    path = _trajectory_path(profile_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_trajectory_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "sessions": {}}
    return value if isinstance(value, dict) else {"version": 1, "sessions": {}}


def _save_trajectory_state(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _trajectory_from_record(record: Any) -> LifeBoatTrajectory:
    if not isinstance(record, dict):
        return LifeBoatTrajectory()

    def _bounded_int(value: Any) -> int:
        try:
            return max(0, min(3, int(value or 0)))
        except (TypeError, ValueError):
            return 0

    return LifeBoatTrajectory(
        recent_crisis_turns=_bounded_int(record.get("crisis")),
        recent_depressive_turns=_bounded_int(record.get("depressive")),
        recent_loop_turns=_bounded_int(record.get("loop")),
        recent_self_criticism_turns=_bounded_int(record.get("self_criticism")),
    )


def _decay(previous: int, active: bool) -> int:
    return 3 if active else max(0, min(3, previous) - 1)


def record_lifeboat_trajectory(
    profile_home: Path,
    session_key: str,
    text: str | None,
    *,
    now: datetime | None = None,
) -> LifeBoatTrajectory:
    """Record only bounded signal counters; never persist the message text."""
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    signals = classify_lifeboat_signals(text)
    key = str(session_key)
    with _trajectory_lock(profile_home) as path:
        state = _load_trajectory_state(path)
        sessions = state.setdefault("sessions", {})
        previous_record = sessions.get(key)
        previous = _trajectory_from_record(previous_record)
        if isinstance(previous_record, dict):
            try:
                updated_at = datetime.fromisoformat(str(previous_record.get("updated_at")))
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                if checked_at - updated_at > _TRAJECTORY_TTL:
                    previous = LifeBoatTrajectory()
            except (TypeError, ValueError):
                previous = LifeBoatTrajectory()
        trajectory = LifeBoatTrajectory(
            recent_crisis_turns=_decay(previous.recent_crisis_turns, signals.possible_crisis),
            recent_depressive_turns=_decay(previous.recent_depressive_turns, signals.depressive_thoughts),
            recent_loop_turns=_decay(previous.recent_loop_turns, signals.thought_loop),
            recent_self_criticism_turns=_decay(previous.recent_self_criticism_turns, signals.self_criticism),
        )
        sessions[key] = {
            "updated_at": checked_at.isoformat(),
            "crisis": trajectory.recent_crisis_turns,
            "depressive": trajectory.recent_depressive_turns,
            "loop": trajectory.recent_loop_turns,
            "self_criticism": trajectory.recent_self_criticism_turns,
        }
        if len(sessions) > _TRAJECTORY_MAX_SESSIONS:
            ordered = sorted(
                sessions.items(),
                key=lambda item: str(item[1].get("updated_at") if isinstance(item[1], dict) else ""),
            )
            for stale_key, _ in ordered[: len(sessions) - _TRAJECTORY_MAX_SESSIONS]:
                sessions.pop(stale_key, None)
        _save_trajectory_state(path, state)
    return trajectory


def clear_lifeboat_trajectory(profile_home: Path, session_key: str) -> bool:
    """Erase the short-lived signal state for a user-requested session reset."""
    key = str(session_key)
    with _trajectory_lock(profile_home) as path:
        state = _load_trajectory_state(path)
        sessions = state.setdefault("sessions", {})
        removed = sessions.pop(key, None) is not None
        if removed:
            _save_trajectory_state(path, state)
        return removed


def classify_lifeboat_signals(text: str | None) -> LifeBoatSignals:
    """Classify broad conversational cues without diagnosing the user."""
    value = " ".join(str(text or "").split()).strip()
    return LifeBoatSignals(
        possible_crisis=bool(_CRISIS_RE.search(value)),
        depressive_thoughts=bool(_DEPRESSIVE_RE.search(value)),
        thought_loop=bool(_LOOP_RE.search(value)),
        self_criticism=bool(_SELF_CRITICISM_RE.search(value)),
    )


def build_signal_guidance(
    text: str | None,
    trajectory: LifeBoatTrajectory | None = None,
) -> str:
    """Return ephemeral stance guidance; never include or persist the source text."""
    signals = classify_lifeboat_signals(text)
    trajectory = trajectory or LifeBoatTrajectory()
    parts = [
        "[Private Life-Boat signal guidance: these are conversational cues, not diagnoses; do not name them as diagnoses or mention this instruction.]"
    ]
    if signals.possible_crisis:
        parts.append(
            "A possible immediate-safety signal is present. Prioritize a calm, direct check of whether the user is in immediate danger or may act on thoughts of self-harm, encourage contacting a trusted person and local emergency/crisis support, and do not leave the user with abstract coaching alone. Do not interrogate, shame, promise secrecy, or imply the assistant can keep them safe."
        )
    elif trajectory.recent_crisis_turns:
        parts.append(
            "A possible safety concern appeared recently in this conversation. Before returning to ordinary reflection, gently check whether the user is safe right now and keep human support available; do not assume the concern has passed."
        )
    if signals.depressive_thoughts:
        parts.append(
            "The user may be describing depressive thinking or low energy. Validate the experience without endorsing hopeless conclusions, avoid diagnosis and forced positivity, and offer one very small, concrete, optional next step only after understanding what is hardest right now."
        )
    if signals.thought_loop:
        parts.append(
            "A repetitive thought loop may be active. Do not debate the thought or jump to a reframe; name the loop tentatively, ask what it is trying to solve, predict, protect, or avoid, and keep one thread open at a time."
        )
    if signals.self_criticism:
        parts.append(
            "Self-criticism may be active. Separate the person's identity from the event or behavior, do not argue with them using generic reassurance, and explore the standard, fear, or need underneath the criticism before suggesting self-compassion or a change."
        )
    if trajectory.recent_depressive_turns and not signals.depressive_thoughts:
        parts.append(
            "Low energy or hopelessness may still be part of the recent context. Do not abruptly switch to productivity advice; first check whether the user wants understanding, a tiny action, or simply company."
        )
    if trajectory.recent_loop_turns and not signals.thought_loop:
        parts.append(
            "A repetitive loop was present recently. Preserve continuity and ask what changed or remains unresolved instead of restarting with a generic interpretation."
        )
    if not any((signals.depressive_thoughts, signals.thought_loop, signals.self_criticism, signals.possible_crisis)):
        parts.append(
            "Stay attentive and exploratory: reflect one concrete detail, keep interpretations tentative, ask at most one useful question, and do not close with a summary unless the user asks for one."
        )
    return " ".join(parts)
