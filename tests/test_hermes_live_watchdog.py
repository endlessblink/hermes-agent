import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hermes_live_watchdog.py"
SPEC = importlib.util.spec_from_file_location("hermes_live_watchdog", SCRIPT)
watchdog = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = watchdog
SPEC.loader.exec_module(watchdog)


NOW = 1_784_200_000.0


def _iso(timestamp=NOW):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _write_rows(home, rows):
    path = home / "logs" / "personal-assistant-monitor.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _row(event, *, source="producer", timestamp=NOW, **values):
    return {
        "ts": _iso(timestamp),
        "component": "personal_assistant_monitor",
        "profile": "office-work",
        "source": source,
        "event": event,
        "status": "available",
        "count": 0,
        **values,
    }


def _args(home, **values):
    defaults = {
        "profile_home": str(home),
        "producer_stale_seconds": 1200,
        "consumer_stale_seconds": 300,
        "alert_cooldown_seconds": 1200,
        "notify": False,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_delayed_and_future_heartbeats_cannot_regress_or_poison_liveness(tmp_path):
    ledger = _write_rows(
        tmp_path / "office-work",
        [
            _row("producer_check", timestamp=NOW - 10),
            _row("producer_check", timestamp=NOW - 100),
            _row("producer_check", timestamp=NOW + 61),
        ],
    )

    heartbeats, _ = watchdog.scan_monitor_ledger(
        ledger, profile="office-work", now=NOW
    )

    assert heartbeats["producer"]["timestamp"] == NOW - 10


def test_naive_and_malformed_timestamps_are_ignored(tmp_path):
    ledger = _write_rows(
        tmp_path / "office-work",
        [
            _row("producer_check", timestamp=NOW),
            {**_row("producer_check"), "ts": "2026-07-16T12:00:00"},
            {**_row("producer_check"), "ts": "invalid"},
        ],
    )

    heartbeats, _ = watchdog.scan_monitor_ledger(
        ledger, profile="office-work", now=NOW
    )

    assert heartbeats["producer"]["timestamp"] == NOW


def test_connector_failure_is_resolved_only_by_a_newer_producer_check(tmp_path):
    home = tmp_path / "office-work"
    ledger = _write_rows(
        home,
        [
            _row("producer_check", timestamp=NOW - 30),
            _row("connector_failure", timestamp=NOW - 20, status="connection_refused"),
            _row("producer_check", timestamp=NOW - 10),
        ],
    )

    _, incidents = watchdog.scan_monitor_ledger(
        ledger, profile="office-work", now=NOW
    )

    assert incidents == {}


def test_delayed_old_success_does_not_resolve_newer_connector_failure(tmp_path):
    home = tmp_path / "office-work"
    ledger = _write_rows(
        home,
        [
            _row("connector_failure", timestamp=NOW - 10, status="timeout"),
            _row("producer_check", timestamp=NOW - 20),
        ],
    )

    _, incidents = watchdog.scan_monitor_ledger(
        ledger, profile="office-work", now=NOW
    )

    assert incidents[("producer", "connector_failure")]["status"] == "timeout"


def test_dead_letter_survives_more_than_one_thousand_later_heartbeats(tmp_path):
    home = tmp_path / "office-work"
    rows = [_row("dead_letter", source="consumer", timestamp=NOW - 2000, count=1)]
    rows.extend(
        _row("consumer_heartbeat", source="consumer", timestamp=NOW - 1500 + index)
        for index in range(1200)
    )
    ledger = _write_rows(home, rows)

    _, incidents = watchdog.scan_monitor_ledger(
        ledger, profile="office-work", now=NOW
    )

    assert incidents[("consumer", "dead_letter")]["count"] == 1


def test_dead_letter_requires_explicit_newer_resolution_receipt(tmp_path):
    home = tmp_path / "office-work"
    ledger = _write_rows(
        home,
        [
            _row("dead_letter", source="consumer", timestamp=NOW - 20, count=1),
            _row("dead_letter_resolved", source="consumer", timestamp=NOW - 10),
        ],
    )

    _, incidents = watchdog.scan_monitor_ledger(
        ledger, profile="office-work", now=NOW
    )

    assert incidents == {}


def test_other_profile_rows_cannot_affect_this_watchdog(tmp_path):
    home = tmp_path / "office-work"
    ledger = _write_rows(
        home,
        [{**_row("dead_letter", source="consumer"), "profile": "other"}],
    )

    _, incidents = watchdog.scan_monitor_ledger(
        ledger, profile="office-work", now=NOW
    )

    assert incidents == {}


def test_invalid_incident_count_is_safely_redacted_to_zero(tmp_path):
    home = tmp_path / "office-work"
    ledger = _write_rows(
        home,
        [_row("dead_letter", source="consumer", count="private-not-a-number")],
    )

    _, incidents = watchdog.scan_monitor_ledger(
        ledger, profile="office-work", now=NOW
    )

    assert incidents[("consumer", "dead_letter")]["count"] == 0


def test_process_start_ticks_handles_spaces_in_comm(monkeypatch):
    raw = "41 (Hermes Gateway Worker) S " + " ".join(str(i) for i in range(4, 23))
    monkeypatch.setattr(watchdog.platform, "system", lambda: "Linux")
    monkeypatch.setattr(watchdog.Path, "read_text", lambda *args, **kwargs: raw)

    assert watchdog.process_start_ticks(41) == 22


def test_owner_checks_fail_closed_off_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(watchdog.platform, "system", lambda: "Darwin")

    assert watchdog.process_start_ticks(41) is None
    assert watchdog.consumer_owner_active({"owner_pid": 41, "owner_start_ticks": 22}) is False
    assert watchdog.producer_owner_active(tmp_path) is False


def test_consumer_owner_requires_matching_pid_identity_and_gateway_command(monkeypatch):
    monkeypatch.setattr(watchdog, "process_start_ticks", lambda _pid: 22)
    monkeypatch.setattr(watchdog, "_consumer_command_is_gateway", lambda _pid: True)

    assert watchdog.consumer_owner_active({"owner_pid": 41, "owner_start_ticks": 22})
    assert not watchdog.consumer_owner_active({"owner_pid": 41, "owner_start_ticks": 23})


def test_gateway_command_requires_an_exact_supported_module(monkeypatch):
    monkeypatch.setattr(watchdog.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        watchdog.Path,
        "read_bytes",
        lambda *_args, **_kwargs: b"python\0-m\0tui_gateway.entry_evil\0",
    )
    assert watchdog._consumer_command_is_gateway(41) is False
    monkeypatch.setattr(
        watchdog.Path,
        "read_bytes",
        lambda *_args, **_kwargs: b"python\0-m\0tui_gateway.entry\0",
    )
    assert watchdog._consumer_command_is_gateway(41) is True


def test_producer_owner_requires_active_exact_profile_unit(monkeypatch, tmp_path):
    home = tmp_path / "office-work"
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="active\n"),
            SimpleNamespace(
                returncode=0,
                stdout=f"python -m agent.personal_assistant_monitor --profile-home {home.resolve()}",
            ),
        ]
    )
    monkeypatch.setattr(watchdog, "_systemctl", lambda *_args: next(responses))

    assert watchdog.producer_owner_active(home) is True


