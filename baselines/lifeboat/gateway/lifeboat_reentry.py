"""Is this reply re-entering a conversation without naming anything in it?

Coming back from a technical detour, Life-Boat kept opening with a question
broad enough to fit any conversation with anyone: "מה הכי חי אצלך עכשיו, אם
בכלל?". Banning that sentence only taught the problem to reword itself -- the
example named in the bug report is already a reword of the banned line.

So the test here is shape, not wording. A re-entry is contextless when it is a
short generic question that shares nothing concrete with what the user has been
talking about. A question that names the interview, the manager, or the user's
own phrase is doing its job, whatever words it uses.
"""

from __future__ import annotations

import re


_QUESTION_RE = re.compile(r"[?？]")
_SENTENCE_RE = re.compile(r"[^.!?？]+[.!?？]?")

#: A re-entry is essentially only its question.  Anything more than this much
#: other prose means the reply is doing substantive work as well.
_MAX_CARRIED_PROSE_CHARS = 40
_HEBREW_RE = re.compile(r"[֐-׿]")

#: Above this, a reply is doing something other than re-entering.
_MAX_REENTRY_CHARS = 130

#: Openers that carry no subject of their own: they ask about "it", "this", or
#: an unnamed inner state, and would read identically in any conversation.
_GENERIC_OPENER_RE = re.compile(
    r"(?:"
    r"מה\s+(?:\S{2,8}\s+)?(?:הכי\s+)?(?:חי|נוכח|עולה|קורה)\s+(?:אצלך|בך|לך)"
    r"|מה\s+(?:\S{2,8}\s+)?(?:הכי\s+)?(?:חי|נוכח|עולה)\b"
    r"|איך\s+(?:זה|это)?\s*(?:יושב|נוחת|מרגיש|פוגש)\s*(?:איתך|אצלך|אותך)?"
    r"|מה\s+אתה\s+מרגיש\s+עכשיו"
    r"|what(?:'s| is)\s+(?:most\s+)?alive\s+for\s+you"
    r"|how\s+does\s+(?:that|this)\s+(?:land|sit|feel)"
    r"|what(?:'s| is)\s+(?:coming\s+up|present)\s+for\s+you"
    r")",
    re.IGNORECASE,
)

#: The process menu: pick feeling or pick action. Generic whatever the topic.
_PROCESS_BINARY_RE = re.compile(
    r"(?:רוצה\s+(?:ש?נחשוב|לחשוב).{0,40}\bאו\b.{0,60}(?:להישאר|לשבת|להיות)"
    r"|would you (?:rather|like to).{0,40}\bor\b.{0,60}(?:stay|sit) with)",
    re.IGNORECASE,
)

#: Safety triage legitimately offers an either/or and must never be filtered.
_SAFETY_RE = re.compile(
    r"(?:בטוח\s+עכשיו|סכנה|לפגוע\s+בעצמך|אובדנ|"
    r"\bsafe right now\b|\bin danger\b|\bhurt yourself\b)",
    re.IGNORECASE,
)

#: Words too common to prove a reply is anchored in this conversation.
_STOPWORDS = frozenset(
    """
    את אתה אני זה זאת הוא היא הם הן אנחנו כל כך גם רק עוד יותר פחות
    מה איך למה מתי איפה אם או אבל כי של על עם בלי אצל אותו אותה
    היה היתה יהיה להיות יש אין לא כן עכשיו כרגע היום אתמול מחר
    שזה שזאת שלי שלך שלו שלה הכי מאוד ממש קצת רגע דבר משהו
    that this what how why when with without your you the and for
    """.split()
)

_TOKEN_RE = re.compile(r"[\w֐-׿]{3,}", re.UNICODE)


def _content_tokens(text: str) -> set[str]:
    """Words specific enough to tie a sentence to a particular conversation."""
    tokens = set()
    for raw in _TOKEN_RE.findall(str(text or "")):
        word = raw.strip("־-").casefold()
        if word and word not in _STOPWORDS:
            tokens.add(word)
            # Hebrew glues prefixes onto nouns (ה/ו/ב/ל/מ/כ/ש); keep the stem
            # so "הראיון" and "ראיון" count as the same anchor.
            if _HEBREW_RE.match(word) and len(word) > 3 and word[0] in "הובלמכש":
                tokens.add(word[1:])
    return tokens


def is_contextless_reentry(response: str | None, *, user_text: str | None = "") -> bool:
    """Return True when this reply re-enters the conversation naming nothing in it.

    ``user_text`` is the material being re-entered. With none of it -- a fresh
    conversation -- a broad opening is a reasonable way to start, so nothing is
    rejected.
    """
    text = " ".join(str(response or "").split()).strip()
    prior = str(user_text or "").strip()
    if not text or not prior:
        return False
    if len(text) > _MAX_REENTRY_CHARS:
        return False
    if not _QUESTION_RE.search(text):
        return False
    if _SAFETY_RE.search(text):
        return False

    if not (_GENERIC_OPENER_RE.search(text) or _PROCESS_BINARY_RE.search(text)):
        return False

    # A substantive answer that merely ends generically is a different fault:
    # the tail is wrong, not the re-entry.  Leave that to the reply contract.
    carried = "".join(
        part for part in _SENTENCE_RE.findall(text) if not _QUESTION_RE.search(part)
    ).strip()
    if len(carried) > _MAX_CARRIED_PROSE_CHARS:
        return False

    # A generic opener is redeemed by naming something from the conversation.
    shared = _content_tokens(text) & _content_tokens(prior)
    return not shared
