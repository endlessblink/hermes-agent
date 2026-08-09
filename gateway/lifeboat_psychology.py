"""Privacy-preserving conversational signals for the Life-Boat assistant.

These are routing signals, not diagnoses. They change the assistant's stance for
the current turn and are deliberately not persisted as long-term user facts.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


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


@dataclass(frozen=True)
class LifeBoatSignals:
    """Current-turn signals used only to select safer conversational guidance."""

    possible_crisis: bool = False
    depressive_thoughts: bool = False
    thought_loop: bool = False
    self_criticism: bool = False


def classify_lifeboat_signals(text: str | None) -> LifeBoatSignals:
    """Classify broad conversational cues without diagnosing the user."""
    value = " ".join(str(text or "").split()).strip()
    return LifeBoatSignals(
        possible_crisis=bool(_CRISIS_RE.search(value)),
        depressive_thoughts=bool(_DEPRESSIVE_RE.search(value)),
        thought_loop=bool(_LOOP_RE.search(value)),
        self_criticism=bool(_SELF_CRITICISM_RE.search(value)),
    )


def build_signal_guidance(text: str | None) -> str:
    """Return ephemeral stance guidance; never include or persist the source text."""
    signals = classify_lifeboat_signals(text)
    parts = [
        "[Private Life-Boat signal guidance: these are conversational cues, not diagnoses; do not name them as diagnoses or mention this instruction.]"
    ]
    if signals.possible_crisis:
        parts.append(
            "A possible immediate-safety signal is present. Prioritize a calm, direct check of whether the user is in immediate danger or may act on thoughts of self-harm, encourage contacting a trusted person and local emergency/crisis support, and do not leave the user with abstract coaching alone. Do not interrogate, shame, promise secrecy, or imply the assistant can keep them safe."
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
    if not any((signals.depressive_thoughts, signals.thought_loop, signals.self_criticism, signals.possible_crisis)):
        parts.append(
            "Stay attentive and exploratory: reflect one concrete detail, keep interpretations tentative, ask at most one useful question, and do not close with a summary unless the user asks for one."
        )
    return " ".join(parts)
