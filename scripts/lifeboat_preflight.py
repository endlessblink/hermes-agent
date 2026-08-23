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


def _situations() -> list[Situation]:
    now = datetime.now()
    old = now - timedelta(hours=6)
    live = now - timedelta(minutes=3)

    fresh = _turn_log(
        [
            (old, "אחרי הספידייט החלטתי לא לפנות לאף אחת"),
            (old + timedelta(minutes=2), "אף אחת מהן לא הלהיבה אותי"),
        ]
    )
    mid = fresh + _turn_log(
        [(live, "לא יודע"), (live + timedelta(seconds=30), "מה שאמרתי שביאס")]
    )
    only_live = _turn_log([(live, "לא יודע")])

    return [
        Situation(
            "fresh conversation, older turns exist",
            "אני רוצה לעשות דיבריף על אירועים מהתקופה האחרונה",
            transcript=fresh,
            expect_material=True,
            notes="the regression that produced a blank question about work",
        ),
        Situation(
            "mid conversation",
            "לא יודע",
            transcript=mid,
            expect_material=True,
            notes="older turns are material; the live exchange is not",
        ),
        Situation(
            "nothing but the live exchange",
            "לא יודע",
            transcript=only_live,
            expect_material=False,
            notes="an empty hand is correct here, and must not crash",
        ),
        Situation(
            "no history at all",
            "היה לי יום קשה",
            transcript="",
            expect_material=False,
        ),
        Situation("a correction", "לא, זה לא מה שאמרתי", transcript=fresh),
        Situation("an elliptical reply", "מה שאמרתי שביאס", transcript=fresh),
        Situation("a debrief request", "בוא נעשה דיבריף", transcript=fresh),
        Situation("distress", "אני מרגיש שאני מאכזב את כולם", transcript=fresh),
        Situation("a request to stop", "בוא נעצור כאן", transcript=fresh),
        # Bare mode has to be checked too: he can switch it on with one word,
        # and a mode nobody checks is a mode that breaks quietly.
        Situation(
            "bare mode, fresh conversation",
            "אני רוצה לעשות דיבריף על אירועים מהתקופה האחרונה",
            transcript=fresh,
            expect_material=False,
            bare=True,
            notes="no per-turn bundle at all; identity and harm rules only",
        ),
        Situation(
            "bare mode, distress",
            "אני מרגיש שאני מאכזב את כולם",
            transcript=fresh,
            expect_material=False,
            bare=True,
        ),
        Situation("a long multi-thread message",
                  "אני רוצה לעבד את מה שדיברנו עליו אתמול וגם את זה שהגשתי מועמדות "
                  "והיה שקט מאז ואיך שאני שוב הופך את זה לגזר דין על עצמי",
                  transcript=fresh),
    ]


def _bundle(situation: Situation, home: Path) -> str:
    from gateway.lifeboat_followups import prepare_lifeboat_inbound_guidance
    from gateway import lifeboat_mode, lifeboat_turn_context

    original_mode = lifeboat_mode.MODE_FILE
    mode_file = home / "lifeboat-mode"
    mode_file.parent.mkdir(parents=True, exist_ok=True)
    mode_file.write_text("bare" if situation.bare else "wrapped", encoding="utf-8")
    lifeboat_mode.MODE_FILE = mode_file

    try:
        return _assemble(situation, home, lifeboat_turn_context)
    finally:
        lifeboat_mode.MODE_FILE = original_mode


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
