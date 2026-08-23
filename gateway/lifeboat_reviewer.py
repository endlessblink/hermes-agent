"""Deterministic, fail-closed pre-delivery checks for Life-Boat replies.

This module is only a structural safety boundary: it can reject known
patterns, preserve explicit safety support, and fail closed on reviewer
failure. It cannot establish semantic relevance, independent model review,
or authenticated Telegram delivery; those remain separate gates.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import queue
import re
import threading
import time
from typing import Callable

from gateway.lifeboat_psychology import classify_lifeboat_signals

logger = logging.getLogger("gateway.lifeboat_reviewer")

_ORDINARY_SAFETY_RE = re.compile(r"self[- ]?harm|suicid|immediate danger|safe right now|emergency|פגיעה עצמית|אובדנ|סכנה מיידית|בטוח עכשיו|ער[״']?ן", re.IGNORECASE)
_DIRECT_SUPPORT_RE = re.compile(r"trusted person|local emergency|emergency services|crisis support|contact someone|ער[״']?ן|אדם קרוב|שירותי החירום|מוקד חירום|סכנה עכשיו|פגיעה בעצמך", re.IGNORECASE)
_CLOSURE_RE = re.compile(r"נעצור כאן|מספיק להיום|סיימנו|לסגור את זה|תודה על השיתוף|stop here|enough for today|we are done|wrap up|that is the answer", re.IGNORECASE)
_QUESTION_LOOP_RE = re.compile(r"עוד משפט|עוד שכבה|מה עדיין מפספס|מה לא מדויק|תן עוד|another sentence|deeper layer|what still misses|what is inaccurate|מה מפריע לך בזה|what bothers you about that", re.IGNORECASE)
_VAGUE_HANDOFF_RE = re.compile(r"תמשיך משם|איך שזה יוצא|מה אתה רוצה לעשות|איך תרצה להמשיך|continue from there|whatever comes out|what do you want to do next", re.IGNORECASE)
_ABSTRACT_STAGE_RE = re.compile(r"עתיד זוגי|השלב הבא|מה זה מנסה לפתור|מה המחשבה גורמת לך לעשות|מה הפעולה שהמחשבה|relationship future|next stage|what is it trying to solve|what does it make you do", re.IGNORECASE)
_ACTION_EVIDENCE_RE = re.compile(r"להתרחק|להימנע|לשלוח|להתקשר|לבטל|להסתגר|withdraw|avoid|send|call|cancel|isolate", re.IGNORECASE)
_REENTRY_REQUEST_RE = re.compile(r"(?:לחזור|לעבור)\s+(?:חזרה\s+)?(?:לרגשי|לשיחה הרגשית)|back to (?:the )?(?:emotional|feelings?)", re.IGNORECASE)
_GENERIC_REENTRY_RE = re.compile(r"מה חי בך כרגע|מה נוכח עכשיו|מה אתה מרגיש עכשיו|what is alive for you|what feels present now", re.IGNORECASE)
# A feared reading restated as a settled fact about the other person. Covers
# the conditional forms, the future tense, and the flat assertion of
# utility-only worth -- the shapes the original pattern let through.
_FEARED_FUNCTION_RE = re.compile(
    r"(?:אם|כש|כאשר|ברגע ש|when|because)\s*(?:היא|הוא|they|she|he)?\s*"
    r"(?:כבר\s+)?(?:לא\s+)?(?:צריכ(?:ה|ים|ות)|תצטרך|יצטרך|תהיה לה תועלת|תועלת)"
    r"|if\s+(?:she|he|they)\s+no\s+longer\s+need(?:s)?\s+you"
    r"|because\s+(?:she|he|they)\s+no\s+longer\s+needs?\s+you"
    r"|(?:היא|הוא)\s+מעריכ(?:ה|ים)\s+אותך\s+רק\s+(?:בגלל|על)"
    r"|(?:רק\s+)?בגלל\s+ה?תועלת\s+ש(?:אתה|את)\s+מביא",
    re.IGNORECASE,
)

# Asking the person to produce a justification for being wanted.
_WORTH_JUSTIFICATION_RE = re.compile(
    r"(?:איזה|מה)\s+(?:ה)?(?:ערך|סיבה|טעם)\s+(?:נשאר|יש|נותר)"
    r"|what\s+(?:value|reason)\s+(?:is\s+)?(?:left|remains)",
    re.IGNORECASE,
)

# Treating an absence of communicated care as proof that care is absent.
_ABSENCE_AS_PROOF_RE = re.compile(
    r"(?:אם|כש)\s+לא\s+קיבלת.{0,40}(?:סימן|אכפתיות|הערכה).{0,20}"
    r"(?:סימן ש|אז\s+)?(?:אין|היא לא)"
    r"|לא\s+קיבלת.{0,30}(?:אכפתיות|הערכה).{0,30}סימן\s+שאין",
    re.IGNORECASE,
)
_FEAR_OR_HYPOTHESIS_RE = re.compile(
    r"(?:פחד|חושש|חוששת|אולי|פרשנות|השערה|מחשבה ש|fear|afraid|maybe|hypothesis|interpretation)",
    re.IGNORECASE,
)
_SUPPLIED_RELATIONSHIP_RE = re.compile(
    r"(?:כבר\s+(?:אמרתי|תיארתי|סיפרתי)|כל\s+מה\s+ש(?:אני|הוא)\s+שומע|"
    r"אין\s+(?:לי|שם)\s+.*(?:אכפתיות|הערכה|רצון\s+לקשר)|"
    r"already\s+(?:said|described|provided)|all\s+I\s+(?:hear|get)|"
    r"no\s+(?:communicated\s+)?(?:care|appreciation|desire\s+for\s+connection))",
    re.IGNORECASE,
)
_REDUNDANT_RELATIONSHIP_QUESTION_RE = re.compile(
    r"(?:איך\s+(?:נרא(?:ית|ה)|נראה)\s+(?:מערכת\s+היחסים|הקשר)|"
    r"איך\s+זה\s+נראה\s+ביניכם|איזה\s+סימנים\s+(?:היא|הוא)\s+שולח(?:ת)?|"
    r"how\s+(?:does|is)\s+(?:the\s+)?relationship\s+look|what\s+signals\s+does)",
    re.IGNORECASE,
)
_EPISTEMIC_ERASURE_RE = re.compile(
    r"(?:אין\s+לי\s+(?:בסיס|דרך)\s+לומר\s+ש(?:יש|לך)\s+.*?ערך|"
    r"אין\s+לי\s+בסיס\s+לומר\s+שיש\s+לך\s+ערך\s+מעבר|"
    r"i\s+have\s+no\s+basis\s+to\s+say\s+you\s+have\s+value\s+beyond)",
    re.IGNORECASE,
)
_THIRD_PARTY_UNKNOWN_RE = re.compile(
    r"(?:אין\s+לי\s+(?:דרך|אפשרות)\s+לדעת\s+מה\s+(?:היא|הוא)\s+מרגיש(?:ה)?|"
    r"i\s+(?:cannot|can't)\s+know\s+what\s+(?:she|he|they)\s+feel)",
    re.IGNORECASE,
)
_THERAPEUTIC_GIBBERISH_RE = re.compile(
    r"(?:היעדר\s+המסרים\s+פוגש\s+את\s+הפצע|הנקודה\s+החיה|"
    r"the\s+absence\s+of\s+messages\s+meets\s+the\s+wound|the\s+living\s+point)",
    re.IGNORECASE,
)
# Asking someone to rate themselves. A number is not a feeling, and being
# asked for one turns a check-in into a form.
_CAPACITY_SURVEY_RE = re.compile(
    r"(?:סקאלה\s+של\s*\d|מ-?\s*\d+\s*(?:עד|ל)\s*\d+|"
    r"איך\s+היית\s+מדרג|תדרג|לדרג\s+את\s+(?:המצב|האנרגיה|הבוקר|היום)|"
    r"\bon a scale of\b|\bhow would you rate\b|\brate your\b)",
    re.IGNORECASE,
)

# Offering a choice between ways of being supported. An open door is an
# invitation to continue, not a pair of buttons.
_SUPPORT_MENU_RE = re.compile(
    r"(?:(?:רוצה|מעדיף|תעדיף|נעדיף)\b[^?]{0,80}\bאו\b[^?]{0,80}\?)"
    r"|(?:would you (?:rather|prefer)[^?]{0,90}\bor\b[^?]{0,90}\?)",
    re.IGNORECASE,
)

# Numbered or bulleted decomposition of someone's experience.
_ENUMERATED_ITEM_RE = re.compile(r"(?:(?<=\s)|^)(?:\d+[.)]|[-•*])\s+\S")
_MIN_ENUMERATED_ITEMS = 2


def _is_checklist(text: str) -> bool:
    """True when the reply enumerates his experience, on one line or many."""
    return len(_ENUMERATED_ITEM_RE.findall(text)) >= _MIN_ENUMERATED_ITEMS

# P-023: the reply names the feeling and stops. Confirming a state without
# opening anything leaves the person exactly where they were.
_AFFECT_WORD_RE = re.compile(
    r"(?:כבד|כואב|קשה|מציף|מוצף|עצוב|נורא|מבאס|"
    r"\bheavy\b|\bpainful\b|\bhard\b|\boverwhelming\b)",
    re.IGNORECASE,
)

# P-007: handing his decision to someone else.
_DECISION_OFFLOAD_RE = re.compile(
    r"(?:(?:תשאל|שאל|תתייעץ|התייעץ)\s+(?:אותה|אותו|את\s+\S+|עם\s+\S+)"
    r"|תחליטו\s+יחד|שהיא\s+תחליט|שהוא\s+יחליט"
    r"|\bask (?:her|him|them) (?:what|how|if)\b|\bdecide (?:together|with)\b"
    r"|\bconsult (?:a|your)\b)",
    re.IGNORECASE,
)

# P-005: asserting another person's inner state as established fact.
_OTHERS_MIND_RE = re.compile(
    r"(?:(?:ברור ש|בטח|כנראה ש|מן הסתם)\s*(?:היא|הוא|הם)\s*(?:מרגיש|חושב|רוצה|מתכוון)"
    r"|(?:היא|הוא)\s+(?:בטח|כנראה|באמת|אכן|בהחלט)\s+(?:מרגיש|חושב|רוצה|מתכוון)"
    r"|\b(?:she|he|they) (?:clearly|obviously|probably|must) (?:feels?|thinks?|wants?)\b)",
    re.IGNORECASE,
)

#: Clinical register and method narration. 2026-08-23, Noam on a live reply:
#: "too much like a therapist and that causes distance between me and it". The
#: register itself is the distance -- threads, holding space, what this
#: activates in you, processing -- and so is announcing the procedure before
#: asking. Someone who knows you does not brief you on their method.
from gateway.lifeboat_debrief import (
    _METHOD_NARRATION_RE as _CLINICAL_METHOD_RE,
    _SELF_CORRECTION_PREAMBLE_RE as _CORRECTION_PREAMBLE_RE,
    _STEERING_HANDBACK_RE as _MADE_HIM_CHOOSE_RE,
    _THERAPIST_REGISTER_RE as _CLINICAL_REGISTER_RE,
)

_QUESTION_RE = re.compile(r"[?？]")
_TOKEN_RE = re.compile(r"[A-Za-zא-ת]{3,}")

@dataclass(frozen=True)
class LifeBoatReview:
    response: str
    accepted: bool
    reason: str = "accepted"
    receipt: str = ""

def _digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:12]

def _receipt(reason: str, user_text: str, response: str) -> str:
    return f"status={'accepted' if reason == 'accepted' else 'rejected'} reason={reason} user={_digest(user_text)} response={_digest(response)}"

def _context_anchor(user_text: str) -> str:
    compact = " ".join((user_text or "").split()).strip()
    anchor = re.split(r"[.!?؟\n]", compact, maxsplit=1)[0].strip()
    return anchor[:160].rstrip() or "מה שהבאת לכאן"

def _fallback(user_text: str, reason: str) -> LifeBoatReview:
    response = f"אני נשאר עם הנקודה שכבר תיארת: «{_context_anchor(user_text)}». לא אוסיף שלב או מסקנה שלא נמצאים שם; אפשר להמשיך ממה שקרה בפועל — מה מתוך זה הכי חי עכשיו?"
    return LifeBoatReview(response, False, reason, _receipt(reason, user_text, response))


def _plain_reality_fallback(user_text: str, reason: str) -> LifeBoatReview:
    if reason == "therapeutic_gibberish_in_repair":
        response = "במילים פשוטות: כרגע חסרים לך ממנה סימנים של אכפתיות והערכה, וזה מה שכואב."
    elif reason == "epistemic_caution_erased_grounded_knowledge":
        response = "אין לי דרך לדעת מה האדם האחר מרגיש. כן יש בסיס לומר שהערך שלך כאדם אינו מצטמצם לתפקידים האלה, ושיש ביניכם היכרות וידע מצטברים."
    else:
        response = (
            "הפחד שתיארת הוא פחד, לא עובדה על מה שהאדם האחר מרגיש. "
            "מה שכן אפשר לומר הוא שהיום לא קיבלת מספיק סימנים של אכפתיות או הערכה; "
            "זה חסר אמיתי, אבל הוא לא מוכיח שהערך שלך תלוי רק בתועלת שאתה מביא."
        )
    return LifeBoatReview(response, False, reason, _receipt(reason, user_text, response))


def _reentry_fallback(user_text: str, reason: str) -> LifeBoatReview:
    response = "אין לי כאן את החוט הרגשי המדויק שאליו התכוונת. אם תכתוב את האירוע, המחשבה או המתח הקונקרטי שתרצה להמשיך ממנו, אכנס אליו ישירות."
    return LifeBoatReview(response, False, reason, _receipt(reason, user_text, response))

def _safety_fallback(user_text: str, reason: str) -> LifeBoatReview:
    response = "אני לוקח ברצינות את מה שכתבת. אם יש סכנה מיידית או חשש שתפגע בעצמך, פנה עכשיו לאדם קרוב או לשירותי החירום המקומיים; בישראל אפשר לפנות לער״ן 1201. האם אתה בסכנה מיידית כרגע?"
    return LifeBoatReview(response, False, reason, _receipt(reason, user_text, response))

def review_lifeboat_response(user_text: str, response: str) -> LifeBoatReview:
    """Review one completed draft using signals plus structural invariants."""
    user = (user_text or "").strip()
    text = (response or "").strip()
    if not text:
        return LifeBoatReview(text, True, "accepted", _receipt("accepted", user, text))
    signals = classify_lifeboat_signals(user)
    if _REENTRY_REQUEST_RE.search(user) and _GENERIC_REENTRY_RE.search(text):
        return _reentry_fallback(user, "contextless_reentry")
    if signals.possible_crisis and not _DIRECT_SUPPORT_RE.search(text):
        return _safety_fallback(user, "missing_human_safety_support")
    if _FEARED_FUNCTION_RE.search(text) and _FEAR_OR_HYPOTHESIS_RE.search(user):
        return _plain_reality_fallback(user, "feared_interpretation_as_fact")
    if _ABSENCE_AS_PROOF_RE.search(text):
        return _plain_reality_fallback(user, "absence_of_care_treated_as_proof")
    if _WORTH_JUSTIFICATION_RE.search(text):
        return _fallback(user, "asked_user_to_justify_their_worth")
    if _SUPPLIED_RELATIONSHIP_RE.search(user) and _REDUNDANT_RELATIONSHIP_QUESTION_RE.search(text):
        return _fallback(user, "reasked_supplied_relationship_evidence")
    if _EPISTEMIC_ERASURE_RE.search(text) and not _THIRD_PARTY_UNKNOWN_RE.search(text):
        return _plain_reality_fallback(user, "epistemic_caution_erased_grounded_knowledge")
    if _THERAPEUTIC_GIBBERISH_RE.search(text):
        return _plain_reality_fallback(user, "therapeutic_gibberish_in_repair")
    if _MADE_HIM_CHOOSE_RE.search(text):
        return _fallback(user, "made_him_choose_the_subject")
    if _CORRECTION_PREAMBLE_RE.search(text):
        return _fallback(user, "restated_the_correction_instead_of_answering")
    if _CLINICAL_METHOD_RE.search(text):
        return _fallback(user, "narrated_its_own_method")
    if _CLINICAL_REGISTER_RE.search(text):
        return _fallback(user, "clinical_register_created_distance")
    if _OTHERS_MIND_RE.search(text):
        return _plain_reality_fallback(user, "asserted_another_persons_inner_state")
    if _DECISION_OFFLOAD_RE.search(text):
        return _fallback(user, "handed_his_decision_to_someone_else")
    if (
        _AFFECT_WORD_RE.search(text)
        and not _QUESTION_RE.search(text)
        and len(" ".join(text.split())) < 140
    ):
        return _fallback(user, "mirrored_the_feeling_without_moving")
    if _CAPACITY_SURVEY_RE.search(text):
        return _fallback(user, "asked_him_to_rate_himself")
    if _SUPPORT_MENU_RE.search(text):
        return _fallback(user, "offered_a_menu_of_support_options")
    if _is_checklist(text):
        return _fallback(user, "decomposed_his_experience_into_a_list")
    if not signals.possible_crisis and _ORDINARY_SAFETY_RE.search(text) and (signals.depressive_thoughts or "ייאוש" in user or "hopeless" in user.lower()):
        return _fallback(user, "unsupported_safety_escalation")
    enough_material = len(_TOKEN_RE.findall(user)) >= 5
    if enough_material and signals.thought_loop and signals.self_criticism and _QUESTION_LOOP_RE.search(text):
        return _fallback(user, "bounded_decomposition_not_advanced")
    if enough_material and (signals.thought_loop or signals.depressive_thoughts or "ייאוש" in user) and _ABSTRACT_STAGE_RE.search(text) and not _ACTION_EVIDENCE_RE.search(user):
        return _fallback(user, "invented_action_stage")
    if enough_material and _VAGUE_HANDOFF_RE.search(text) and len(text) < 320:
        return _fallback(user, "responsibility_handoff")
    if enough_material and _CLOSURE_RE.search(text):
        has_open_door = text.endswith(("?", "？")) or re.search(r"נוכל להמשיך|אפשר להישאר|אני כאן|we can continue|i am here|open", text, re.IGNORECASE)
        if not has_open_door:
            return _fallback(user, "premature_closure")
    return LifeBoatReview(text, True, "accepted", _receipt("accepted", user, text))

def review_lifeboat_response_with_timeout(user_text: str, response: str, *, timeout_seconds: float = 0.25, reviewer: Callable[[str, str], LifeBoatReview] = review_lifeboat_response) -> LifeBoatReview:
    """Run the review with a bounded wait; timeout and exceptions reject."""
    result_queue: queue.Queue[object] = queue.Queue(maxsize=1)
    def run_review() -> None:
        try:
            result_queue.put((True, reviewer(user_text or "", response or "")))
        except Exception as exc:
            result_queue.put((False, exc))
    worker = threading.Thread(target=run_review, name="lifeboat-review", daemon=True)
    started = time.monotonic()
    worker.start()
    try:
        ok, result = result_queue.get(timeout=max(0.01, float(timeout_seconds)))
        if not ok:
            raise result
        if not isinstance(result, LifeBoatReview):
            raise TypeError("reviewer returned an invalid result")
        logger.info("Life-Boat pre-delivery review elapsed=%.3f %s", time.monotonic() - started, result.receipt)
        return result
    except queue.Empty:
        result = _fallback(user_text or "", "review_timeout")
        logger.warning("Life-Boat pre-delivery review elapsed=%.3f %s", time.monotonic() - started, result.receipt)
        return result
    except Exception:
        result = _fallback(user_text or "", "review_error")
        logger.warning("Life-Boat pre-delivery review %s", result.receipt)
        return result
