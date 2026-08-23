"""The failure matrix runs as a test, not only as a script.

Every row in docs/lifeboat-failure-matrix.md names something that reached a
real conversation. This drives each one through the gate that is supposed to
stop it, using the functions the gateway calls at delivery time — so a gate
that stays correct but stops being reachable fails here.
"""

from __future__ import annotations

from scripts.lifeboat_stress_matrix import CASES, NOT_DRIVABLE, run


def test_every_failure_matrix_row_is_still_caught() -> None:
    assert run() == []


def test_the_matrix_covers_every_documented_row() -> None:
    """A new row in the doc must land in this harness or in NOT_DRIVABLE."""
    import pathlib
    import re

    doc = pathlib.Path("docs/lifeboat-failure-matrix.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| (LF-\d+) \|", doc, re.M))
    driven = {case.row for case in CASES} | set(NOT_DRIVABLE)

    assert documented - driven == set()
