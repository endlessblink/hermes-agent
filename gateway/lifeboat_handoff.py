"""Passing hands-on work from Life-Boat to Orchestrator.

Life-Boat is mainly a support conversation. When it turns into real work, the
work belongs to the Orchestrator profile, which has the tools and its own topic
to answer in.

The handoff is in-process, not a Telegram message. Telegram bots cannot read
each other's messages, and this install routes every topic through one bot, so
a message-based handoff would be dropped without a trace.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from gateway.lifeboat_modes import WORK


#: Profile that owns the developer topic in the routing table.
ORCHESTRATOR_PROFILE = "orchestrator"

#: How the user says "not this one, answer here".
_KEEP_LOCAL_RE = re.compile(
    r"(?:אל תעביר|בלי להעביר|תענה לי כאן|תשאר איתי|כאן בבקשה|"
    r"don'?t hand (?:this )?off|answer here|stay here)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HandoffRequest:
    """One work request on its way to another profile."""

    target_profile: str
    prompt: str
    origin_session_key: str


def should_hand_off(mode: str, user_text: str | None) -> bool:
    """Return True when this turn belongs to Orchestrator rather than here."""
    text = str(user_text or "").strip()
    if not text:
        return False
    # Safety is never routed. Whatever else the message contains, someone in
    # crisis is answered here, by the conversation they are already in.
    if str(mode or "").strip().casefold() != WORK:
        return False
    if _KEEP_LOCAL_RE.search(text):
        return False
    return True


def build_handoff(user_text: str, *, session_key: str) -> HandoffRequest:
    """Package a work request for the Orchestrator profile.

    The user's own words are passed through unchanged; summarising them here
    would lose the detail the work depends on.
    """
    return HandoffRequest(
        target_profile=ORCHESTRATOR_PROFILE,
        prompt=str(user_text or "").strip(),
        origin_session_key=str(session_key),
    )


def pointer_line() -> str:
    """The one line Life-Boat leaves behind when it hands work over."""
    return "העברתי את זה ל-Orchestrator. התשובה תגיע שם."
