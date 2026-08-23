"""Recognise when Noam says the bot got it wrong.

Corrections used to be just conversation. He would say a subject had passed, or
that a reading was wrong, and nothing changed: the same subject could return the
next day and the same reading could be repeated. What he says is the most
reliable signal in the whole system, and it was the one being discarded.

Only plain language is read here — nothing to remember, no command to learn, and
no inference about how he feels. A correction is recognised because he wrote
one, or it is not recognised at all.
"""

from __future__ import annotations

from enum import Enum
import re


class Correction(str, Enum):
    """What Noam just told the bot about its last reply."""

    NONE = "none"
    ENGAGED = "engaged"
    PASSED = "passed"
    DISMISSED = "dismissed"
    MISREAD = "misread"


#: "Stop raising this" — final, and stronger than saying it has passed.
_DISMISSED_RE = re.compile(
    r"(?:תפסיק\s+(?:לשאול|להעלות)|אל\s+ת(?:עלה|שאל).{0,12}(?:יותר|שוב)|"
    r"די\s+עם\s+זה|"
    r"\bdrop it\b|stop (?:bringing|asking) (?:this|about))",
    re.IGNORECASE,
)

#: "That's over" — the subject is done, but not forbidden.
_PASSED_RE = re.compile(
    r"(?:זה\s+עבר|כבר\s+לא\s+(?:רלוונטי|מעסיק|רלבנטי)|לא\s+רלוונטי\s+יותר|"
    r"\bthat'?s? passed\b|\bnot relevant (?:any\s?more|anymore)\b|\bthat'?s over\b)",
    re.IGNORECASE,
)

#: "You read that wrong" — the subject stands, the interpretation does not.
_MISREAD_RE = re.compile(
    r"(?:לא\s+דייקת|זה\s+לא\s+מה\s+ש(?:אמרתי|כתבתי|התכוונתי)|פירשת\s+לא\s+נכון|"
    r"לא\s+הבנת\s+נכון|"
    r"\bthat'?s not what i (?:said|meant)\b|\byou misread\b|\bmisunderstood\b)",
    re.IGNORECASE,
)

#: Taking it up. Kept narrow: agreement, not merely a reply.
_ENGAGED_RE = re.compile(
    r"(?:^|\s)(?:כן,?\s*בדיוק|נכון|בדיוק|בוא\s+נמשיך|נמשיך\s+עם\s+זה|"
    r"yes,?\s*exactly|exactly|that'?s right)(?:$|[\s,.!])",
    re.IGNORECASE,
)

#: A question about why something was raised is not an instruction about it.
_QUESTION_RE = re.compile(r"^\s*(?:למה|מדוע|why)\b.*\?\s*$", re.IGNORECASE)


def classify_correction(text: str | None) -> Correction:
    """Return what this message says about the bot's last reply.

    Order matters: a dismissal outranks "it passed", because "it passed, stop
    asking" is an instruction and treating it as merely closed would let the
    subject come back.
    """
    value = " ".join(str(text or "").split()).strip()
    if not value or _QUESTION_RE.match(value):
        return Correction.NONE

    if _DISMISSED_RE.search(value):
        return Correction.DISMISSED
    if _PASSED_RE.search(value):
        return Correction.PASSED
    if _MISREAD_RE.search(value):
        return Correction.MISREAD
    if _ENGAGED_RE.search(value):
        return Correction.ENGAGED
    return Correction.NONE
