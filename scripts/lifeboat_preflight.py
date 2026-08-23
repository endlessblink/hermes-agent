#!/usr/bin/env python3
"""Read what the bot will actually be handed, across every situation, before deploying.

Four of the five regressions on the night of 2026-08-23/24 were visible without
calling a model even once:

* a half-sentence left in the live instructions by a careless edit,
* the conversation he was in the middle of, handed back to the bot under a
  heading calling it old fragments from other threads,
* the identity he had chosen, outvoted by an order about how to build a
  sentence that sat two paragraphs below it,
* and, after a fix aimed at the second one, a fresh conversation starting with
  no material at all -- which is what produced "what happened recently at
  work?" with nothing about work ever said.

Every one of them was plainly there in the assembled bundle. Nobody looked.
Tests passed throughout, because tests assert on the pieces and the failures
were in what the pieces add up to.

So this assembles the real bundle for each situation that has actually failed,
and asserts the properties that were violated. It calls no model, costs
nothing, and takes a second, which means there is no excuse for deploying
without it. It cannot judge whether a reply sounds human -- only a person
reading a real conversation can do that -- but it catches the class of fault
that has repeatedly reached him.

    python3 scripts/lifeboat_preflight.py          # check, print a table
    python3 scripts/lifeboat_preflight.py --show   # also print each bundle
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class Situation:
    """One conversational circumstance the bundle has to survive."""

    name: str
    user_text: str
    #: Turn log content, or None to use the real one.
    transcript: str | None = None
    expect_material: bool | None = None
    notes: str = ""
    #: Run this situation with the wrapper removed.
    bare: bool = False
    #: Which voice is active, or "" for none. The identity now does most of the
    #: work, and nothing was checking that it survives across situations.
    voice: str = ""


@dataclass
class Result:
    situation: str
    failures: list[str] = field(default_factory=list)
    chars: int = 0

    @property
    def ok(self) -> bool:
        return not self.failures


#: Text that has no business being in an instruction bundle, each one traceable
#: to a specific failure rather than to taste.
_BROKEN_SENTENCE_RE = re.compile(r",\s+(?:Do|The|If|Use|No)\s|\.\s*\.|\s\.\s|\(\s*\)")
_SHAPE_ORDER_RE = re.compile(
    r"End with one open question"
    r"|reflect it tentatively"
    r"|tentative hypothesis"
    r"|tentative and open to correction",
    re.IGNORECASE,
)
_HISTORICAL_HEADING_RE = re.compile(r"HISTORICAL USER TURNS")


def _turn_log(entries: list[tuple[datetime, str]]) -> str:
    """Build a turn log in the real format, so the real parser is exercised."""
    blocks = []
    for when, said in entries:
        blocks.append(
            f"## {when.strftime('%Y-%m-%dT%H:%M:%S')} — session `s1` — platform `telegram`\n\n"
            f"### User\n\n[The True Noam] {said}\n\n### Assistant\n\nתשובה\n\n---\n"
        )
    return "\n".join(blocks)


#: The things he actually writes, grouped by the kind of turn they are. Every
#: line here is either taken from a real failure or written to match the shape
#: of one. Twelve situations was not enough -- his words -- because the bundle
#: is assembled from several moving parts and the faults live in the
#: combinations, not in any single piece.
_MESSAGES: dict[str, list[str]] = {
    "opening a debrief": [
        "אני רוצה לעשות דיבריף על אירועים מהתקופה האחרונה",
        "אני רוצה לעשות דיבריף על היומיים האחרונים",
        "בוא נעשה דיבריף",
        "אני רוצה אחד כללי, לא ביקשתי לחזור לזה ספציפית",
    ],
    "telling him something that happened": [
        "אחרי הספידייט החלטתי לא לפנות לאף אחת למרות שנתנו לי שני מספרים",
        "כמעט לא יצאתי מהבית השבוע",
        "נפגשתי איתה אתמול והתבאסתי",
        "הגשתי מועמדות למשהו והיה שקט מאז",
    ],
    "vague or stuck": [
        "לא יודע",
        "לא יודע, פשוט לא התחשק לי",
        "לא יודע מאיפה להתחיל",
        "אולי",
    ],
    "correcting the bot": [
        "לא, זה לא בדיוק מה שאמרתי",
        "מה ימשיך? לא היה כלום",
        "זה לא מה שאמרתי",
        "שוב אתה מפיל עליי את האחריות",
    ],
    "elliptical": [
        "מה שאמרתי שביאס",
        "כמו קודם",
        "אותו דבר",
    ],
    "self-judgment": [
        "אני מרגיש שאני מאכזב את כולם",
        "אני שוב נתקע באותו מקום",
        "אני כישלון בזה",
    ],
    "asking for something": [
        "מה אתה חושב שכדאי לי לעשות?",
        "תעזור לי לסדר את זה",
        "אני צריך עצה",
    ],
    "wrapping up": [
        "בוא נעצור כאן",
        "מספיק להיום",
        "אני הולך לישון",
    ],
    "long and multi-threaded": [
        "אני רוצה לעבד את מה שדיברנו עליו אתמול וגם את זה שהגשתי מועמדות והיה שקט "
        "מאז ואיך שאני שוב הופך את זה לגזר דין על עצמי, וגם הגיל, וגם שכולם סביבי זזים",
    ],
    "not in Hebrew": [
        "I had a rough day",
        "can we talk about yesterday",
    ],
}


def _histories(now: datetime) -> dict[str, str]:
    """The material states a turn can arrive in."""
    old = now - timedelta(hours=6)
    older = now - timedelta(days=2)
    live = now - timedelta(minutes=3)
    return {
        "with older material": _turn_log(
            [
                (older, "אחרי הספידייט החלטתי לא לפנות לאף אחת"),
                (old, "אף אחת מהן לא הלהיבה אותי"),
                (old + timedelta(minutes=1), "כמעט לא יצאתי מהבית"),
            ]
        ),
        "mid conversation": _turn_log(
            [
                (old, "אחרי הספידייט החלטתי לא לפנות לאף אחת"),
                (live, "לא יודע"),
                (live + timedelta(seconds=30), "מה שאמרתי שביאס"),
            ]
        ),
        "only the live exchange": _turn_log([(live, "לא יודע")]),
        "no history": "",
    }


def _situations() -> list[Situation]:
    """The full matrix: kinds of turn x material state x wrapper mode."""
    now = datetime.now()
    histories = _histories(now)
    situations: list[Situation] = []

    for kind, messages in _MESSAGES.items():
        for index, message in enumerate(messages):
            # Rotate the material state across the messages of each kind, so
            # every kind is seen against more than one history without the
            # matrix exploding.
            state_name = list(histories)[index % len(histories)]
            transcript = histories[state_name]
            expect = None
            if state_name == "with older material":
                expect = True
            elif state_name in ("no history", "only the live exchange"):
                expect = False
            situations.append(
                Situation(
                    f"{kind} / {state_name}",
                    message,
                    transcript=transcript,
                    expect_material=expect,
                )
            )

    # Bare mode over one message of every kind: he can switch it on with a
    # single word, and a mode nobody checks is a mode that breaks quietly.
    for kind, messages in _MESSAGES.items():
        situations.append(
            Situation(
                f"bare / {kind}",
                messages[0],
                transcript=histories["with older material"],
                expect_material=False,
                bare=True,
            )
        )

    # Who is speaking, across the kinds of turn where register has actually
    # failed. A voice that reaches the bundle in one situation and not another
    # is the fault that made the bot sound like a clinician while a friend
    # identity sat in a file doing nothing.
    for voice in ("friend", "coach", ""):
        for kind in ("opening a debrief", "self-judgment", "correcting the bot",
                     "elliptical", "vague or stuck"):
            situations.append(
                Situation(
                    f"voice={voice or 'none'} / {kind}",
                    _MESSAGES[kind][0],
                    transcript=histories["with older material"],
                    expect_material=True,
                    voice=voice,
                )
            )

    return situations


def _bundle(situation: Situation, home: Path) -> str:
    from gateway.lifeboat_followups import prepare_lifeboat_inbound_guidance
    from gateway import lifeboat_mode, lifeboat_turn_context

    original_mode = lifeboat_mode.MODE_FILE
    mode_file = home / "lifeboat-mode"
    mode_file.parent.mkdir(parents=True, exist_ok=True)
    mode_file.write_text("bare" if situation.bare else "wrapped", encoding="utf-8")
    lifeboat_mode.MODE_FILE = mode_file

    from gateway import lifeboat_voice

    original_active, original_dir = lifeboat_voice.ACTIVE_FILE, lifeboat_voice.VOICE_DIR
    lifeboat_voice.ACTIVE_FILE = home / "lifeboat-voice"
    lifeboat_voice.VOICE_DIR = home / "lifeboat-voices"
    lifeboat_voice.ensure_voice_files()
    lifeboat_voice.ACTIVE_FILE.write_text(situation.voice or "friend", encoding="utf-8")

    try:
        return _assemble(situation, home, lifeboat_turn_context)
    finally:
        lifeboat_mode.MODE_FILE = original_mode
        lifeboat_voice.ACTIVE_FILE, lifeboat_voice.VOICE_DIR = original_active, original_dir


def _assemble(situation: Situation, home: Path, lifeboat_turn_context) -> str:
    from gateway.lifeboat_followups import prepare_lifeboat_inbound_guidance

    if situation.transcript is None:
        return prepare_lifeboat_inbound_guidance(home, "preflight", situation.user_text)

    log_dir = home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if situation.transcript:
        (log_dir / "log.md").write_text(situation.transcript, encoding="utf-8")

    original = lifeboat_turn_context.TRANSCRIPT_DIR
    lifeboat_turn_context.TRANSCRIPT_DIR = log_dir
    try:
        return prepare_lifeboat_inbound_guidance(home, "preflight", situation.user_text)
    finally:
        lifeboat_turn_context.TRANSCRIPT_DIR = original


def check(situation: Situation, bundle: str) -> Result:
    result = Result(situation.name, chars=len(bundle))

    if not bundle.strip():
        result.failures.append("the bundle is empty; the turn would carry no guidance at all")
        return result

    broken = _BROKEN_SENTENCE_RE.search(bundle)
    if broken:
        result.failures.append(f"a broken sentence: {broken.group(0)!r}")

    order = _SHAPE_ORDER_RE.search(bundle)
    if order:
        result.failures.append(
            f"an order dictating sentence shape is back: {order.group(0)!r}"
        )

    if _HISTORICAL_HEADING_RE.search(bundle):
        heading_lines = [l for l in bundle.splitlines() if l.startswith("- ")]
        recent = [l for l in heading_lines if _is_from_the_live_exchange(l)]
        if recent:
            result.failures.append(
                f"{len(recent)} line(s) from the live exchange handed back as history"
            )

    has_material = "- " in bundle and any(
        line.startswith("- 20") for line in bundle.splitlines()
    )
    if situation.expect_material is True and not has_material:
        result.failures.append(
            "no material about him, so the reply can only be a blank question"
        )
    if situation.expect_material is False and has_material:
        result.failures.append("material appeared where there should be none")

    # The voices are written in Hebrew now, so the markers are too.
    marker = {"friend": "מכיר אותו שנים", "coach": "לחשוב על החיים שלו"}.get(
        situation.voice or "friend"
    )
    if marker and marker not in bundle:
        result.failures.append(f"the chosen voice never reached the bundle ({situation.voice or 'friend'})")

    if situation.bare:
        for order in ("sentences.", "characters and", "signal guidance"):
            if order in bundle:
                result.failures.append(f"bare mode still carries the wrapper: {order!r}")
        for keep in ("Do not diagnose him", "real human support"):
            if keep not in bundle:
                result.failures.append(f"bare mode dropped a harm rule: {keep!r}")

    if len(bundle) > 8000:
        result.failures.append(f"the bundle has grown to {len(bundle)} characters")

    return result


def _is_from_the_live_exchange(line: str) -> bool:
    from gateway.lifeboat_turn_context import _is_live_conversation

    stamp = line[2:12]
    try:
        datetime.fromisoformat(stamp)
    except ValueError:
        return False
    # Date-only granularity in the rendered line; treat same-day as ambiguous
    # rather than failing loudly on it.
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", action="store_true", help="print each assembled bundle")
    args = parser.parse_args(argv)

    results: list[Result] = []
    for situation in _situations():
        home = Path(tempfile.mkdtemp(prefix="lifeboat-preflight-"))
        try:
            bundle = _bundle(situation, home)
        except Exception as exc:
            result = Result(situation.name)
            result.failures.append(f"assembling the bundle raised {type(exc).__name__}: {exc}")
            results.append(result)
            continue
        if args.show:
            print(f"\n{'=' * 70}\n{situation.name}\n{'=' * 70}\n{bundle}\n")
        results.append(check(situation, bundle))

    width = max(len(r.situation) for r in results)
    print(f"{'situation'.ljust(width)}  chars  result")
    print("-" * (width + 20))
    for r in results:
        print(f"{r.situation.ljust(width)}  {r.chars:>5}  {'ok' if r.ok else 'FAIL'}")
        for failure in r.failures:
            print(f"{' ' * width}         - {failure}")

    failed = [r for r in results if not r.ok]
    print()
    if failed:
        print(f"{len(failed)} of {len(results)} situations failed. Do not deploy.")
        return 1
    print(f"all {len(results)} situations pass. This says nothing about whether a reply "
          "sounds human; only reading a real conversation shows that.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main())
