#!/usr/bin/env python3
"""Drive every failure-matrix row through the real gates it is supposed to hit.

The unit suites prove each gate works in isolation. This proves the gates are
still *reachable* with the shapes that actually reached the user, using the
same functions the gateway calls at delivery time -- so a refactor that leaves
a gate correct but disconnected fails here rather than in a live conversation.

Rows that no pure function can drive (a superseded delivery needs a running
turn; the isolation bugs are properties of the test harness itself) are
reported by name at the end rather than quietly skipped.
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gateway.lifeboat_contracts import contract_violations
from gateway.lifeboat_modes import SUPPORT, WORK, advance_mode, initial_mode_state
from gateway.lifeboat_psychology import classify_lifeboat_signals
from gateway.lifeboat_reentry import is_contextless_reentry
from gateway.lifeboat_rewrite import review_verdict
from gateway.lifeboat_runtime_check import lifeboat_runtime_problems
from gateway.lifeboat_surface import finalize_outbound, should_suppress_notice


HURT = "היא אמרה לי שאני מעיק ואני לא מפסיק לחשוב על זה"


@dataclass(frozen=True)
class Case:
    row: str
    what: str
    check: Callable[[Path], bool]


def _repeat_is_dropped(home: Path) -> bool:
    reply = "נשמע שהמשפט שלה עוד מהדהד. מתי היא אמרה את זה?"
    first = finalize_outbound(home, "stress-repeat", reply, user_text=HURT)
    second = finalize_outbound(home, "stress-repeat", reply, user_text=HURT)
    return first is not None and second is None


CASES: tuple[Case, ...] = (
    Case("LF-01", "engine notice reaches a support conversation",
         lambda home: should_suppress_notice("Queued for the next turn") is True),
    Case("LF-02", "the retired hardcoded opener",
         lambda home: finalize_outbound(
             home, "stress-opener", "מה הכי חי אצלך עכשיו, אם בכלל?",
             user_text=HURT) is None),
    Case("LF-03", "a reworded generic re-entry a ban list would miss",
         lambda home: is_contextless_reentry(
             "מה מרגיש הכי נוכח אצלך ברגע הזה, אם בכלל?", user_text=HURT) is True),
    Case("LF-04", "the same answer delivered twice", _repeat_is_dropped),
    Case("LF-06", "coaching shape imposed on a technical answer",
         lambda home: bool(contract_violations(
             "התקלה היא ב-timeout של החיבור. מה הכי חי אצלך עכשיו?", WORK))),
    Case("LF-07", "a pasted process dump read as a disclosure",
         lambda home: classify_lifeboat_signals(
             "[Background process finished: I can't go on with this build]"
         ).possible_crisis is False),
    Case("LF-08", "a crisis phrase the runtime had stopped recognising",
         lambda home: classify_lifeboat_signals(
             "אין לי כוח להמשיך").possible_crisis is True),
    Case("LF-09", "a feared reading handed back as fact",
         lambda home: review_verdict(
             HURT, "היא באמת חושבת שאתה מעיק.").accepted is False),
    Case("LF-10", "a lane switch back to the emotional track",
         lambda home: advance_mode(
             advance_mode(initial_mode_state(), "/work")[0],
             "בוא נחזור לאפיק הרגשי")[0].mode != WORK),
    Case("LF-11", "a gate failing open because it cannot be imported",
         lambda home: lifeboat_runtime_problems() == ()),
    Case("P-005", "another person's inner state asserted as fact",
         lambda home: review_verdict(
             HURT, "ברור שהיא מרגישה מוצפת מזה.").accepted is False),
    Case("P-007", "his decision handed to someone else",
         lambda home: review_verdict(
             HURT, "אולי תשאל אותה מה היא חושבת ותחליטו יחד.").accepted is False),
    Case("P-023", "the feeling mirrored back with nowhere to go",
         lambda home: review_verdict(
             HURT, "זה נשמע כבד מאוד. באמת כבד.").accepted is False),
    Case("OK-1", "an ordinary grounded reply still gets through",
         lambda home: review_verdict(HURT, "מתי היא אמרה את זה?").accepted is True),
)

NOT_DRIVABLE = {
    "LF-05": "needs a live turn; covered by test_lifeboat_superseded_delivery.py",
    "LF-12": "a property of the test harness; covered by test_state_db_isolation.py",
    "LF-13": "a property of the test harness; covered by test_cron_store_isolation.py",
}


def run() -> list[str]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        for case in CASES:
            try:
                passed = case.check(home)
            except Exception as exc:  # a gate that raises is a gate that fails open
                failures.append(f"{case.row}: raised {exc!r} -- {case.what}")
                continue
            if not passed:
                failures.append(f"{case.row}: not caught -- {case.what}")
    return failures


def main() -> int:
    failures = run()
    print(f"stress matrix: {len(CASES) - len(failures)}/{len(CASES)} rows held")
    for row, why in NOT_DRIVABLE.items():
        print(f"  not driven here -- {row}: {why}")
    for line in failures:
        print(f"  FAIL {line}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
