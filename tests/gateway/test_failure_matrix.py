"""The failure matrix must stay generated, not hand-maintained.

A matrix written by hand becomes a wish list: rows survive long after the
regression behind them is gone. This one is generated from the tests, and these
checks keep it that way.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/lifeboat-failure-matrix.md"
GENERATOR = ROOT / "scripts/build_failure_matrix.py"


def test_the_matrix_exists() -> None:
    assert MATRIX.is_file()


def test_the_generator_exists() -> None:
    assert GENERATOR.is_file()


def test_every_row_points_at_a_test_that_exists() -> None:
    text = MATRIX.read_text(encoding="utf-8")
    referenced = set(re.findall(r"`(test_\w+\.py)`", text))

    assert referenced, "the matrix references no tests at all"
    for name in sorted(referenced):
        assert (ROOT / "tests/gateway" / name).is_file(), f"{name} is referenced but missing"


def test_every_row_names_a_task() -> None:
    rows = [ln for ln in MATRIX.read_text(encoding="utf-8").splitlines() if ln.startswith("| LF-")]

    assert rows
    for row in rows:
        assert re.search(r"\b(?:BUG|TASK|FEATURE|INQUIRY)-\d+\b", row), row


def test_the_matrix_is_up_to_date() -> None:
    """Regenerating must not change the file: otherwise it has been hand-edited."""
    before = MATRIX.read_text(encoding="utf-8")

    subprocess.run([sys.executable, str(GENERATOR)], check=True, capture_output=True)

    assert MATRIX.read_text(encoding="utf-8") == before, (
        "the failure matrix is out of date; run scripts/build_failure_matrix.py"
    )


def test_the_generator_refuses_a_row_without_a_regression() -> None:
    source = GENERATOR.read_text(encoding="utf-8")

    assert "does not exist" in source
    assert "sys.exit(1)" in source
