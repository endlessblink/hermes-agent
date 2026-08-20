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
    r"מחשבות חוזרות|מחשבות טורדניות|לולאה|לופ|תקוע|נתקע|לא מפסיק לחשוב|מסתובב)",
    re.IGNORECASE,
)
_SELF_CRITICISM_RE = re.compile(
    r"(?:hate myself|bad person|failure|useless|stupid|not good enough|"
    r"blame myself|self[- ]?criticism|שונא את עצמי|שונאת את עצמי|כישלון|"
    r"ביקורת עצמית|ביקורת על עצמי|אפס|דפוק|דפוקה|לא מספיק טוב|לא מספיק טובה|"
    r"מאשים את עצמי|מאשימה את עצמי)",
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


_CONVERSATION_CONTRACT = (
    "Talk with the user; do not package what they said. Engage with what they actually "
    "raised — when they raise several things at once, stay with more than one of them "
    "instead of choosing a single angle and dropping the rest. Work in their own words "
    "and their own specifics rather than therapeutic abstraction, and keep any "
    "interpretation tentative and open to correction. Do not produce numbered "
    "breakdowns, bulleted layers, framework labels such as \"the core here is\" or "
    "\"so the conclusion is\", quoted maxims presented as the takeaway, or exercises, "
    "homework and behavioural experiments unless they explicitly ask for practical "
    "steps. Do not offer a menu of support options or an either/or question that "
    "forces a choice between two readings of their experience; an open door is an "
    "invitation to continue, not a pair of buttons. Do not close on a polished "
    "summary line; leave the thread alive. If they "
    "correct you, take the correction and continue from it rather than restating the "
    "same point more elegantly. Do not repeat an interpretation you have already given "
     "unless it moves somewhere new. For an ambiguous non-response, separate what is unknown "
     "from what the user is blaming themselves for; silence is not evidence against them. "
     "For a personal decision, distinguish a possible opener from genuine interest and finish "
     "the user's own decomposition before offering advice."
)

_SUMMARY_CONSENT = (
    "If the conversation has genuinely gathered enough material, or the user seems to "
    "be wrapping up, you may offer a brief optional daily summary and ask permission "
    "before creating or saving it; never start it or prompt for it repeatedly on your "
    "own."
)


def _signal_cautions(
    signals: LifeBoatSignals,
    trajectory: LifeBoatTrajectory,
) -> list[str]:
    """Name only what to avoid on sensitive ground; never prescribe a move.

    Prescribed moves are what produced canned questions like "what is this loop
    trying to solve or prevent" and unsolicited "one small next step" homework.
    The user's own coaching corrections rule those out, so this layer stays
    subtractive and lets the coaching skill govern what the reply actually does.
    """
    cautions: list[str] = []
    if signals.depressive_thoughts or trajectory.recent_depressive_turns:
        cautions.append(
            "no diagnosis, no forced positivity, no endorsing hopeless conclusions, "
            "and do not switch into productivity or self-improvement advice."
        )
    if signals.thought_loop or trajectory.recent_loop_turns:
        cautions.append(
            "Do not debate the thought. Thought-record work uses data; do not ask for "
            "another sentence/deeper cause; advance next missing stage."
        )
    if signals.self_criticism or trajectory.recent_self_criticism_turns:
        cautions.append(
            "Avoid generic reassurance; separate event, verdict, and action-demand."
        )
    return cautions


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
            "A possible immediate-safety signal is present. Prioritize a calm, direct check of whether the user is in immediate danger or may act on thoughts of self-harm, encourage contacting a trusted person and local emergency/crisis support, and do not leave the user with abstract coaching alone. For an Israel-based user, ERAN 1201 is a human crisis-support option; otherwise ask the user's location or use the correct local emergency/crisis resource. Do not interrogate, shame, promise secrecy, or imply the assistant can keep them safe."
        )
    elif trajectory.recent_crisis_turns:
        parts.append(
            "A possible safety concern appeared recently in this conversation. Before returning to ordinary reflection, gently check whether the user is safe right now and keep human support available; do not assume the concern has passed."
        )
    parts.append(_CONVERSATION_CONTRACT)
    cautions = _signal_cautions(signals, trajectory)
    if cautions:
        parts.append(
            "Sensitive ground in this turn, so avoid these specifically: " + " ".join(cautions)
        )
    parts.append(_SUMMARY_CONSENT)
    return " ".join(parts)
