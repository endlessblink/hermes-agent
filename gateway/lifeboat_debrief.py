"""A debrief that leads, without inventing what it does not know.

Asked how to do a debrief, Life-Boat gave a correct instruction: do not order
it in advance, dump everything as it is, I will hold the structure. Correct,
and it cost Noam more than a short interview would have. Facing an open dump is
itself the stressful part.

So the debrief leads. One question per turn, anchored where there is something
to anchor to, and free to walk into ground it knows nothing about.

The limit is on claims, not on subjects -- that distinction is the whole design.
Restricting it to subjects already discussed would keep it safe and make it
useless, since it could then only ever ask about what he already tells it. What
actually went wrong on 2026-08-12 was not breadth: the check-in invented a
Hebrew quote he had never said. That is a fabricated *claim*.

So: anything stated as already true about him must come from something he said
or wrote. A question into unknown territory asserts nothing and is allowed --
unless it smuggles the assumption in, which is the difference between "how did
it go with your sister" (there was something with his sister) and "anything on
the family side?" (nothing assumed).

One area per debrief, never a sweep. Asking about eight life areas in a row is
the exhaustive intake survey the reviewer already rejects.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


#: Used when the vault note is missing or unreadable. Deliberately short: a
#: long list invites the sweep this module exists to prevent.
_FALLBACK_AREAS: tuple[str, ...] = (
    "עבודה",
    "כסף",
    "בריאות",
    "שינה",
    "קשר וזוגיות",
    "משפחה",
    "בית",
    "יצירה",
)

#: The heading the areas live under, in either language.
_AREAS_HEADINGS = frozenset({"areas", "אזורים", "תחומים"})

_DEBRIEF_RE = re.compile(r"(?:דיבריף|תחקיר|\bdebrief\b|/debrief)", re.IGNORECASE)

#: Taking the lead back. He is asking to be left alone with it, not asking a
#: question about it -- a question is a question, not a request to dump.
_OPEN_DUMP_RE = re.compile(
    r"(?:פשוט\s+לשפוך|לשפוך\s+(?:את\s+)?הכל|תן\s+לי\s+לדבר|בלי\s+שאלות|"
    r"אל\s+תשאל|לא\s+רוצה\s+שאלות|"
    r"\bjust\s+dump\b|\blet\s+me\s+(?:just\s+)?(?:talk|dump)\b|"
    r"\bwithout\s+questions\b|\bno\s+questions\b)",
    re.IGNORECASE,
)

#: Announcing the procedure. The first real debrief opened by explaining that
#: it would interview him, hold the threads, and spare him managing the
#: structure. Noam: too much like a therapist, and that creates distance. A
#: person who knows you does not brief you on their method before asking.
_METHOD_NARRATION_RE = re.compile(
    r"(?:אני\s+(?:א|נ)?(?:ראיין|שאל\s+אותך\s+שאלה\s+אחת)"
    r"|שאלה\s+אחת\s+בכל\s+פעם"
    r"|א(?:שמור|חזיק)\s+(?:את\s+)?(?:כל\s+)?ה?חוט"
    r"|אשאיר\s+מקום\s+למה\s+שחדש"
    r"|לנהל\s+את\s+המבנה"
    r"|בלי\s+שתצטרך"
    r"|\bI(?:'ll| will)\s+(?:interview|guide|hold)\b"
    r"|one\s+question\s+at\s+a\s+time)",
    re.IGNORECASE,
)

#: The clinical register itself: threads, holding space, what it activates in
#: you, processing. Each of these is a therapist's word for something ordinary,
#: and using it puts a desk between them.
_THERAPIST_REGISTER_RE = re.compile(
    r"(?:ה?חוט\s+(?:הפעיל|הרגשי)|ה?חוטים\s+ש"
    r"|לה?חזיק\s+(?:לך\s+)?מקום|מחזיק\s+לך\s+מקום|אחזיק\s+לך\s+מקום"
    r"|מה\s+זה\s+מפעיל\s+אצלך|מה\s+(?:זה\s+)?מעורר\s+אצלך"
    r"|[לנתא]עבד\s+את\s+(?:זה|ה)|עיבוד\s+רגשי|להכיל\s+את"
    r"|להישאר\s+עם\s+(?:זה|התחושה)|מרחב\s+בטוח"
    r"|מה\s+נשאר\s+(?:איתך|אתך)|מה\s+זה\s+עשה\s+לך|איפה\s+זה\s+יושב"
    r"|\bwhat\s+stayed\s+with\s+you\b|\bwhere\s+does\s+(?:that|this)\s+sit\b"
    r"|\bhold(?:ing)?\s+space\b|\bprocess\s+this\b|\bsit\s+with\s+(?:that|this)\b"
    r"|\bwhat\s+(?:does\s+)?(?:that|this)\s+bring\s+up\s+for\s+you\b)",
    re.IGNORECASE,
)

#: Asking him to pick the subject. He asked to be interviewed; an interviewer
#: chooses the thread. "מה היה הדבר הראשון שהיה לך בראש?" is the open dump
#: wearing a question mark -- his words, 2026-08-23: "ועכשיו שוב אתה לא מראיין
#: אותי אלא מפיל עליי את האחריות".
_STEERING_HANDBACK_RE = re.compile(
    r"(?:ה?דבר\s+הראשון\s+ש(?:היה|עולה|בא)"
    r"|מה\s+היה\s+הדבר\s+הראשון(?:\s+[^?]{0,50})?"
    r"|מה\s+(?:היה\s+)?עולה\s+לך\s+ראשון"
    r"|ב?מה\s+(?:תרצה|היית\s+רוצה)\s+(?:להתחיל|לדבר|שנתחיל)"
    r"|מה\s+תרצה\s+ש"
    r"|(?:מ?איפה|ממה|במה)\s+(?:אתה\s+)?(?:רוצה|מעדיף|תרצה|היית\s+רוצה)?\s*(?:ש?נתחיל|להתחיל)"
    r"|מאיזה\s+(?:רגע|יום|אירוע|מקום)\s+(?:אתה\s+)?(?:רוצה|מעדיף|תרצה|היית\s+רוצה)?\s*להתחיל"
    r"|איזו?\s+(?:סצנה|תחושה|רגע|מחשבה|תמונה)\s+עולה\s+(?:לך\s+)?ראשונה"
    r"|מה\s+קרה\s+ביום\s+הראשון\s+ש(?:אתה\s+)?רוצה\s+לכלול"
    r"|מה\s+היה\s+(?:האירוע|הרגע)\s+המוקדם\s+ביותר(?:\s+[^?]{0,60})?"
    r"|לבחור\s+(?:מאיפה|במה|מה)"
    r"|מה\s+הכי\s+(?:חשוב|דחוף)\s+לך\s+(?:לדבר|להתחיל)"
    r"|\bwhat\s+would\s+you\s+like\s+to\s+(?:start|talk|begin)"
    r"|\bwhat(?:'s| is)\s+the\s+first\s+thing\s+that\s+comes"
    r"|\bwhere\s+(?:should|do)\s+(?:we|you)\s+(?:want\s+to\s+|prefer\s+to\s+)?start"
    r"|\bwhat\s+do\s+you\s+want\s+to\s+talk\s+about)",
    re.IGNORECASE,
)

#: Restating the correction before answering it. Every time Noam corrected the
#: tone, the next reply opened with a paragraph agreeing and describing what it
#: had done wrong. The fix for a bad turn is a good turn.
_SELF_CORRECTION_PREAMBLE_RE = re.compile(
    r"(?:^\s*(?:נכון|צודק|אתה\s+צודק)\s*[,.\u2014-]"
    r"|ביקשת\s+ש.{0,40}?\s+ואני"
    r"|אני\s+ביקשתי\s+ממך"
    r"|דיברתי\s+אליך\s+כאילו"
    r"|אז\s+אני\s+מתחיל\s+ספציפית"
    r"|^\s*(?:you(?:'re| are)\s+right|right|fair(?:\s+enough)?)\s*[,.\u2014-])",
    re.IGNORECASE,
)

#: Telling him to narrate, and handing him the headings to narrate under.
#: 2026-08-23 20:46: "תתחיל מהאירוע הראשון כפי שהוא קרה—מי היה שם, מה קרה
#: בפועל, ומה נשאר איתך אחריו." That is the open dump with homework attached.
_ASSIGNS_THE_TELLING_RE = re.compile(
    r"(?:(?:תתחיל|ספר\s+לי|תספר)\b[^?!\n]{0,120}?[:\u2014-][^?!\n]{0,140}?,"
    r"[^?!\n]{0,140}?\b(?:ו?מה|ואיך)\b"
    r"|\b(?:start\s+with|tell\s+me)\b[^?!\n]{0,120}?[:\u2014-][^?!\n]{0,140}?,"
    r"[^?!\n]{0,140}?\band\s+what\b)",
    re.IGNORECASE,
)

#: A read the bot is offering rather than asserting. Noam asked for exactly
#: this: it should be working to understand him, not extracting material from
#: him. Marked as fallible, it is not a claim about his life -- it is the bot
#: doing the thinking and showing its work so he can correct it in one word.
_TENTATIVE_READ_RE = re.compile(
    r"(?:נשמע\s+(?:לי\s+)?ש|נדמה\s+לי\s+ש|אולי\s|יכול\s+להיות\s+ש"
    r"|התחושה\s+שלי\s+ש|אם\s+אני\s+מבין\s+נכון"
    r"|\bit\s+sounds\s+like\b|\bit\s+seems\b|\bmaybe\b|\bmy\s+read\s+is\b)",
    re.IGNORECASE,
)

#: And it must actually be offered: a read with no invitation to correct it is
#: just an assertion with a softener in front.
_INVITES_CORRECTION_RE = re.compile(
    r"(?:זה\s+מדויק|קרוב\?|טועה\?|נכון\?|או\s+שלא|תקן\s+אותי"
    r"|זה\s+הכיוון|זה\s+הכיוון\s*\?|\bis\s+that\s+right\b|\bam\s+i\s+wrong\b|\bclose\?)",
    re.IGNORECASE,
)

# A productive next step is chosen by the assistant, not delegated back as a
# menu.  This deliberately describes the grammatical role (assistant commits
# to a scope, then asks about that scope) rather than enumerating bad wording.
_ASSISTANT_STEP_RE = re.compile(
    r"(?:^|[.!?؟]\s*)(?:בוא(?:\s+נ)?|ניקח|נתחיל|נפתח|נעבור|"
    r"אני\s+(?:לוקח|מתחיל|פותח|ממקד)|let's|we(?:'ll|\s+will)|"
    r"i(?:'ll|\s+will)\s+(?:start|take|focus)\b)",
    re.IGNORECASE,
)
_USER_SELECTION_RE = re.compile(
    r"(?:שאתה\s+(?:רוצה|תרצה|מעדיף|תבחר)|מה\s+שתרצה|"
    r"(?:you\s+)?want\s+to\s+(?:include|choose|start)|"
    r"your\s+(?:choice|selection))",
    re.IGNORECASE,
)
_CHAT_META_ANCHOR_RE = re.compile(
    r"(?:ההודעה\s+שכתבת|החלטת\s+לכתוב|לפני\s+שכתבת|"
    r"המשפט\s+האחרון\s+שנאמר|מה\s+נאמר\s+לפני\s+ההודעה|"
    r"the\s+message\s+you\s+wrote|before\s+you\s+wrote|"
    r"before\s+you\s+decided\s+to\s+write)",
    re.IGNORECASE,
)

#: A debrief over a period, rather than about one thing. "Let's start the
#: debrief again" opens the whole span; it does not resume the thread that
#: happened to be live last time.
_BROAD_REQUEST_RE = re.compile(
    r"(?:דיבריף|תחקיר|\bdebrief\b)"
    r"(?![^.!?\n]{0,40}?\b(?:על\s+(?:מה\s+ש|ה)?[\wא-ת]{3,})\b)",
    re.IGNORECASE,
)
_PERIOD_RE = re.compile(
    r"(?:התקופה\s+האחרונה|הימים\s+האחרונים|השבוע|מהתקופה|בזמן\s+האחרון|"
    r"לאחרונה|בשבועות\s+האחרונים|recent(?:\s+(?:events|period))?|"
    r"the\s+week|lately)",
    re.IGNORECASE,
)

#: Reopening a single earlier thread instead of the period.
_RESUMES_ONE_THREAD_RE = re.compile(
    r"(?:חוזרים\s+ל|נחזור\s+ל|נמשיך\s+מ(?:איפה\s+ש)?עצרנו|נתחיל\s+משם"
    r"|\bpicking\s+up\s+where\b|\bback\s+to\s+the\b)",
    re.IGNORECASE,
)

_QUESTION_RE = re.compile(r"[?？]")
_SENTENCE_RE = re.compile(r"[^.!?？\n]+[.!?？]?")

#: "How did it go with X" / "what happened with X" -- the shape that treats an
#: event as settled. Captures the subject so it can be checked against what he
#: actually said.
_PRESUPPOSING_RE = re.compile(
    r"(?:איך\s+(?:היה|הלך|עבר)\s+(?:לך\s+)?(?:עם|ב|ה)?\s*(?P<he>[\wא-ת֐-׿'\"]+)"
    r"|מה\s+(?:קרה|היה)\s+(?:עם|ב)\s*(?P<he2>[\wא-ת֐-׿'\"]+)"
    r"|how\s+did\s+(?:it\s+go\s+with|the)\s+(?:your|the|his|her|their|my)?\s*(?P<en>[\w']+)"
    r"|what\s+happened\s+with\s+(?:your|the|his|her|their|my)?\s*(?P<en2>[\w']+))",
    re.IGNORECASE,
)

#: Words too common to prove a statement is grounded in what he said.
_STOPWORDS = frozenset(
    """
    את אתה אני זה זאת הוא היא הם הן אנחנו כל כך גם רק עוד יותר פחות
    מה איך למה מתי איפה אם או אבל כי של על עם בלי אצל אותו אותה
    היה היתה הייתה יהיה להיות יש אין לא כן עכשיו כרגע היום אתמול מחר
    שזה שזאת שלי שלך שלו שלה הכי מאוד ממש קצת רגע דבר משהו נשמע שומע
    אמרת אמר אמרה כשעצרת עומד עומדת נגענו נגעתי הזה הזאת
    that this what how why when with without your you the and for was were
    said sound hear about there here still now
    """.split()
)

_TOKEN_RE = re.compile(r"[\w֐-׿]{3,}", re.UNICODE)
_HEBREW_RE = re.compile(r"[֐-׿]")


def _content_tokens(text: str | None) -> set[str]:
    """Words specific enough to tie a sentence to a particular conversation."""
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(str(text or "")):
        word = raw.strip("־-").casefold()
        if not word or word in _STOPWORDS:
            continue
        tokens.add(word)
        # Hebrew glues prefixes onto nouns (ה/ו/ב/ל/מ/כ/ש); keep the stem so
        # "המשפחה" and "משפחה" count as the same subject.
        if _HEBREW_RE.match(word) and len(word) > 3 and word[0] in "הובלמכש":
            tokens.add(word[1:])
        if _HEBREW_RE.match(word) and len(word) > 4 and word.endswith(("ך", "י", "ו", "ה")):
            tokens.add(word[:-1])
    return tokens


def _is_known(word: str, known: set[str]) -> bool:
    candidates = _content_tokens(word)
    return bool(candidates & known) if candidates else True


@dataclass(frozen=True)
class DebriefState:
    """What this debrief has already done."""

    active: bool = False
    open_dump: bool = False
    areas_opened: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "open_dump": self.open_dump,
            "areas_opened": list(self.areas_opened),
        }

    @classmethod
    def from_dict(cls, value) -> "DebriefState":
        if not isinstance(value, dict):
            return cls()
        opened = value.get("areas_opened")
        return cls(
            active=bool(value.get("active")),
            open_dump=bool(value.get("open_dump")),
            areas_opened=tuple(str(item) for item in opened) if isinstance(opened, list) else (),
        )


def is_debrief_request(text: str | None) -> bool:
    """Return True when he is asking for a debrief."""
    return bool(_DEBRIEF_RE.search(str(text or "")))


def wants_open_dump(text: str | None) -> bool:
    """Return True when he wants to be left to spill it rather than led."""
    value = str(text or "")
    if _QUESTION_RE.search(value) and not _OPEN_DUMP_RE.search(value):
        return False
    return bool(_OPEN_DUMP_RE.search(value))


def load_life_areas(note_text: str | None) -> tuple[str, ...]:
    """Read the areas from the vault note, falling back to a built-in list.

    The map is written rather than derived on purpose. A derived one could only
    ever contain what he already talks about, which is the limitation this
    whole design exists to remove.
    """
    collected: list[str] = []
    in_areas = False
    for line in str(note_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            # The note explains itself in prose, and that prose uses bullets
            # too, so only the Areas section counts.
            in_areas = stripped.lstrip("#").strip().casefold() in _AREAS_HEADINGS
            continue
        if in_areas and stripped.startswith("- ") and stripped[2:].strip():
            collected.append(stripped[2:].strip())
    return tuple(collected) or _FALLBACK_AREAS


def next_area(
    areas: tuple[str, ...],
    state: DebriefState,
    *,
    recent_text: str | None = "",
) -> str | None:
    """Return the area that has been quiet longest, or None if none is left."""
    recent = _content_tokens(recent_text)
    remaining = [area for area in areas if area not in state.areas_opened]
    if not remaining:
        return None
    quiet = [area for area in remaining if not (_content_tokens(area) & recent)]
    return (quiet or remaining)[0]


def is_broad_debrief(request: str | None) -> bool:
    """True when he opened the period, not one subject inside it."""
    text = str(request or "")
    if not text.strip():
        return False
    if _PERIOD_RE.search(text):
        return True
    return bool(_BROAD_REQUEST_RE.search(text))


def debrief_problems(
    response: str | None,
    *,
    known_text: str | None = "",
    areas: tuple[str, ...] = (),
    request: str | None = "",
) -> tuple[str, ...]:
    """Name every way this debrief turn breaks its shape."""
    text = " ".join(str(response or "").split()).strip()
    if not text:
        return ("empty",)

    known = _content_tokens(known_text)
    issues: list[str] = []

    if is_broad_debrief(request) and _RESUMES_ONE_THREAD_RE.search(text):
        issues.append("narrowed_a_broad_debrief")
    if _ASSIGNS_THE_TELLING_RE.search(text):
        issues.append("assigned_him_the_telling")
    if _STEERING_HANDBACK_RE.search(text):
        issues.append("handed_back_the_steering")
    if _SELF_CORRECTION_PREAMBLE_RE.search(text):
        issues.append("self_correction_preamble")
    if _METHOD_NARRATION_RE.search(text):
        issues.append("method_narration")
    if _THERAPIST_REGISTER_RE.search(text):
        issues.append("therapist_register")

    questions = len(_QUESTION_RE.findall(text))
    if questions > 1:
        issues.append("too_many_questions")
    elif questions == 0:
        issues.append("no_question")

    for match in _PRESUPPOSING_RE.finditer(text):
        subject = next(
            (value for value in match.groupdict().values() if value), ""
        )
        if subject and not _is_known(subject, known):
            issues.append("presupposed_event")
            break

    offered_as_a_read = bool(
        _TENTATIVE_READ_RE.search(text) and _INVITES_CORRECTION_RE.search(text)
    )
    assistant_step = False
    if is_broad_debrief(request) and not offered_as_a_read:
        # A question can be perfectly grammatical while doing none of the
        # thinking.  For a broad opening, require either a read offered for
        # correction or a scope the assistant has actually chosen.  The latter
        # must not be conditional on the user selecting the scope first.
        assistant_step = (
            bool(_ASSISTANT_STEP_RE.search(text))
            and not bool(_USER_SELECTION_RE.search(text))
            and not bool(_CHAT_META_ANCHOR_RE.search(text))
        )
        if not assistant_step:
            issues.append("assistant_did_not_advance")
    for sentence in _SENTENCE_RE.findall(text):
        clean = sentence.strip()
        if not clean or _QUESTION_RE.search(clean):
            continue
        if offered_as_a_read:
            # Hedged and open to correction: thinking, not asserting.
            continue
        specific = _content_tokens(clean)
        if specific and not (specific & known) and not offered_as_a_read and not assistant_step:
            issues.append("unsourced_continuity")
            break

    if areas:
        named = [area for area in areas if _content_tokens(area) & _content_tokens(text)]
        if len(named) >= 2:
            issues.append("area_sweep")

    return tuple(issues)


def build_debrief_guidance(
    *,
    anchors: tuple[str, ...] = (),
    area: str | None = None,
    broad: bool = False,
) -> str:
    """Tell the model the shape, never the words.

    Supplying an example sentence is exactly how the canned opener returned
    last time, so this names the constraints and the material and stops.
    """
    lines = [
        "DEBRIEF SHAPE: lead with one question and nothing else. Do not tell him "
        "to dump everything, do not lay out stages, do not offer a menu.",
        "Do not describe what you are about to do. Do not announce that you will "
        "interview him, hold anything, or spare him managing the structure. Ask.",
        "Speak the way someone who knows him speaks. Not threads, not holding "
        "space, not what this activates in him, not processing.",
        "Anything you state as already true about him must come from the anchors "
        "below. If it is not there, ask instead of asserting.",
        "You may ask about ground you know nothing about, provided the question "
        "assumes nothing happened.",
        "Open at most one new area this debrief.",
        "You choose the thread, not him. Never ask him what to start with, what "
        "is most important, or what comes to mind first -- that is the dump again.",
        "If he corrects you, do not agree, apologise, or describe what you did "
        "wrong. Ask the better question instead.",
    ]
    if broad:
        lines.append(
            "He opened the period, not one subject inside it. Do not resume the "
            "thread that happened to be live last time; that is his whole "
            "conversation reduced to one thing."
        )
    if area:
        lines.append(f"AREA THAT HAS BEEN QUIET: {area}")
    lines.append("ANCHORS (the only things he has actually said):")
    lines.extend(f"- {anchor}" for anchor in anchors) if anchors else lines.append(
        "- none; do not refer to anything earlier"
    )
    return "\n".join(lines)


def _state_path(profile_home: Path, session_key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_key or "default"))
    return Path(profile_home) / "state" / f"lifeboat-debrief-{safe}.json"


def load_debrief_state(profile_home: Path, session_key: str) -> DebriefState:
    try:
        return DebriefState.from_dict(
            json.loads(_state_path(profile_home, session_key).read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError):
        return DebriefState()


def save_debrief_state(profile_home: Path, session_key: str, state: DebriefState) -> None:
    path = _state_path(profile_home, session_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False), encoding="utf-8")


def clear_debrief_state(profile_home: Path, session_key: str) -> bool:
    try:
        _state_path(profile_home, session_key).unlink()
        return True
    except OSError:
        return False
