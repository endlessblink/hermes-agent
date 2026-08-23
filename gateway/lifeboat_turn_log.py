"""Where a turn's transcript belongs.

Life-Boat's Telegram turns were written into the shared default-profile log,
mixed with cron jobs and every other topic, and marked only by a session id.
Meanwhile everything that reads Life-Boat's history -- the check-in anchors,
the behaviour verification -- looked in a folder that only the local
`life-advisor` profile ever wrote to. Both sides were working correctly and
they were never pointed at the same place, so a check that claimed to confirm
behaviour from the real transcript was reading an unrelated file.

The support topic therefore gets a folder of its own, identified the way every
other Life-Boat decision identifies it: by the chat and topic it arrives on,
not by which process profile happens to be serving it.
"""

from __future__ import annotations

from typing import Any

from gateway.lifeboat_followups import is_lifeboat_source


#: The folder the support topic's turns are written to.
LIFEBOAT_LOG_NAME = "life-boat"


def log_folder_for(source: Any, *, profile: str = "default") -> str:
    """Return the turn-log folder for this turn.

    Everything that is not the support topic keeps writing where it always
    did, so nothing already reading those files changes underneath it.
    """
    fallback = str(profile or "default").strip() or "default"
    if source is None:
        return fallback
    try:
        return LIFEBOAT_LOG_NAME if is_lifeboat_source(source) else fallback
    except Exception:
        # A malformed source must never cost the turn its transcript.
        return fallback


#: The local `life-advisor` profile's own transcript. Not the bot's.
LOCAL_PROFILE_LOG_NAME = "life-advisor"


def readable_log_folders(*, for_bot: bool) -> tuple[str, ...]:
    """Which transcripts this reader is allowed to see.

    Noam, 2026-08-23: "life advisor is a hermes local profile, not the telegram
    bot one. we can make it use the bot's context but not the other way
    around." The support bot handles material the local profile has no claim
    on, so the flow runs one way and is refused rather than merely documented.
    """
    if for_bot:
        return (LIFEBOAT_LOG_NAME,)
    return (LIFEBOAT_LOG_NAME, LOCAL_PROFILE_LOG_NAME)


def log_folder_read_check(folder: str, *, for_bot: bool) -> bool:
    """Raise if this reader may not read ``folder``; return True when it may."""
    name = str(folder or "").strip()
    if name not in readable_log_folders(for_bot=for_bot):
        raise PermissionError(
            f"the Life-Boat bot may not read the '{name}' transcript; "
            "the local profile may read the bot's, never the reverse"
        )
    return True
