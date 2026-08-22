"""The single chokepoint for everything the Life-Boat topic is allowed to show.

Life-Boat behaviour used to be spread across dozens of call sites, each free to
emit its own text.  That is why gateway plumbing notices ("Queued for the next
turn"), verbatim double-sends, and a hardcoded Hebrew opener all reached a
psychological-support conversation.

This module owns the outbound decision instead.  It has two jobs and refuses a
third: it drops engine notices that do not belong in a support conversation, it
drops exact repeats, and it never writes prose of its own.  Whatever the model
said is what the user reads, or nothing is sent at all.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from gateway.lifeboat_psychology import record_lifeboat_response_fingerprint


logger = logging.getLogger(__name__)

_GENERIC_REENTRY_RE = re.compile(
    r"מה\s*הכי\s*חי\s*אצלך\s*עכשיו\s*[,،]?\s*אם\s*בכלל"
    r"|רוצה\s*שנחשוב\s*על\s*צעד\s*אחד\s*קטן\s*[,،]?\s*או\s*שעדיף\s*להישאר\s*רגע\s*עם\s*מה\s*שזה\s*מעורר",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION_RE = re.compile(r"[.!?؟…\s]+$")


def _canonical_generic_text(value: str) -> str:
    """Compare only the banned wording, ignoring whitespace and punctuation."""
    return re.sub(r"[^\w\u0590-\u05ff]", "", str(value or "").casefold())


_BANNED_REENTRY_CANONICAL = frozenset(
    {
        _canonical_generic_text("מה הכי חי אצלך עכשיו, אם בכלל?"),
        _canonical_generic_text(
            "רוצה שנחשוב על צעד אחד קטן, או שעדיף להישאר רגע עם מה שזה מעורר?"
        ),
    }
)


def _strip_banned_generic_tail(text: str) -> tuple[str, str | None]:
    """Remove a banned generic ending without inventing replacement prose."""
    cleaned = text
    found = False
    while True:
        matches = list(_GENERIC_REENTRY_RE.finditer(cleaned))
        if not matches:
            break
        match = matches[-1]
        suffix = cleaned[match.end():]
        if suffix.strip(" \t\r\n.!?؟…"):
            break
        cleaned = cleaned[: match.start()].rstrip()
        cleaned = _TRAILING_PUNCTUATION_RE.sub("", cleaned).rstrip()
        found = True
    if not found:
        return text, None
    return cleaned, "generic_reentry_tail"


def is_banned_generic_response(response: str | None) -> bool:
    """Return True for a whole banned prompt or a response ending with one."""
    text = str(response or "").strip()
    if _canonical_generic_text(text) in _BANNED_REENTRY_CANONICAL:
        return True
    cleaned, reason = _strip_banned_generic_tail(text)
    return reason is not None and cleaned != text


#: Modes that are hands-on enough for engine plumbing to be useful rather than
#: intrusive.  Everything else -- support, time management, crisis -- is a
#: conversation, and a queue notice in the middle of one is noise at best.
NOTICE_MODES = frozenset({"work"})

#: Substrings identifying gateway status chatter rather than a real reply.
#: Matched as substrings because the gateway appends live detail to several of
#: them (queue depth, elapsed time) before delivery.
NOTICE_MARKERS: tuple[str, ...] = (
    "Queued for the next turn",
    "Interrupting current task",
    "Steered into current run",
    "Context compression finished",
    "Compressing context",
    "Subagent working",
    "Pre-API compression",
    "Compacting before the next model call",
    "Approved for session",
    "מעבד את ההודעות",
)


def is_engine_notice(text: str | None) -> bool:
    """Return True when ``text`` is gateway plumbing rather than a reply."""
    value = str(text or "")
    return any(marker in value for marker in NOTICE_MARKERS)


def should_suppress_notice(text: str | None, *, mode: str = "support") -> bool:
    """Return True when this notice must not reach the Life-Boat topic."""
    if str(mode or "").strip().casefold() in NOTICE_MODES:
        return False
    return is_engine_notice(text)


def finalize_outbound(
    profile_home: Path,
    session_key: str,
    response: str | None,
    *,
    mode: str = "support",
    user_text: str = "",
) -> str | None:
    """Return the text to deliver, or ``None`` to send nothing at all.

    Suppression is deliberately the only power this function has.  Rewriting a
    reply -- padding it, trimming it, appending a stock question -- is what
    produced the repetition this replaces, so a reply either goes out as the
    model wrote it or it does not go out.
    """
    text = str(response or "").strip()
    if not text:
        return None
    if should_suppress_notice(text, mode=mode):
        return None
    try:
        from gateway.lifeboat_reentry import is_contextless_reentry

        if is_contextless_reentry(text, user_text=user_text):
            logger.info(
                "Life-Boat output suppressed receipt reason=contextless_reentry "
                "message_content=redacted chars=%d",
                len(text),
            )
            return None
    except Exception:
        logger.error("Life-Boat re-entry check failed", exc_info=True)

    canonical = _canonical_generic_text(text)
    if canonical in _BANNED_REENTRY_CANONICAL:
        logger.info(
            "Life-Boat output suppressed receipt reason=generic_reentry message_content=redacted chars=%d",
            len(text),
        )
        return None
    cleaned, reason = _strip_banned_generic_tail(text)
    if reason is not None:
        if not cleaned:
            logger.info(
                "Life-Boat output suppressed receipt reason=%s message_content=redacted chars=%d",
                reason,
                len(text),
            )
            return None
        logger.info(
            "Life-Boat output trimmed receipt reason=%s message_content=redacted chars=%d",
            reason,
            len(text),
        )
        text = cleaned
    if record_lifeboat_response_fingerprint(profile_home, session_key, text):
        logger.info(
            "Life-Boat output suppressed receipt reason=recent_duplicate message_content=redacted chars=%d",
            len(text),
        )
        return None
    return text
