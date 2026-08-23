"""Judge Life-Boat by what it actually said.

Four times in one session a fix was reported as verified because the tests
passed and the build was live, and each time the delivered message was still
wrong. Every miss lived in the gap between "the component is correct" and "the
message was good", and no unit test crosses that gap.

The transcripts were available the whole time. This reads them, so "verified"
can mean the delivered text was inspected rather than inferred.

It checks only what can be decided mechanically: a banned sentence, an engine
notice that escaped, a stock comfort line, a check-in that opened on nothing, a
reply repeated verbatim. Whether a reply was *good* is still a person's
judgement — but "this specific thing must never appear" is not, and those are
exactly the failures that kept being reported as fixed.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


_TURN_RE = re.compile(
    r"^##\s*(?P<stamp>\d{4}-\d\d-\d\dT[\d:]+)\s*—\s*session\s*`(?P<session>[^`]*)`"
    r"\s*—\s*platform\s*`(?P<platform>[^`]*)`\s*$"
    r"(?P<body>.*?)(?=^##\s*\d{4}-\d\d-\d\dT|\Z)",
    re.M | re.S,
)
_ASSISTANT_RE = re.compile(r"^###\s*Assistant\s*$\n(.*?)(?=^###\s|\Z)", re.M | re.S)

#: Sentences that must never be delivered again, each traced to an incident.
_BANNED_REENTRY = (
    "מה הכי חי אצלך עכשיו",
    "רוצה שנחשוב על צעד אחד קטן",
    "מה חי בך כרגע",
)
_STOCK_COMFORT = (
    "אני כאן, והדלת פתוחה",
    "אני כאן והדלת פתוחה",
    "הדלת פתוחה",
    "i am here for you",
)
_ENGINE_NOTICES = (
    "Interrupting current task",
    "Queued for the next turn",
    "Context compression finished",
    "Pre-API compression",
    "Approved for session",
    "מעבד את ההודעות",
    "לא נמצאו ולכן דולגו",
)

#: A check-in that is only a greeting and a bare "how are you" opened on
#: nothing, which is the failure mode the context wiring exists to remove.
_BARE_GREETING_RE = re.compile(r"(?:בוקר טוב|ערב טוב|היי|שלום)", re.IGNORECASE)
_BARE_QUESTION_RE = re.compile(r"מה שלומ|איך אתה מרגיש|מה נשמע", re.IGNORECASE)
_MAX_BARE_CHARS = 120


@dataclass(frozen=True)
class Turn:
    """One logged exchange."""

    stamp: str
    session: str
    platform: str
    assistant: str


def parse_turns(text: str | None) -> tuple[Turn, ...]:
    """Parse a Hermes turn log. Never raises on malformed text."""
    turns: list[Turn] = []
    for match in _TURN_RE.finditer(str(text or "")):
        body = match.group("body") or ""
        assistant = _ASSISTANT_RE.search(body)
        turns.append(
            Turn(
                stamp=match.group("stamp"),
                session=match.group("session"),
                platform=match.group("platform"),
                assistant=(assistant.group(1).strip() if assistant else "").strip("- \n"),
            )
        )
    return tuple(turns)


def _is_bare_checkin(turn: Turn) -> bool:
    text = " ".join(turn.assistant.split())
    if turn.platform != "cron" or not text:
        return False
    if len(text) > _MAX_BARE_CHARS:
        return False
    return bool(_BARE_GREETING_RE.search(text) and _BARE_QUESTION_RE.search(text))


def behaviour_problems(turns) -> tuple[str, ...]:
    """Return every delivered message that breaks an invariant we claim to hold."""
    problems: list[str] = []
    seen: dict[str, str] = {}

    for turn in turns or ():
        text = turn.assistant
        if not text:
            continue
        where = f"{turn.stamp}"

        for phrase in _BANNED_REENTRY:
            if phrase in text:
                problems.append(f"{where}: delivered a banned re-entry sentence ({phrase!r})")
                break
        for phrase in _STOCK_COMFORT:
            if phrase.casefold() in text.casefold():
                problems.append(f"{where}: delivered a stock comfort line ({phrase!r})")
                break
        for phrase in _ENGINE_NOTICES:
            if phrase.casefold() in text.casefold():
                problems.append(f"{where}: delivered an engine notice ({phrase!r})")
                break

        if _is_bare_checkin(turn):
            problems.append(
                f"{where}: the check-in was generic — a greeting and a bare question, "
                "opening on nothing he wrote"
            )

        key = " ".join(text.split()).casefold()
        if key in seen:
            problems.append(f"{where}: repeated a reply already sent at {seen[key]}")
        else:
            seen[key] = where

    return tuple(problems)
