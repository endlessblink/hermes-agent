"""Read the notes Life-Boat already keeps, so a reply can be continuous.

The bot kept a daily journal, a weekly rollup and a queue of parked subjects,
and consulted none of them. It scraped the last few chat messages instead,
which is why a check-in could open by asking about a deploy while a subject the
user had deliberately parked sat untouched for a month.

Everything here is read-only and bounded. It offers material the user wrote
himself -- a pattern he named, a sentence he balanced, a subject he parked --
and never a conclusion drawn about him. The block it produces says plainly that
the material may have passed, because a month-old worry quoted as current is
its own kind of wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


#: Enough to be continuous, not so much that a reply becomes a digest.
MAX_JOURNAL_LINES = 12
MAX_QUEUE_ITEMS = 4
MAX_LINE_CHARS = 240

#: Journal headings worth opening from. Things done and good moments are real
#: but they are records, not threads; a pattern the user named and a sentence
#: he balanced are the parts still in motion.
_OPENABLE_HEADINGS = (
    "דפוס ששמתי לב אליו",
    "משפט מאוזן",
    "מחשבת מפולת",
    "דברים קשים",
)

_HEADING_RE = re.compile(r"^##\s*(.+?)\s*$", re.M)
_ITEM_RE = re.compile(
    r"^-\s*id:\s*(?P<id>\S+)\s*$"
    r"(?P<body>(?:\n\s+\w[^\n]*)*)",
    re.M,
)
_FIELD_RE = re.compile(r"^\s+(\w+):\s*(.*)$", re.M)


@dataclass(frozen=True)
class QueueItem:
    """One parked subject, in the user's own words."""

    id: str
    status: str
    added: str
    topic: str
    next_point: str


def parse_queue(text: str | None) -> tuple[QueueItem, ...]:
    """Parse the emotional-processing queue. Never raises on malformed text."""
    body = str(text or "")
    if not body.strip():
        return ()

    items: list[QueueItem] = []
    for match in _ITEM_RE.finditer(body):
        fields = dict(_FIELD_RE.findall(match.group("body") or ""))
        items.append(
            QueueItem(
                id=match.group("id").strip(),
                status=fields.get("status", "").strip(),
                added=fields.get("added", "").strip(),
                topic=fields.get("topic", "").strip(),
                next_point=fields.get("next_point", "").strip(),
            )
        )
    return tuple(items)


def _sections(entry: str) -> dict[str, str]:
    """Split one journal entry into heading -> body."""
    parts: dict[str, str] = {}
    headings = list(_HEADING_RE.finditer(entry))
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(entry)
        parts[heading.group(1).strip()] = entry[heading.end():end].strip()
    return parts


def recent_journal_lines(entries) -> tuple[str, ...]:
    """Return dated lines worth opening from, newest last and bounded."""
    collected: list[str] = []
    for date, entry in entries or ():
        sections = _sections(str(entry or ""))
        for heading, body in sections.items():
            if not any(openable in heading for openable in _OPENABLE_HEADINGS):
                continue
            for line in body.splitlines():
                value = " ".join(line.split()).strip("-• ").strip()
                if value:
                    collected.append(f"[{date}] {value[:MAX_LINE_CHARS]}")
    return tuple(collected[-MAX_JOURNAL_LINES:])


def build_context_block(*, queue_text=None, journal_entries=None) -> str:
    """Assemble the bounded context a reply may draw on, or an empty string."""
    try:
        items = [
            item for item in parse_queue(queue_text)
            if item.status in ("active", "pending") and item.topic
        ][:MAX_QUEUE_ITEMS]
        lines = recent_journal_lines(journal_entries)
    except Exception:  # noqa: BLE001 — context is an improvement, never a dependency
        return ""

    if not items and not lines:
        return ""

    out = [
        "LIFE-BOAT CONTEXT — material Noam wrote himself. You may name at most "
        "one thing from it, only if it is plausibly still live. Use his wording. "
        "Do not invent anything that is not written here, do not assume he still "
        "feels the same, and make it easy for him to say it has passed.",
    ]
    if items:
        out.append("Parked subjects, newest first:")
        for item in items:
            marker = "active" if item.status == "active" else "waiting"
            out.append(f"  - ({marker}, since {item.added}) {item.topic} — next: {item.next_point}")
    if lines:
        out.append("Recent lines from his journal:")
        out.extend(f"  {line}" for line in lines)
    return "\n".join(out)
