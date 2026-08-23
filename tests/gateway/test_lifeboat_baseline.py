"""The rollback is tested, not asserted.

INQUIRY-26 asks for a frozen accepted baseline with a *tested* rollback
command. An untested one is the same promise the unreachable gates made. These
run freeze, damage, verify, and restore against a temporary copy of the tree —
never the live runtime.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.lifeboat_baseline import GATE_FILES, freeze, restore, verify


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    for rel in GATE_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# original {rel}\n", encoding="utf-8")
    return root


def test_a_clean_runtime_matches_its_own_baseline(runtime: Path, tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    freeze(runtime, baseline, "clean")

    assert verify(runtime, baseline) == []


def test_an_edited_gate_shows_up_as_drift(runtime: Path, tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    freeze(runtime, baseline, "clean")
    (runtime / GATE_FILES[0]).write_text("# broken\n", encoding="utf-8")

    assert verify(runtime, baseline) == [f"{GATE_FILES[0]}: differs from the baseline"]


def test_a_deleted_gate_shows_up_as_drift(runtime: Path, tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    freeze(runtime, baseline, "clean")
    (runtime / GATE_FILES[1]).unlink()

    assert verify(runtime, baseline) == [f"{GATE_FILES[1]}: missing from the runtime"]


def test_restore_puts_the_bytes_back_and_names_what_it_touched(
    runtime: Path, tmp_path: Path
) -> None:
    baseline = tmp_path / "baseline"
    freeze(runtime, baseline, "clean")
    (runtime / GATE_FILES[0]).write_text("# broken\n", encoding="utf-8")
    (runtime / GATE_FILES[1]).unlink()

    changed = restore(runtime, baseline)

    assert changed == sorted([GATE_FILES[0], GATE_FILES[1]])
    assert verify(runtime, baseline) == []
    assert (runtime / GATE_FILES[0]).read_text(encoding="utf-8") == f"# original {GATE_FILES[0]}\n"


def test_a_dry_run_changes_nothing(runtime: Path, tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    freeze(runtime, baseline, "clean")
    (runtime / GATE_FILES[0]).write_text("# broken\n", encoding="utf-8")

    assert restore(runtime, baseline, dry_run=True) == [GATE_FILES[0]]
    assert (runtime / GATE_FILES[0]).read_text(encoding="utf-8") == "# broken\n"


def test_restoring_a_matching_runtime_touches_nothing(runtime: Path, tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    freeze(runtime, baseline, "clean")

    assert restore(runtime, baseline) == []


def test_freezing_refuses_when_a_gate_is_missing(runtime: Path, tmp_path: Path) -> None:
    (runtime / GATE_FILES[2]).unlink()

    with pytest.raises(FileNotFoundError):
        freeze(runtime, tmp_path / "baseline", "incomplete")
