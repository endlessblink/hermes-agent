"""What the bot actually knows about him, handed to the turn that needs it.

Every Life-Boat turn was given guidance made entirely of rules -- reflect one
detail, do not close, do not list, do not reassure -- and no material at all.
No journal, no recent conversation, nothing about his week.

Asked on 2026-08-23 to debrief "the recent period", it had no access to the
recent period. It answered "כשאתה מסתכל על התקופה האחרונה בכללותה, איך אתה
מרגיש שעברת אותה?" because a blank question was the only thing it could build
from an empty hand. Every prohibition added on top made it blander, since bland
is always legal.

So the turn gets the material: what he wrote in his journal, what is open in
his own queue, and what he actually said recently. Bounded, his words only, and
with operational chatter dropped -- a support conversation opening on a deploy
is its own failure.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from gateway.lifeboat_checkin_context import is_operational


logger = logging.getLogger(__name__)

VAULT = Path(
    "/media/endlessblink/data/app-data/sync/Dropbox/OBSIDIAN_SYNCED/MAIN VULT"
)
TRANSCRIPT_DIR = VAULT / "_System" / "Hermes Turn Logs" / "life-boat"

#: Enough to be specific, not so much that the turn becomes a digest.
MAX_TURNS = 12
MAX_LINE_CHARS = 220

_USER_BLOCK = re.compile(
    r"^## (\S+) — session `[^`]+` — platform `[^`]+`\s*\n+### User\s*\n+(.*?)\n+### Assistant",
    re.M | re.S,
)
_SPEAKER_PREFIX = re.compile(r"^\[[^\]]{0,40}\]\s*")
_REPLY_QUOTE = re.compile(r"^\[Replying to:.*?\]\s*", re.S)
#: Engine and provenance notes recorded as if he had typed them. Reading
#: one back as his own words is the same fault that once turned a pasted
#: process dump into a crisis disclosure.
_INJECTED_NOTE_RE = re.compile(
    r"^\[\s*(?:IMPORTANT|Background process|System note|Note|Reminder)\b",
    re.IGNORECASE,
)

#: The same fault in a second disguise. Six copies of an engine block --
#: "# Suggestion discipline / Local time: ... / Suggestions voiced today: 0/2"
#: -- sit in the transcript under "### User", indistinguishable from something
#: he typed, and were being handed to the turn as his recent words.
#:
#: The bracket guard above could not see them because they are not bracketed.
#: The rule that does see them is provenance, not vocabulary: he types messages
#: into Telegram, and a message that opens with a markdown heading or carries an
#: engine's own bookkeeping was assembled by this system, not by him.
_ENGINE_BLOCK_RE = re.compile(
    r"^#{1,6}\s"
    r"|^(?:Local time|Suggestion discipline|Suggestions voiced|Session|Context)\s*:"
    r"|Suggestions voiced today",
    re.IGNORECASE,
)


def is_engine_block(text: str | None) -> bool:
    """True when this "user turn" was written by the system, not by him."""
    value = str(text or "").strip()
    if not value:
        return False
    return bool(_INJECTED_NOTE_RE.match(value) or _ENGINE_BLOCK_RE.search(value))


def _clean(text: str) -> str:
    value = _REPLY_QUOTE.sub("", str(text or "")).strip()
    value = _SPEAKER_PREFIX.sub("", value)
    return " ".join(value.split())[:MAX_LINE_CHARS]


#: Everything before the topics were split sits in the shared log, and reading
#: it was judged safe on the grounds that a filter would drop the bug and deploy
#: talk. A live replay on 2026-08-23 showed what it actually delivered, layer
#: after layer: praise of two bot replies read back as two good days, an
#: instruction about Telegram agents, six copies of an engine bookkeeping block
#: filed as his words, and under those, a debugging session -- "we have problems
#: on top of problems", "what did we get wrong in development".
#:
#: Two filters were added and each one only revealed the next layer. That is the
#: treadmill this whole subsystem keeps climbing, and the reason it never
#: converges is structural: that log is his general-purpose thread, where he
#: works. There is no vocabulary that separates a man's life from his work when
#: both were typed into the same box.
#:
#: So it is no longer read. The support thread has its own log now, and until it
#: fills, the honest answer is that the bot does not have the recent picture --
#: which it is told to say plainly rather than paper over with a broad question.
#: An empty hand is not the failure here. A confident false read of his week is.
LEGACY_DIR = None


def recent_user_turns(
    transcript_dir: Path | None = None,
    *,
    limit: int = MAX_TURNS,
    legacy_dir: Path | None = None,
    exclude_live: bool = False,
    now=None,
):
    """Return what he actually said recently, newest last.

    Only his own words. The bot's replies are not evidence about his life, and
    feeding them back is how a conversation starts circling itself.
    """
    root = transcript_dir if transcript_dir is not None else TRANSCRIPT_DIR
    legacy = legacy_dir if legacy_dir is not None else LEGACY_DIR
    collected: list[tuple[str, str]] = []
    for source in (source for source in (legacy, root) if source is not None):
        try:
            for path in sorted(source.glob("*.md"))[-7:]:
                text = path.read_text(encoding="utf-8")
                for stamp, said in _USER_BLOCK.findall(text):
                    if is_engine_block(said):
                        continue
                    if exclude_live and _is_live_conversation(stamp, now=now):
                        continue
                    line = _clean(said)
                    if line and not is_operational(line):
                        collected.append((stamp, line))
        except OSError:
            continue
    collected.sort(key=lambda item: item[0])
    # Tonight's turns were copied rather than moved, so both logs hold them.
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for stamp, line in collected:
        if line in seen:
            continue
        seen.add(line)
        unique.append((stamp, line))
    return tuple(unique[-limit:])


QUEUE = VAULT / "_System" / "Hermes Knowledge Graph" / "Projects" / "Emotional Processing Queue.md"
JOURNAL = VAULT / "_System" / "Hermes Knowledge Graph" / "Projects" / "Daily Evidence Journal"


def _read_queue() -> str:
    try:
        return QUEUE.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_journal(limit: int = 5):
    try:
        return [p.read_text(encoding="utf-8") for p in sorted(JOURNAL.glob("*.md"))[-limit:]]
    except OSError:
        return []


#: How recent a turn has to be before it counts as the conversation in progress
#: rather than as material. Long enough to cover a live exchange with pauses in
#: it, short enough that a thread reopened later still arrives with his week.
LIVE_CONVERSATION_MINUTES = 45


def _is_live_conversation(stamp: str, *, now=None) -> bool:
    """True when this turn is part of the exchange the model can already see."""
    from datetime import datetime, timedelta

    try:
        when = datetime.fromisoformat(str(stamp)[:19])
    except (TypeError, ValueError):
        return False
    current = now or datetime.now()
    return (current - when) < timedelta(minutes=LIVE_CONVERSATION_MINUTES)


def build_turn_context(
    *,
    about_a_period: bool = False,
    request_text: str = "",
    now=None,
    transcript_dir: Path | None = None,
    legacy_dir: Path | None = None,
    queue_text: str | None = None,
    journal_entries=None,
) -> str:
    """Assemble the material this turn may draw on, or an empty string."""
    # A broad period request is not permission to pick the most vivid item in
    # an old log and make it the subject.  The live conversation history already
    # contains anything explicitly being discussed; unrelated vault material is
    # a candidate store, not the user's chosen topic.  Keep this boundary here,
    # where context enters the prompt, rather than trying to repair each wording
    # the model might invent downstream.
    if about_a_period and request_text:
        try:
            from gateway.lifeboat_debrief import is_broad_debrief

            if is_broad_debrief(request_text):
                return (
                    "PERIOD-WIDE DEBRIEF: the user asked to review a stretch of time "
                    "without naming one event. Do not select an old event, person, "
                    "or motive from background material as the subject. Use only "
                    "events already present in the live conversation, or choose a "
                    "concrete starting scope without asking him to design the "
                    "whole debrief."
                )
        except Exception:
            logger.debug("Life-Boat debrief scope unavailable", exc_info=True)
    parts: list[str] = []
    try:
        from gateway.lifeboat_context_sources import build_context_block

        block = build_context_block(
            queue_text=queue_text if queue_text is not None else _read_queue(),
            journal_entries=(
                journal_entries if journal_entries is not None else _read_journal()
            ),
        )
        if block:
            parts.append(block)
    except Exception:
        logger.debug("Life-Boat written context unavailable", exc_info=True)

    # Material means what the model cannot already see. The turns of the
    # conversation it is currently in reach it as history; repeating them here
    # under a heading calling them old fragments from other threads is how it
    # ended up circling back over ground he had just settled.
    #
    # The first attempt at this excluded everything from today, and that was the
    # wrong axis. Opening a fresh thread an hour later, the bot had no material
    # at all and did exactly what an empty hand produces: a blank question, with
    # an invented subject attached ("what happened recently at work?"). Nothing
    # about work had been said.
    #
    # So the cut is by recency, not by date. What he said in the last stretch is
    # the live conversation; what he said before it is material.
    turns = recent_user_turns(
        transcript_dir, legacy_dir=legacy_dir, exclude_live=True, now=now
    )
    if turns:
        said = "\n".join(f"- {stamp[:10]}: {line}" for stamp, line in turns)
        parts.append(
            "HISTORICAL USER TURNS (explicit user words, but fragments from prior "
            "threads; do not treat them as current or combine unrelated lines):\n"
            + said
        )

    if not parts:
        return ""

    # Two different situations, and one heading was serving both badly.
    #
    # Normally this material is background: he says something new, and older
    # fragments must not be dragged in to make a reply sound attentive. That is
    # what the cautious wording is for, and it stays.
    #
    # But when he asks about a period -- "let's debrief the last two days" --
    # the material is not background, it is the subject. On 2026-08-24 he asked
    # exactly that, the bot held ten lines including the whole conversation
    # about his speed dating, and opened with "what happened yesterday
    # morning?". It was obeying the heading: his message did not name the speed
    # date, so it stayed with the message and asked a cold question about a
    # period it already knew something about.
    #
    # Guarding against invention had turned into ignoring what it actually
    # knows. So the heading depends on what he asked for.
    if about_a_period:
        heading = (
            "MATERIAL YOU ALREADY HAVE, AND HE HAS ASKED ABOUT THIS PERIOD. "
            "This is the subject, not background: these are things he told you "
            "himself, and starting somewhere else would be asking him to supply "
            "what you already have. Begin from something in here. Do not "
            "combine unrelated lines into a story, do not add anything that is "
            "not here, and let him correct you."
        )
    else:
        heading = (
            "MATERIAL YOU ALREADY HAVE. It is optional historical context, not "
            "a current account. Use it only when the user's new message "
            "connects to it; otherwise stay with the new message. If it is "
            "empty or unrelated, say plainly that you do not have the recent "
            "picture instead of asking a broad question."
        )
    return heading + "\n\n" + "\n\n".join(parts)
