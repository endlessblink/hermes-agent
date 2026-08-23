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
Every word it returns came from a model that was shown *this* conversation, and
the single fixed sentence that survives here -- the admission that the bot has
no read -- is rate limited per session so it can never become the eighth
delivery of the same line.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
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
    "A good reply arrives with a read. One sentence of what you make of what he "
    "said, held lightly enough that he can knock it down in a single word, then "
    "one question that tests that read and nothing else. He corrects it or he "
    "lets it stand; either one moves. A blank question does not.\n"
    "\n"
    "Rules:\n"
    "- Use only his message and the material below. Never invent an event, a "
    "person, a decision, or a feeling he has not indicated.\n"
    "- If nothing supports a read, do not manufacture one and do not retreat to "
    "a broad question. Say plainly, in one sentence, that you do not have a "
    "clear picture of where he is right now, and ask one narrow question about "
    "the thing he just raised.\n"
    "- Keep his words, his specifics, and his register. Plain speech, not "
    "therapy vocabulary.\n"
    "- Do not summarise him back to himself, do not lay out stages or lists, do "
    "not offer a menu of options, do not narrate what you are doing.\n"
    "- Do not state what another person thinks or feels as established fact.\n"
    "- Do not use stock coaching sentences. If a line could be sent to anyone, "
    "it is the wrong line.\n"
    "- If the draft already arrives with a read, change as little as it needs. "
    "You are not required to rewrite what is already right.\n"
    "- Reply with the message text only, in Hebrew. No preamble, no explanation, "
    "no quotation marks around it."
)

#: The one fixed sentence in this module, and the only text delivered that no
#: model wrote.  It exists because the alternative was shipping a reply that had
#: already failed review twice.  ``no_read_allowed`` keeps it rare.
NO_READ_TEXT = (
    "אין לי עכשיו קריאה מספיק ברורה של מה שקורה איתך, ואני לא רוצה להמציא אחת. "
    "מה קרה היום שהביא אותך לכתוב?"
)

#: How many deliveries must pass before the admission may be sent again.  A
#: canned line once is honesty; the same line twice in a session is BUG-6.
NO_READ_COOLDOWN = 6


def build_editor_messages(
    user_text: str,
    draft: str,
    *,
    material: str = "",
    reason: str = "",
) -> list[dict[str, str]]:
    """Build the request that hands one draft to the editing agent."""
    blocks = [f"His message:\n{str(user_text or '').strip() or '(empty)'}"]
    if material and material.strip():
        blocks.append(material.strip())
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
    try:
        raw = edit(build_editor_messages(user_text, draft, material=material, reason=reason))
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
    return EditResult(text, True, text != original)


def _no_read_path(profile_home: Path, session_key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_key or "default"))
    return Path(profile_home) / "state" / f"lifeboat-noread-{safe}.json"


def no_read_allowed(
    profile_home: Path | None,
    session_key: str,
    *,
    deliveries: int = 1,
) -> bool:
    """True when the fixed admission may be sent again in this session.

    With no state -- no profile home, an unreadable file -- the answer is yes.
    The failure being guarded is repetition, and a bot that has no counter yet
    has nothing to repeat.
    """
    if profile_home is None:
        return True
    try:
        raw = json.loads(_no_read_path(profile_home, session_key).read_text(encoding="utf-8"))
        since = int(raw.get("deliveries_at_last_use", 0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return True
    return (int(deliveries) - since) >= NO_READ_COOLDOWN


def record_no_read(
    profile_home: Path | None,
    session_key: str,
    *,
    deliveries: int = 1,
) -> None:
    """Remember that the admission was just used."""
    if profile_home is None:
        return
    path = _no_read_path(profile_home, session_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"deliveries_at_last_use": int(deliveries), "at": time.time()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        logger.debug("Life-Boat no-read state unwritable", exc_info=True)


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
