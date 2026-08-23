"""How much machinery sits between the model and him.

Noam, at the end of a long night: "you are a better bot than it is -- you talked
to me all night and I didn't feel like you degraded."

He is right, and it is not the model. It is the same class of model. The
difference is what wraps it. Nothing rewrites an ordinary assistant's words
after it writes them, nothing caps it at two sentences, nothing hands it a page
of rules about how to build a sentence, and it gets the whole conversation.
Life-Boat had all four working against it, and every attempt to fix its manner
by editing the rules moved the failure somewhere else instead of removing it.

So the amount of wrapper becomes a setting rather than an argument:

    ~/.hermes/lifeboat-mode      "bare" or "wrapped" (default)

``wrapped`` is everything as it is: per-turn guidance, length budget, reviewer,
editor. ``bare`` is what an ordinary assistant has -- who it is, and the
conversation. The harm rules stay in both; crisis handling is not part of the
wrapper and is never removed.

An absent or unreadable file means ``wrapped``. A setting that silently changes
how the bot talks to him at 2am would be its own failure.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MODE_FILE = Path.home() / ".hermes" / "lifeboat-mode"

WRAPPED = "wrapped"
BARE = "bare"


def current_mode() -> str:
    """Return the active mode, defaulting to the way it has always worked."""
    try:
        value = MODE_FILE.read_text(encoding="utf-8").strip().casefold()
    except OSError:
        return WRAPPED
    if value == BARE:
        return BARE
    if value and value != WRAPPED:
        logger.warning("Life-Boat mode %r is not a mode; staying wrapped", value)
    return WRAPPED


def is_bare() -> bool:
    """True when the model should be left to talk with the wrapper removed."""
    return current_mode() == BARE
