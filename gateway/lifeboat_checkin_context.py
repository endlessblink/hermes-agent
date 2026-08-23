"""Choose what a Life-Boat check-in may open from.

Two failures shaped this. On 2026-08-12 the check-in was told to find "one
relevant detail for continuity", had nothing to draw on, and invented a quote
in Hebrew that Noam had never said. Removing the instruction stopped the
fabrication but left the message generic. On 2026-08-22 the feed worked and
handed it a full day of bot debugging, so a support check-in would have opened
by asking about a deploy.

The rule that follows from both: offer only material the person brought as
their own, drop operational chatter entirely, and when nothing qualifies say so
plainly rather than reach for filler. A check-in that opens with an ordinary
hello is fine. One that invents continuity, or asks about a deploy, is not.
"""

from __future__ import annotations

import re


#: Never hand back more than this, so a check-in cannot become a digest.
MAX_ANCHORS = 4
MAX_ANCHOR_CHARS = 300

NOTHING_RECENT = (
    "RECENT CONTEXT: none available. Noam has not written anything personal "
    "recently, or the conversation could not be read. Open plainly with an "
    "ordinary greeting and one simple question. Do not refer to anything "
    "earlier, and do not invent a detail to sound attentive."
)

#: Work talk. A support check-in opening on any of this is a category error --
#: it is the assistant asking about its own errands.
_OPERATIONAL_RE = re.compile(
    r"(?:באג|תקלה|קוד|קומיט|דיפלוי|לוג|טסט|בדיק|סטטוס|תריץ|הרץ|תעצור|תבדוק|בדוק|"
    r"מה השתנה|ניתוח עצמי|עובד\?|קודקס|הבוט|גייטוויי|"
    r"לאבחן|אבחון|לזהות|שגיאה|נכשל|מסירה|"
    r"\bbug\b|\bfix\b|\bcode\b|\bcommit\b|\bdeploy\b|\blog\b|\btest\b|\bstatus\b|"
    r"\brun\b|\brestart\b|\bgateway\b|\bcodex\b|\bbot\b)",
    re.IGNORECASE,
)

#: A question about the assistant's own capabilities is work talk however it
#: is phrased -- "why can't you tell" is not the person describing their day.
_ABOUT_THE_ASSISTANT_RE = re.compile(
    r"(?:למה\s+(?:אין\s+לך|אתה\s+לא)|אתה\s+לא\s+(?:מצליח|יודע|מזהה)|"
    r"why\s+(?:can't|don't|doesn't)\s+(?:you|it))",
    re.IGNORECASE,
)

#: Things he says *to* the assistant about the assistant: instructions on how
#: to talk to him, corrections of its conduct, requests about the conversation
#: itself. These are not events in his life, and the 2026-08-23 replay proved
#: what happens when they are treated as such: "השניים האחרונים הרבה יותר
#: טובים" -- his praise of two bot replies -- was handed over as material and
#: came back to him as "the last two days were much better than the ones
#: before". A confident false read of his week, assembled from a code review.
#:
#: The keyword list for work talk above could never have caught that line, and
#: lengthening it is the wrong move for the same reason it is wrong everywhere
#: else in this system. The rule here is structural instead: a sentence whose
#: subject is the assistant is not evidence about him.
_ADDRESSED_TO_THE_ASSISTANT_RE = re.compile(
    r"(?:^|\s)(?:"
    # Hebrew imperatives and requests aimed at the assistant.
    r"בבקשה\s+ת|תשמור|תדאג|תזכור|תפסיק|תתחיל|תמשיך|תראיין|תשאל|תדבר|דבר\s+אליי|"
    r"תענה|תגיב|תסביר|תכתוב|תשנה|תוסיף|בוא\s+נתחיל|בוא\s+ננסה|אני\s+מעדיף\s+ש|"
    r"אני\s+רוצה\s+ש(?:את[הי])?|"
    # Comments on how the assistant is behaving.
    r"אתה\s+(?:לא|שוב|כל\s+פעם|תמיד)|אתה\s+מ[א-ת]+\s+(?:אותי|לי|עליי)|"
    r"מה\s+שלמדת|מה\s+שאמרתי\s+לך|"
    # English, which in this thread is always him giving the system direction.
    r"\byou\s+(?:should|need\s+to|must|keep|always|never)\b|"
    r"\bi\s+want\s+(?:you|this|it)\b|\btell\s+me\s+about\s+yourself\b|"
    r"\bimplement\b|\bexposed?\s+to\b|\borchestrator\b|\bagents?\b|\bprompt\b"
    r")",
    re.IGNORECASE,
)


def is_addressed_to_the_assistant(text: str | None) -> bool:
    """True when the sentence is about the assistant rather than about him."""
    value = " ".join(str(text or "").split())
    if not value:
        return False
    return bool(_ADDRESSED_TO_THE_ASSISTANT_RE.search(value))


#: A short command or acknowledgement carries nothing to open from either.
_MIN_SUBSTANTIVE_CHARS = 12


def is_operational(text: str | None) -> bool:
    """Return True when this turn is about running the system, not about him."""
    value = " ".join(str(text or "").split())
    if not value:
        return False
    return bool(
        _OPERATIONAL_RE.search(value)
        or _ABOUT_THE_ASSISTANT_RE.search(value)
        or _ADDRESSED_TO_THE_ASSISTANT_RE.search(value)
    )


def _is_substantive(text: str) -> bool:
    return len(text) >= _MIN_SUBSTANTIVE_CHARS


def select_checkin_anchors(turns) -> tuple[str, ...]:
    """Return the recent turns a check-in may legitimately open from.

    Oldest first, newest last, bounded in both count and length. Returns an
    empty tuple when nothing qualifies -- the caller is expected to fall back
    to opening plainly rather than to soften the filter.
    """
    kept: list[str] = []
    for turn in turns or ():
        value = " ".join(str(turn or "").split()).strip()
        if not value or not _is_substantive(value) or is_operational(value):
            continue
        kept.append(value[:MAX_ANCHOR_CHARS].rstrip())
    return tuple(kept[-MAX_ANCHORS:])
