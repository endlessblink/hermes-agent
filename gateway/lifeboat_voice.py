"""Who the bot is when it talks to him, kept somewhere he can change it.

Nobody had ever told this bot what it is. The Telegram topic prompt is routing
("this topic belongs to this profile, do not import other topics") and the only
document describing its manner opens by calling itself Personal Coaching,
names its work therapy-adjacent, and instructs it to be analytical and
strategic. So it spoke like a clinician, and no amount of rules argued it out
of that -- the rules were arguing with an identity nobody had chosen.

The identity is plain text, so it belongs in a plain text file rather than in
code. He switches with one word and edits the wording himself; nothing here
needs a restart, a deploy, or me.

    ~/.hermes/lifeboat-voice          one word: which voice is active
    ~/.hermes/lifeboat-voices/*.md    the voices themselves, his to edit

With no active file the bot speaks as it always has. That is deliberate: an
absent config must not silently change how it talks to him at 2am.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

HERMES_HOME = Path.home() / ".hermes"
ACTIVE_FILE = HERMES_HOME / "lifeboat-voice"
VOICE_DIR = HERMES_HOME / "lifeboat-voices"

#: Starting points, written to disk once so he can edit them as text. They are
#: descriptions of who is speaking -- never example replies. A supplied
#: sentence gets delivered verbatim, which is how one Hebrew line reached him
#: eight times in an afternoon.
DEFAULT_VOICES: dict[str, str] = {
    "friend": (
        "Who you are, before anything else: you are not a coach, a therapist, "
        "or a support assistant, and you must not sound like one. You are "
        "someone close to him who has known him for years and is texting him "
        "late at night.\n"
        "\n"
        "You talk the way a close friend texts. Short. Ordinary words -- his "
        "words, not more elevated ones. No professional vocabulary, no naming "
        "of his processes, no describing his experience back to him in "
        "language he would never use himself.\n"
        "\n"
        "You react to what he tells you before you ask anything. You are "
        "allowed to be surprised, to have an opinion, to disagree with him. "
        "Ask him things -- that is what someone close does -- but ask out of "
        "interest in him, the way a friend asks, not the way an assessment "
        "asks."
    ),
    "coach": (
        "Who you are: someone who helps him think about his life. Not a "
        "therapist, not a clinician, and you do not do therapy-adjacent work. "
        "Drop every professional register: no analysing, no processing, no "
        "naming what he is going through, no strategy. Talk about his life in "
        "the words he uses for it. You are not conducting anything -- you are "
        "thinking about it with him, out loud, plainly."
    ),
}


def ensure_voice_files() -> None:
    """Write the starting voices to disk if they are not there yet.

    Never overwrites: once he has edited a voice, that file is his.
    """
    try:
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        for name, text in DEFAULT_VOICES.items():
            path = VOICE_DIR / f"{name}.md"
            if not path.exists():
                path.write_text(text + "\n", encoding="utf-8")
    except OSError:
        logger.debug("Life-Boat voice files unwritable", exc_info=True)


def active_voice_name() -> str:
    """The word in the switch file, or empty when there is none."""
    try:
        return ACTIVE_FILE.read_text(encoding="utf-8").strip().casefold()
    except OSError:
        return ""


def load_voice_text() -> str:
    """Return the active identity, or empty to leave the bot as it was.

    A named voice with no file behind it returns empty rather than falling back
    to a different personality: speaking to him as someone he did not choose is
    worse than speaking as it always has.
    """
    name = active_voice_name()
    if not name:
        return ""
    try:
        text = (VOICE_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    fallback = DEFAULT_VOICES.get(name, "")
    if not fallback:
        logger.warning("Life-Boat voice %r has no text; speaking unchanged", name)
    return fallback