def test_producer_owner_rejects_profile_path_prefix(monkeypatch, tmp_path):
    home = tmp_path / "office-work"
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="active\n"),
            SimpleNamespace(
                returncode=0,
                stdout=(
                    "python -m agent.personal_assistant_monitor --profile-home "
                    f"{home.resolve()}-other"
                ),
            ),
        ]
    )
    monkeypatch.setattr(watchdog, "_systemctl", lambda *_args: next(responses))

    assert watchdog.producer_owner_active(home) is False


def test_producer_owner_rejects_similarly_named_monitor_module(monkeypatch, tmp_path):
    home = tmp_path / "office-work"
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="active\n"),
            SimpleNamespace(
                returncode=0,
                stdout=(
                    "python -m agent.personal_assistant_monitor_old --profile-home "
                    f"{home.resolve()}"
                ),
            ),
        ]
    )
    monkeypatch.setattr(watchdog, "_systemctl", lambda *_args: next(responses))

    assert watchdog.producer_owner_active(home) is False


def test_producer_owner_rejects_ambiguous_profile_arguments(monkeypatch, tmp_path):
    home = tmp_path / "office-work"
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="active\n"),
            SimpleNamespace(
                returncode=0,
                stdout=(
                    "python -m agent.personal_assistant_monitor "
                    f"--profile-home {home.resolve()} --profile-home /tmp/other"
                ),
            ),
        ]
    )
    monkeypatch.setattr(watchdog, "_systemctl", lambda *_args: next(responses))

    assert watchdog.producer_owner_active(home) is False


