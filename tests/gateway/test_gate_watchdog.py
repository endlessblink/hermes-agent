"""The gate watchdog must be silent when healthy and loud when not.

A watchdog that reports "all good" on a schedule becomes noise, gets muted, and
then says nothing when it matters. This one prints only on failure, which is
the only way it stays worth reading.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys


WATCHDOG = pathlib.Path.home() / ".hermes/scripts/lifeboat_gate_watchdog.py"
RUNTIME = "/home/endlessblink/.hermes/hermes-agent/venv/bin/python"


def _run(script: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [RUNTIME, str(script)], capture_output=True, text=True, timeout=120
    )


def test_the_watchdog_exists() -> None:
    assert WATCHDOG.is_file()


def test_a_healthy_runtime_produces_no_output() -> None:
    result = _run(WATCHDOG)

    assert result.stdout.strip() == "", f"watchdog spoke while healthy: {result.stdout}"
    assert result.returncode == 0


def test_a_broken_gate_produces_a_warning(tmp_path) -> None:
    """Simulated by pointing the check at a module that cannot satisfy it."""
    broken = tmp_path / "broken_watchdog.py"
    broken.write_text(
        WATCHDOG.read_text(encoding="utf-8").replace(
            "from gateway.lifeboat_runtime_check import lifeboat_runtime_problems",
            "from gateway.does_not_exist import lifeboat_runtime_problems",
        ),
        encoding="utf-8",
    )

    result = _run(broken)

    assert "Life-Boat gates cannot be checked" in result.stdout
    assert result.returncode == 0


def test_it_never_exits_non_zero(tmp_path) -> None:
    """A non-zero exit would mark the scheduled job failed and hide the message."""
    broken = tmp_path / "raising_watchdog.py"
    broken.write_text(
        WATCHDOG.read_text(encoding="utf-8").replace(
            "        problems = lifeboat_runtime_problems()",
            "        raise RuntimeError('boom')",
        ),
        encoding="utf-8",
    )

    result = _run(broken)

    assert result.returncode == 0
    assert "raised" in result.stdout


def test_the_warning_says_what_it_means_for_the_user() -> None:
    text = WATCHDOG.read_text(encoding="utf-8")

    assert "ungated" in text
