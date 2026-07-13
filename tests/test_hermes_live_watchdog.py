import importlib.util
import json
import sys
import time
from pathlib import Path

from tui_gateway import server


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hermes_live_watchdog.py"
SPEC = importlib.util.spec_from_file_location("hermes_live_watchdog", SCRIPT)
watchdog = importlib.util.module_from_spec(SPEC)
assert SPEC is not None
assert SPEC.loader is not None
sys.modules[SPEC.name] = watchdog
SPEC.loader.exec_module(watchdog)


def test_discover_ledgers_includes_profile_ledgers(tmp_path):
    home = tmp_path / ".hermes"
    profile_ledger = home / "profiles" / "film-maker" / "logs" / "turn-watchdog.jsonl"
    profile_ledger.parent.mkdir(parents=True)
    profile_ledger.write_text("", encoding="utf-8")

    assert watchdog.discover_ledgers(home) == [
        home / "logs" / "turn-watchdog.jsonl",
        profile_ledger,
    ]


def test_profile_ledger_stuck_turn_writes_shared_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "profiles" / "film-maker" / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "monotonic": time.time() - 10,
                "event": "approval.request",
                "session_id": "sid-profile",
                "session_key": "key-profile",
                "cwd": "/tmp/project",
                "running": True,
                "turn_started_at": time.time() - 20,
                "turn_last_progress_at": time.time() - 10,
                "turn_last_progress_event": "approval.request",
                "payload": {},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    args = watchdog.parse_args(
        [
            "--home",
            str(home),
            "--idle-seconds",
            "1",
            "--alert-cooldown",
            "0",
            "--from-start",
            "--once",
        ]
    )

    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    rows = [json.loads(line) for line in alerts.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["event"] == "turn_stuck"
    assert rows[-1]["session_id"] == "sid-profile"
    assert rows[-1]["last_event"] == "approval.request"
    assert rows[-1]["ledger"] == str(ledger)


def test_personal_assistant_stuck_turn_is_classified(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "profiles" / "office-work" / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "monotonic": time.time() - 90,
                "event": "message.start",
                "session_id": "sid-assistant",
                "session_key": "assistant-key",
                "running": True,
                "personal_assistant": True,
                "turn_started_at": time.time() - 120,
                "payload": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    args = watchdog.parse_args(
        [
            "--home",
            str(home),
            "--idle-seconds",
            "45",
            "--alert-cooldown",
            "0",
            "--from-start",
            "--once",
        ]
    )

    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    rows = [json.loads(line) for line in alerts.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["event"] == "personal_assistant_turn_stuck"
    assert rows[-1]["personal_assistant"] is True


def test_session_not_found_is_alerted_immediately(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "logs" / "turn-watchdog.jsonl"
    monkeypatch.setattr(server, "_TURN_WATCHDOG_LOG", str(ledger))
    server._sessions.pop("sid-stale", None)

    response = server.handle_request(
        {
            "id": "rpc-1",
            "method": "prompt.submit",
            "params": {
                "session_id": "sid-stale",
                "text": "private prompt content",
            },
        }
    )
    assert response["error"]["message"] == "session not found"
    assert "private prompt content" not in ledger.read_text(encoding="utf-8")
    rpc_row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert watchdog.is_terminal(rpc_row) is True

    args = watchdog.parse_args(
        ["--home", str(home), "--from-start", "--once"]
    )

    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    rows = [json.loads(line) for line in alerts.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["event"] == "session_not_found"
    assert rows[-1]["session_id"] == "sid-stale"
    assert rows[-1]["payload"] == {
        "error": "session not found",
        "method": "prompt.submit",
    }


def test_session_not_found_diagnostic_and_error_are_deduplicated(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "process_snapshot", lambda: [])
    home = tmp_path / ".hermes"
    ledger = home / "logs" / "turn-watchdog.jsonl"
    ledger.parent.mkdir(parents=True)
    base = {
        "monotonic": time.time(),
        "session_id": "sid-stale",
        "session_key": "key-stale",
        "running": True,
    }
    rows = [
        {
            **base,
            "event": "diagnostic.event",
            "payload": {
                "component": "turn",
                "event": "error",
                "details": {"error": "session not found"},
            },
        },
        {
            **base,
            "event": "error",
            "payload": {"message": "session not found"},
        },
    ]
    ledger.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    args = watchdog.parse_args(["--home", str(home), "--from-start", "--once"])
    assert watchdog.run(args) == 0

    alerts = home / "logs" / "live-watchdog-alerts.jsonl"
    emitted = [json.loads(line) for line in alerts.read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in emitted].count("session_not_found") == 1
