#!/usr/bin/env python3
"""Check what Life-Boat actually said, not what the code should make it say.

Run this before reporting that anything about the bot's behaviour is fixed.
Tests prove the code changed; only the transcript proves the message was right,
and every false "verified" in this project came from confusing the two.

Reads the profile's turn logs. Prints nothing and exits zero when the delivered
messages hold the invariants; prints the offending turns and exits one when they
do not. It never copies conversation text into its output — only the timestamp
and which invariant broke.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from gateway.lifeboat_behaviour import behaviour_problems, parse_turns  # noqa: E402


LOGS = pathlib.Path(
    "/media/endlessblink/data/app-data/sync/Dropbox/OBSIDIAN_SYNCED/MAIN VULT"
    "/_System/Hermes Turn Logs/life-boat"  # the bot's own transcript, not
    # the local life-advisor profile's -- reading that one is why every
    # "verified from the transcript" check was looking at the wrong file
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=2, help="how many recent log files to read")
    args = parser.parse_args()

    if not LOGS.is_dir():
        print(f"FAIL turn logs not found at {LOGS}")
        return 1

    files = sorted(LOGS.glob("2*.md"))[-max(1, args.days):]
    if not files:
        print("FAIL no turn logs to check")
        return 1

    problems: list[str] = []
    checked = 0
    for path in files:
        turns = parse_turns(path.read_text(encoding="utf-8", errors="replace"))
        checked += len(turns)
        problems.extend(problem for problem in behaviour_problems(turns))

    print(f"Life-Boat behaviour check — {checked} delivered turns across {len(files)} day(s)")
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        print("All delivered messages hold the invariants.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
