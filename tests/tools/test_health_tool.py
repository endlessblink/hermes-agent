from __future__ import annotations

import json
import subprocess


def _remote_config(tmp_path) -> None:
    identity = tmp_path / "health-status-key"
    identity.write_text("test key", encoding="utf-8")
    identity.chmod(0o600)
    (tmp_path / "config.yaml").write_text(
        "\n".join(
            (
                "health:",
                "  status_source:",
                "    transport: ssh",
                "    host: health-vps.example",
                "    user: health-reader",
                f"    identity_file: {identity}",
                "",
            )
        ),
        encoding="utf-8",
    )


def _compact_envelope(**status_changes) -> dict:
    status = {
        "date": "2026-07-19",
        "targetCalories": 1900.0,
        "foodCalories": {"min": 150.0, "max": 170.0},
        "completedExerciseCalories": {"min": 0.0, "max": 0.0},
        "netCalories": {"min": 150.0, "max": 170.0},
        "remainingCalories": {"min": 1730.0, "max": 1750.0},
        "mealLogged": True,
        "workoutPlanned": False,
        "workoutCompleted": False,
    }
    status.update(status_changes)
    return {
        "contract": "health-compact-v1",
        "generatedAt": "2026-07-19T09:00:00Z",
        "timezone": "Asia/Jerusalem",
        "status": status,
    }


def _result(raw: str) -> dict:
    payload = json.loads(raw)
    assert "error" not in payload
    return payload["result"]


def test_health_tool_requires_explicit_config_enablement(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("health:\n  enabled: false\n", encoding="utf-8")

    from tools.health_tool import _check_health_enabled

    assert _check_health_enabled() is False
    (tmp_path / "config.yaml").write_text("health:\n  enabled: true\n", encoding="utf-8")
    assert _check_health_enabled() is True


def test_health_tools_log_correct_query_and_return_only_sanitized_status(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("health:\n  enabled: true\n", encoding="utf-8")

    from tools.health_tool import (
        _handle_compact_status,
        _handle_correct_event,
        _handle_get_day,
        _handle_log_event,
    )

    logged = _result(
        _handle_log_event(
            {
                "requestId": "meal-1",
                "kind": "food",
                "label": "sensitive meal label",
                "caloriesMin": 200,
                "caloriesMax": 240,
                "proteinGrams": 18,
                "occurredAt": "2026-07-19T12:00:00+03:00",
            }
        )
    )
    event_id = logged["event"]["id"]

    _result(
        _handle_correct_event(
            {
                "requestId": "meal-1-correct",
                "eventId": event_id,
                "caloriesMin": 150,
                "caloriesMax": 170,
            }
        )
    )
    day = _result(_handle_get_day({"date": "2026-07-19"}))["day"]
    assert day["foodCalories"] == {"min": 150.0, "max": 170.0}

    compact = _result(_handle_compact_status({"date": "2026-07-19"}))["status"]
    encoded = json.dumps(compact, sort_keys=True)
    assert "sensitive meal label" not in encoded
    assert "protein" not in encoded.lower()
    assert "events" not in compact


def test_health_tool_rejects_invalid_ranges_at_the_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("health:\n  enabled: true\n", encoding="utf-8")

    from tools.health_tool import _handle_log_event

    payload = json.loads(
        _handle_log_event(
            {
                "requestId": "bad-range",
                "kind": "food",
                "label": "meal",
                "caloriesMin": 300,
                "caloriesMax": 200,
            }
        )
    )
    assert "error" in payload


def test_private_health_tools_are_separate_from_the_compact_status_toolset():
    from toolsets import resolve_toolset

    assert set(resolve_toolset("health")) == {
        "health_log_event",
        "health_correct_event",
        "health_get_day",
    }
    assert resolve_toolset("health_status") == ["health_compact_status"]


def test_remote_compact_status_requires_office_work_and_complete_source_config(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _remote_config(tmp_path)
    monkeypatch.setattr(
        "hermes_cli.profiles.get_active_profile_name", lambda: "life-advisor"
    )

    from tools.health_tool import _check_health_status_enabled

    assert _check_health_status_enabled() is False
    monkeypatch.setattr(
        "hermes_cli.profiles.get_active_profile_name", lambda: "office-work"
    )
    assert _check_health_status_enabled() is True
    (tmp_path / "config.yaml").write_text(
        "health:\n  status_source:\n    transport: ssh\n",
        encoding="utf-8",
    )
    assert _check_health_status_enabled() is False


def test_office_remote_status_uses_fixed_strict_ssh_argv_and_no_shell(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _remote_config(tmp_path)
    monkeypatch.setattr(
        "hermes_cli.profiles.get_active_profile_name", lambda: "office-work"
    )
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(_compact_envelope()).encode(),
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    from tools.health_tool import _handle_compact_status

    status = _result(_handle_compact_status({"date": "1999-01-01"}))["status"]

    assert status["date"] == "2026-07-19"
    assert seen["argv"] == [
        "ssh",
        "-T",
        "-F",
        "/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "ConnectTimeout=5",
        "-i",
        str(tmp_path / "health-status-key"),
        "health-reader@health-vps.example",
    ]
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["timeout"] == 8


def test_remote_status_rejects_private_or_oversized_output_with_a_generic_error(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _remote_config(tmp_path)
    monkeypatch.setattr(
        "hermes_cli.profiles.get_active_profile_name", lambda: "office-work"
    )
    payload = _compact_envelope()
    payload["status"]["events"] = [{"label": "sensitive medical meal"}]

    def private_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(payload).encode(), stderr=b"private server detail"
        )

    monkeypatch.setattr(subprocess, "run", private_run)
    from tools.health_tool import _handle_compact_status

    raw = _handle_compact_status({})
    assert "error" in json.loads(raw)
    assert "sensitive" not in raw
    assert "private server detail" not in raw

    def oversized_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=b"x" * 16385, stderr=b"")

    monkeypatch.setattr(subprocess, "run", oversized_run)
    assert "error" in json.loads(_handle_compact_status({}))


def test_remote_status_rejects_inconsistent_date_and_never_falls_back_locally(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _remote_config(tmp_path)
    monkeypatch.setattr(
        "hermes_cli.profiles.get_active_profile_name", lambda: "office-work"
    )
    from agent.health_state import HealthStateStore

    HealthStateStore(tmp_path).record_event(
        request_id="local-private",
        kind="food",
        label="must not become fallback",
        calories={"min": 999, "max": 999},
        occurred_at="2026-07-19T12:00:00+03:00",
    )
    invalid = _compact_envelope(date="2026-07-18")

    def inconsistent_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(invalid).encode(), stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", inconsistent_run)
    from tools.health_tool import _handle_compact_status

    raw = _handle_compact_status({})
    assert "error" in json.loads(raw)
    assert "999" not in raw

    def offline(*args, **kwargs):
        raise subprocess.TimeoutExpired("ssh", 8)

    monkeypatch.setattr(subprocess, "run", offline)
    raw = _handle_compact_status({})
    assert "error" in json.loads(raw)
    assert "must not become fallback" not in raw
