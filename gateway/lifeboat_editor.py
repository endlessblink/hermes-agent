"""An agent that edits the reply before it is delivered, instead of vetoing it.

The pre-send reviewer could only reject, and only a draft that broke a named
rule.  The replies Noam actually complained about broke no rule: they were
gentle, well-formed, and empty -- "when you look at the recent period as a
whole, how do you feel you got through it?".  Bland is always legal, so every
prohibition added to the reviewer pushed the model further toward exactly that.
A gate that can only say no cannot ask for more.

So this module writes.  It receives the draft plus the material the turn
already assembled about him, and returns the reply he will read.  That reverses
an earlier decision on purpose -- the reviewer was left toothless because
generated replacement prose is how one hardcoded Hebrew sentence reached him
eight times in an afternoon (BUG-6).  The protection that mattered is kept and
moved rather than dropped: this module contains no sentence of the reply.
Every word it returns came from a model that was shown *this* conversation;
when revision fails, the delivery path preserves the model's own draft and
records the failed gate rather than authoring a fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


#: The editor's whole brief.  It describes what a good reply *does* and gives
#: no example of what one says: hand a model a sentence and it will deliver
#: that sentence, which is the template returning through the back door.
EDITOR_SYSTEM = (
    "You are editing one reply in an ongoing Hebrew-language emotional support "
    "conversation. You never speak to the user yourself; you return the message "
    "he will read, in Hebrew.\n"
    "\n"
    "The failure you exist to correct: the assistant keeps handing the work back "
    "to him -- asking him to supply the facts, to pick a direction, to say what "
    "is most alive, to explain what he meant -- instead of doing the work of "
    "understanding him. A broad, gentle question about his life is that failure, "
    "not an escape from it.\n"
    "\n"
    "A good reply does the work in one of two ways: it either offers a hedged "
    "read of what you understand, held lightly enough that he can correct it, "
    "or it takes a concrete next step that you choose for him. In the second "
    "shape, make the choice concrete from what he actually wrote — choose the "
    "next event, action, or the message itself — and then ask only for the detail "
    "needed to take that chosen step. Use a time or place only when he supplied it; "
    "otherwise anchor to the words or event he just gave you, and ask "
    "what happened at that anchor \u2014 what was said, what he did, what came next "
    "in the world. Never make that question about his interior. \"What happened in "
    "you between X and Y\", \"how did that feel\", \"what did that do to you\" are the "
    "assistant refusing to think and asking him to think out loud instead; he "
    "recognises it instantly, and it reads like someone speaking from far away. His "
    "interior is the thing you are supposed to guess at. When he reports a decision, "
    "an action, or a feeling, say what you think was going on in him and let him "
    "correct you in one word. Ask him to describe his own inner state only once your "
    "guess is already on the table. "
    "A question about which timeframe, day, event, "
    "or frame to use means the choice has not been made yet; choose one and move. "
    "A correction, a confirmed read, or a concrete step chosen by the assistant "
    "all count as movement. Preserve a draft that already does either kind of "
    "work.\n"
    "\n"
    "Rules:\n"
    "- Use only his message and the material below. Never invent an event, a "
    "person, a decision, or a feeling he has not indicated. Historical material "
    "is optional context, not a fact about this turn; do not combine isolated "
    "fragments from different threads into a current summary. Use an older "
    "detail only when his current message clearly connects to it.\n"
    "- If nothing supports a specific historical read, still do the work from his "
    "current wording: offer one modest, clearly hedged read of what the message "
    "itself conveys, or choose the message he just wrote as the concrete anchor "
    "and ask one narrow detail about it. Do not make 'I do not have enough "
    "context' the whole reply and do not retreat to a broad question.\n"
    "- Keep his words, his specifics, and his register. Plain speech, not "
    "therapy vocabulary.\n"
    "- Sound like a close, attentive person: direct, warm, and ordinary in "
    "Hebrew, with no therapist framing or process language.\n"
    "- Do not summarise him back to himself, do not lay out stages or lists, do "
    "not offer a menu of options, do not narrate what you are doing.\n"
    "- Do not state what another person thinks or feels as established fact.\n"
    "- Do not turn an action into an unstated reason, preference, motive, or goal. "
    "If he says what he did but not why, keep the reason open or offer it clearly "
    "as a guess for correction.\n"
    "- Do not use stock coaching sentences. If a line could be sent to anyone, "
    "it is the wrong line.\n"
    "- If the draft already arrives with a read, change as little as it needs. "
    "You are not required to rewrite what is already right.\n"
    "- Reply with the message text only, in Hebrew. No preamble, no explanation, "
    "no quotation marks around it."
)

def build_editor_messages(
    user_text: str,
    draft: str,
    *,
    material: str = "",
    reason: str = "",
) -> list[dict[str, str]]:
    """Build the request that hands one draft to the editing agent."""
    blocks: list[str] = []
    try:
        from gateway.lifeboat_voice import load_voice_text

        voice = load_voice_text()
    except Exception:
        voice = ""
    if voice:
        blocks.append(
            "The reply you return is spoken by this person. Match them, and do "
            "not normalise the draft into a more careful register than theirs:\n"
            + voice
        )
    blocks.append(f"His message:\n{str(user_text or '').strip() or '(empty)'}")
    if material and material.strip():
        blocks.append(
            "HISTORICAL MATERIAL — user-originated but not necessarily about this "
            "turn; treat it as optional and do not summarize it as current:\n"
            + material.strip()
        )
    else:
        blocks.append(
            "MATERIAL: none available this turn. You know nothing about his "
            "recent life beyond the message above."
        )
    blocks.append(f"The assistant's draft reply:\n{str(draft or '').strip()}")
    if reason:
        blocks.append(
            f"A reviewer rejected this draft for: {reason}. Fix that as well, "
            "but the read is the point, not the rule."
        )
    blocks.append("Return the reply he should receive.")
    return [
        {"role": "system", "content": EDITOR_SYSTEM},
        {"role": "user", "content": "\n\n".join(blocks)},
    ]


_FENCE_RE = re.compile(r"^\s*```[^\n]*\n(?P<body>.*?)\n?```\s*$", re.S)
_WRAPPING_QUOTES = ('"', "'", "“", "”", "«", "»")
_TEMPORAL_ANCHOR_RE = re.compile(
    r"(?:אתמול|היום|מחר|הבוקר|הערב|הלילה|השבוע|בשבוע האחרון|היום האחרון|"
    r"הרגע האחרון|מהבוקר|עד הערב|מהערב|"
    r"yesterday|today|tomorrow|this morning|this evening|last night|"
    r"this week|last week)",
    re.IGNORECASE,
)
_THERAPIST_HANDOFF_RE = re.compile(
    r"(?:אני כאן איתך|אני איתך בקצב שלך|בקצב שלך|אני פה בשבילך|אני איתך(?:[.!,:—–]|$))",
    re.IGNORECASE,
)


#: Asking him to narrate his own interior. This is the therapist move, and it
#: is the one he keeps recognising: told "after the speed dating I decided not
#: to contact anyone even though they gave me two numbers", the bot answered
#: "what happened in you between getting the numbers and that decision?".
#:
#: Every rule in this module passed it. Every fact in it came from him, it took
#: a concrete next step, it invented nothing, and it still failed -- because
#: groundedness cannot see a handback. He said it reads "like a person talking
#: from far away", and that is exactly what it is: the assistant declining to
#: think about him and asking him to do the thinking out loud instead.
#:
#: The interior is what the assistant is supposed to guess at. Asking about the
#: world -- what was said, what he did next -- is fine. Asking him to report
#: what went on inside him, with no guess of your own on the table, is not.
_INTERIOR_INTERROGATION_RE = re.compile(
    r"מה\s+קרה\s+(?:אצלך|בתוכך|לך\s+בפנים)"
    r"|מה\s+(?:עבר|רץ|עלה)\s+(?:לך|בך)"
    r"|מה\s+הרגשת|איך\s+(?:זה\s+)?הרגיש|מה\s+זה\s+עשה\s+לך"
    r"|מה\s+גרם\s+לך|ממה\s+זה\s+נבע|איפה\s+זה\s+יושב\s+אצלך"
    r"|מה\s+היה\s+שם\s+(?:עבורך|בשבילך)|מה\s+הכי\s+נוכח"
    r"|מה\s+התחושה\s+שלך|מה\s+עובר\s+עליך"
    r"|\bwhat\s+(?:happened|came\s+up|went\s+on)\s+(?:in|inside)\s+you\b"
    r"|\bhow\s+did\s+(?:that|it)\s+feel\b",
    re.IGNORECASE,
)


def _offers_a_read(candidate: str) -> bool:
    """True when the assistant has put its own guess about him on the table.

    Reuses the debrief detectors so there is one definition of "a read offered
    for correction" in the system rather than two that drift apart.
    """
    from gateway.lifeboat_debrief import _INVITES_CORRECTION_RE, _TENTATIVE_READ_RE

    return bool(
        _TENTATIVE_READ_RE.search(candidate) and _INVITES_CORRECTION_RE.search(candidate)
    )


def asks_him_to_narrate_his_interior(candidate: str) -> bool:
    """True when the reply asks what went on inside him and guesses nothing.

    No longer enforced. It was added as a hard failure after one bad reply, and
    then Noam said plainly: "I want it to ask me questions, just to feel like a
    close person" -- and separately, "bans are not the solution". A gate that
    blocks a whole class of question he wants is the mistake this project keeps
    making. Kept as a description because the wording is still worth naming in
    review; not used to reject anything.

    The same question is legitimate once the assistant has committed to a read:
    then it is checking its own thinking, not outsourcing it.
    """
    text = str(candidate or "")
    if not _INTERIOR_INTERROGATION_RE.search(text):
        return False
    return not _offers_a_read(text)


def clean_editor_output(value: str | None) -> str:
    """Strip the wrappers models add around a message they were asked for."""
    text = str(value or "").strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group("body").strip()
    while len(text) > 1 and text[0] in _WRAPPING_QUOTES and text[-1] in _WRAPPING_QUOTES:
        text = text[1:-1].strip()
    return text


@dataclass(frozen=True)
class EditResult:
    """What the editor produced, and whether it produced anything."""

    text: str
    available: bool
    changed: bool


def _has_unsupported_temporal_anchor(candidate: str, user_text: str, material: str) -> bool:
    """Reject a revision that introduces a time anchor absent from evidence."""
    # Historical material can contain the same word in an unrelated thread;
    # only the current user turn directly licenses a concrete temporal anchor.
    evidence = str(user_text or "").casefold()
    return any(match.group(0).casefold() not in evidence for match in _TEMPORAL_ANCHOR_RE.finditer(candidate))


def _has_therapist_handoff(candidate: str) -> bool:
    """Reject empty relational/process language that replaces a useful reply."""
    return bool(_THERAPIST_HANDOFF_RE.search(candidate))


def unsafe_draft_reason(candidate: str, user_text: str, material: str = "") -> str:
    """Return the first hard reason a draft must not pass through unchanged."""
    if _has_unsupported_temporal_anchor(candidate, user_text, material):
        return "unsupported_temporal_anchor"
    if _has_therapist_handoff(candidate):
        return "therapist_handoff"
    return ""


def edit_reply(
    user_text: str,
    draft: str,
    *,
    edit: Callable[[list[dict[str, str]]], str],
    material: str = "",
    reason: str = "",
) -> EditResult:
    """Run the editing agent over one draft.

    ``edit`` is injected so the whole decision is testable without a model call.
    An editor that fails, times out, or returns nothing is not an error: the
    caller keeps the draft and the turn is delivered as it would have been.
    """
    original = str(draft or "").strip()
    text = original
    for attempt in range(2):
        retry_reason = reason
        if attempt:
            retry_reason = f"{reason}; the previous edit failed a safety check. "
            retry_reason += (
                "Remove any unsupported time anchor and repair only the failed span."
                if _has_unsupported_temporal_anchor(text, user_text, material)
                else (
                    "You asked him to report what went on inside him without "
                    "putting a guess of your own on the table. Say what you "
                    "think happened in him, hedged so one word from him can "
                    "knock it down, and ask him to confirm that instead."
                    if asks_him_to_narrate_his_interior(text)
                    else "Remove therapist-like handoff language and repair only the failed span."
                )
            )
        try:
            raw = edit(
                build_editor_messages(
                    user_text, draft, material=material, reason=retry_reason
                )
            )
        except Exception as exc:
            logger.warning(
                "Life-Boat editor unavailable error=%s message_content=redacted",
                type(exc).__name__,
            )
            return EditResult(original, False, False)

        text = clean_editor_output(raw)
        if not text:
            logger.warning("Life-Boat editor returned nothing message_content=redacted")
            return EditResult(original, False, False)
        if unsafe_draft_reason(text, user_text, material):
            logger.warning(
                "Life-Boat editor produced an unsafe revision attempt=%s",
                attempt + 1,
            )
            continue
        return EditResult(text, True, text != original)
    return EditResult(original, True, False)


def _count_path(profile_home: Path, session_key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_key or "default"))
    return Path(profile_home) / "state" / f"lifeboat-deliveries-{safe}.json"


def bump_delivery_count(profile_home: Path | None, session_key: str) -> int:
    """Count this delivery and return the new total for the session.

    The cooldown needs a clock that ticks once per reply he receives. Wall time
    is the wrong one: he can be quiet for an hour and then send four messages,
    and the guard is about how many replies he reads, not how long he waited.
    """
    if profile_home is None:
        return 1
    path = _count_path(profile_home, session_key)
    try:
        current = int(json.loads(path.read_text(encoding="utf-8")).get("deliveries", 0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        current = 0
    total = current + 1
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"deliveries": total}), encoding="utf-8")
    except OSError:
        logger.debug("Life-Boat delivery counter unwritable", exc_info=True)
    return total


#: Touch this file to turn the editor off; delete it to turn it back on. No
#: restart, no config edit, no session with me. The editor rewrites the words
#: he reads when he is at his worst, and the last several attempts to improve
#: this bot made it worse -- so the ability to stop it has to be his, and has
#: to be one command.
DISABLE_FLAG = Path.home() / ".hermes" / "lifeboat-editor-off"


def editor_enabled() -> bool:
    """False when the disable flag is present."""
    try:
        return not DISABLE_FLAG.exists()
    except OSError:
        return True


def runtime_receipt() -> dict[str, object]:
    """Describe the editor code this Python process actually imported."""
    module_path = Path(__file__).resolve()
    try:
        module_sha256 = hashlib.sha256(module_path.read_bytes()).hexdigest()
    except OSError:
        module_sha256 = "unavailable"
    return {
        "module": str(module_path),
        "sha256": module_sha256,
        "editor_enabled": editor_enabled(),
        "pid": os.getpid(),
    }


logger.info("Life-Boat editor loaded runtime=%s", runtime_receipt())
