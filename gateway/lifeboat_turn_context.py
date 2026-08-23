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


def _clean(text: str) -> str:
    value = _REPLY_QUOTE.sub("", str(text or "")).strip()
    value = _SPEAKER_PREFIX.sub("", value)
    return " ".join(value.split())[:MAX_LINE_CHARS]


#: Everything before the topics were split still sits in the shared log. Moving
#: those months could not be done safely -- the topics are not separable there
#: -- but reading them is: a wrongly-included line is a stray sentence, not a
#: corrupted record, and the operational filter drops the bug and deploy talk.
LEGACY_DIR = VAULT / "_System" / "Hermes Turn Logs" / "default"


def recent_user_turns(
    transcript_dir: Path | None = None,
    *,
    limit: int = MAX_TURNS,
    legacy_dir: Path | None = None,
):
    """Return what he actually said recently, newest last.

    Only his own words. The bot's replies are not evidence about his life, and
    feeding them back is how a conversation starts circling itself.
    """
    root = transcript_dir if transcript_dir is not None else TRANSCRIPT_DIR
    legacy = legacy_dir if legacy_dir is not None else LEGACY_DIR
    collected: list[tuple[str, str]] = []
    for source in (legacy, root):
        try:
            for path in sorted(source.glob("*.md"))[-7:]:
                text = path.read_text(encoding="utf-8")
                for stamp, said in _USER_BLOCK.findall(text):
                    if _INJECTED_NOTE_RE.match(str(said or "").strip()):
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


def build_turn_context(
    *,
    transcript_dir: Path | None = None,
    legacy_dir: Path | None = None,
    queue_text: str | None = None,
    journal_entries=None,
) -> str:
    """Assemble the material this turn may draw on, or an empty string."""
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

    turns = recent_user_turns(transcript_dir, legacy_dir=legacy_dir)
    if turns:
        said = "\n".join(f"- {stamp[:10]}: {line}" for stamp, line in turns)
        parts.append(
            "WHAT HE HAS SAID RECENTLY (his words; use them to be specific, "
            "never quote them back as proof of attentiveness):\n" + said
        )

    if not parts:
        return ""
    return (
        "MATERIAL YOU ALREADY HAVE. Use it to arrive with a read rather than "
        "asking him to supply the facts. If it is empty, say plainly that you "
        "do not have the recent picture instead of asking a broad question.\n\n"
        + "\n\n".join(parts)
    )
