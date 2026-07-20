from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess


def test_exporter_emits_only_the_compact_current_jerusalem_status(tmp_path):
    from agent.health_state import HealthStateStore
    from agent.health_status_export import build_health_compact_envelope

    (tmp_path / "config.yaml").write_text(
        "health:\n  enabled: true\n  daily_target_calories: 1900\n",
        encoding="utf-8",
    )
    now = datetime(2026, 7, 19, 21, 30, tzinfo=timezone.utc)
    HealthStateStore(tmp_path).record_event(
        request_id="private-meal",
        kind="food",
        label="sensitive medical meal",
        calories={"min": 200, "max": 240},
        macros={"proteinGrams": 18, "privateNote": "never export"},
        now=now,
    )

    envelope = build_health_compact_envelope(tmp_path, now=now)

    assert set(envelope) == {"contract", "generatedAt", "timezone", "status"}
    assert envelope["contract"] == "health-compact-v1"
    assert envelope["generatedAt"] == "2026-07-19T21:30:00Z"
    assert envelope["timezone"] == "Asia/Jerusalem"
    assert envelope["status"]["date"] == "2026-07-20"
    assert set(envelope["status"]) == {
        "date",
        "targetCalories",
        "foodCalories",
        "completedExerciseCalories",
        "netCalories",
        "remainingCalories",
        "mealLogged",
        "workoutPlanned",
        "workoutCompleted",
    }
    encoded = json.dumps(envelope, sort_keys=True)
    assert "sensitive medical meal" not in encoded
    assert "privateNote" not in encoded
    assert "protein" not in encoded.lower()
    assert "events" not in encoded


def test_exporter_cli_fails_closed_without_printing_private_errors(monkeypatch, capsys):
    import agent.health_status_export as exporter

    def fail(*args, **kwargs):
        raise ValueError("sensitive ledger contents")

    monkeypatch.setattr(exporter, "build_health_compact_envelope", fail)

    assert exporter.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.strip() == "health status export unavailable"
    assert "sensitive" not in output.err


def test_zero_argument_exporter_entrypoint_runs_outside_the_repo_cwd(tmp_path):
    from agent.health_state import HealthStateStore

    (tmp_path / "config.yaml").write_text(
        "health:\n  enabled: true\n",
        encoding="utf-8",
    )
    HealthStateStore(tmp_path).record_event(
        request_id="meal-1",
        kind="food",
        label="private label",
        calories={"min": 100, "max": 100},
    )
    script = Path(__file__).resolve().parents[2] / "scripts" / "health-status-export.py"
    env = dict(os.environ)
    env["HERMES_HOME"] = str(tmp_path)

    completed = subprocess.run(
        [str(script)],
        cwd="/tmp",
        env=env,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["contract"] == "health-compact-v1"
    assert "private label" not in completed.stdout.decode()
    assert completed.stderr == b""