def test_incident_alert_is_allowlisted_private_and_profile_scoped(monkeypatch, tmp_path):
    home = tmp_path / "office-work"
    _write_rows(
        home,
        [
            _row(
                "connector_failure",
                timestamp=NOW - 10,
                status="connection_refused",
                raw_payload="Bearer secret-value",
                command="python --token secret-value",
            )
        ],
    )
    monkeypatch.setattr(watchdog, "producer_owner_active", lambda _home: False)
    monkeypatch.setattr(watchdog, "consumer_owner_active", lambda _heartbeat: False)

    emitted = watchdog.check(_args(home), now=NOW)

    assert emitted == [
        {
            "ts": _iso(NOW),
            "component": "personal_assistant_monitor",
            "profile": "office-work",
            "source": "producer",
            "event": "connector_failure",
            "status": "connection_refused",
            "count": 0,
        }
    ]
    alert_path = home / "logs" / "personal-assistant-watchdog.jsonl"
    latest_path = home / "state" / "personal-assistant-watchdog" / "latest.json"
    state_path = home / "state" / "personal-assistant-watchdog" / "state.json"
    combined = alert_path.read_text() + latest_path.read_text() + state_path.read_text()
    assert "secret-value" not in combined
    assert stat.S_IMODE(alert_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(latest_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_alert_cooldown_isolated_by_profile_source_event_and_incident(monkeypatch, tmp_path):
    state = {"alerts": {}}
    keys = {
        watchdog._alert_key("one", "producer", "stale"),
        watchdog._alert_key("two", "producer", "stale"),
        watchdog._alert_key("one", "consumer", "stale"),
        watchdog._alert_key("one", "producer", "connector_failure", "10.000000"),
        watchdog._alert_key("one", "producer", "connector_failure", "20.000000"),
    }

    assert all(watchdog._should_alert(state, key, NOW, 1200) for key in keys)
    assert all(not watchdog._should_alert(state, key, NOW + 1, 1200) for key in keys)


def test_stale_alert_requires_a_live_exact_owner(monkeypatch, tmp_path):
    home = tmp_path / "office-work"
    _write_rows(home, [_row("producer_check", timestamp=NOW - 2000)])
    monkeypatch.setattr(watchdog, "producer_owner_active", lambda _home: False)
    monkeypatch.setattr(watchdog, "consumer_owner_active", lambda _heartbeat: False)
    assert watchdog.check(_args(home), now=NOW) == []

    monkeypatch.setattr(watchdog, "producer_owner_active", lambda _home: True)
    emitted = watchdog.check(_args(home), now=NOW + 1)
    assert emitted[0]["event"] == "stale"
    assert emitted[0]["source"] == "producer"


def test_latest_is_removed_after_explicit_resolution(monkeypatch, tmp_path):
    home = tmp_path / "office-work"
    ledger = _write_rows(
        home,
        [_row("dead_letter", source="consumer", timestamp=NOW - 20, count=1)],
    )
    monkeypatch.setattr(watchdog, "producer_owner_active", lambda _home: False)
    monkeypatch.setattr(watchdog, "consumer_owner_active", lambda _heartbeat: False)
    watchdog.check(_args(home), now=NOW)
    latest = home / "state" / "personal-assistant-watchdog" / "latest.json"
    assert latest.exists()

    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                _row("dead_letter_resolved", source="consumer", timestamp=NOW + 1)
            )
            + "\n"
        )
    watchdog.check(_args(home), now=NOW + 2)
    assert not latest.exists()


def test_latest_remains_while_stale_owner_is_still_active_during_cooldown(
    monkeypatch, tmp_path
):
    home = tmp_path / "office-work"
    _write_rows(home, [_row("producer_check", timestamp=NOW - 2000)])
    monkeypatch.setattr(watchdog, "producer_owner_active", lambda _home: True)
    monkeypatch.setattr(watchdog, "consumer_owner_active", lambda _heartbeat: False)

    watchdog.check(_args(home), now=NOW)
    latest = home / "state" / "personal-assistant-watchdog" / "latest.json"
    assert latest.exists()

    assert watchdog.check(_args(home), now=NOW + 1) == []
    assert latest.exists()


def test_latest_reconciles_when_previous_incident_resolves(monkeypatch, tmp_path):
    home = tmp_path / "office-work"
    ledger = _write_rows(
        home,
        [
            _row("connector_failure", timestamp=NOW - 30, status="timeout"),
            _row("dead_letter", source="consumer", timestamp=NOW - 20, count=1),
        ],
    )
    monkeypatch.setattr(watchdog, "producer_owner_active", lambda _home: False)
    monkeypatch.setattr(watchdog, "consumer_owner_active", lambda _heartbeat: False)
    watchdog.check(_args(home), now=NOW)

    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                _row("dead_letter_resolved", source="consumer", timestamp=NOW + 1)
            )
            + "\n"
        )
    assert watchdog.check(_args(home), now=NOW + 2) == []

    latest = json.loads(
        (home / "state" / "personal-assistant-watchdog" / "latest.json").read_text()
    )
    assert latest["event"] == "connector_failure"


def test_notify_uses_only_allowlisted_alert_text(monkeypatch):
    calls = []
    monkeypatch.setattr(watchdog.shutil, "which", lambda _name: "/usr/bin/notify-send")
    monkeypatch.setattr(
        watchdog.subprocess,
        "run",
        lambda args, **kwargs: calls.append((args, kwargs)),
    )

    watchdog._notify(
        {
            "source": "producer",
            "event": "connector_failure",
            "raw": "Bearer private-secret",
        }
    )

    rendered = json.dumps(calls)
    assert "private-secret" not in rendered
    assert "connector failure" in rendered
