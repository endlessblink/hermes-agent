"""What a reply is allowed to look like, per conversation mode.

The contract only describes and inspects; it never rewrites. A violation is
reported so the draft can be sent back to the model, because generating
replacement prose here is exactly what produced the same Hebrew sentence turn
after turn.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from gateway.lifeboat_modes import CRISIS, PAUSED, SUPPORT, TIME, WORK


_QUESTION_RE = re.compile(r"[?？]")
_STRUCTURE_RE = re.compile(r"(?:^|\n)\s*(?:[-•*]|\d+[.)])\s+")
#: A status block labels its lines instead of bullets: "Done: ...", "חסום: ...".
_LABELLED_LINE_RE = re.compile(r"^[^\n:]{1,24}:\s+\S", re.M)
_MIN_LABELLED_LINES = 2
_CLOSURE_RE = re.compile(
    r"(?:לסיכום|מכאן ש|זה אומר ש|אין פלא|הדבר החשוב הוא|"
    r"in conclusion|this means|the important thing is|therefore)",
    re.IGNORECASE,
)
#: Coaching moves that are right in a support conversation and wrong in a
#: working one, where the user asked a concrete question.
_COACHING_TAIL_RE = re.compile(
    r"(?:רוצה שנחשוב על צעד אחד קטן|מה הכי חי אצלך|מה הכי נוכח אצלך|"
    r"איך זה פוגש אותך|להישאר רגע עם מה שזה מעורר|"
    r"what(?:'s| is) most alive for you|how does that land)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReplyContract:
    """The shape a reply must take in one mode."""

    mode: str
    max_chars: int
    wants_open_question: bool
    allows_structure: bool


_CONTRACTS = {
    SUPPORT: ReplyContract(SUPPORT, 720, True, False),
    CRISIS: ReplyContract(CRISIS, 900, True, False),
    TIME: ReplyContract(TIME, 1200, False, True),
    WORK: ReplyContract(WORK, 4000, False, True),
    PAUSED: ReplyContract(PAUSED, 420, False, False),
}


def contract_for(mode: str) -> ReplyContract:
    """Return the contract for ``mode``, defaulting to the support contract."""
    return _CONTRACTS.get(str(mode or "").strip().casefold(), _CONTRACTS[SUPPORT])


def contract_violations(response: str | None, mode: str = SUPPORT) -> tuple[str, ...]:
    """Name every way this draft breaks its mode's contract."""
    text = str(response or "").strip()
    if not text:
        return ()

    contract = contract_for(mode)
    issues: list[str] = []

    if len(text) > contract.max_chars:
        issues.append("too_long")

    if not contract.allows_structure and (
        _STRUCTURE_RE.search(f"\n{text}")
        or len(_LABELLED_LINE_RE.findall(text)) >= _MIN_LABELLED_LINES
    ):
        issues.append("structure")

    if contract.wants_open_question:
        if not _QUESTION_RE.search(text):
            issues.append("closed")
        elif _CLOSURE_RE.search(text):
            issues.append("premature_conclusion")
    elif _CLOSURE_RE.search(text) and contract.mode == SUPPORT:
        issues.append("premature_conclusion")

    # A coaching close belongs to support, not to an answer about a bug.
    if not contract.wants_open_question and _COACHING_TAIL_RE.search(text):
        issues.append("coaching_tail")

    return tuple(issues)
