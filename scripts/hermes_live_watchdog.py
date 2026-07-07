#!/usr/bin/env python3
"""Out-of-band live watchdog for Hermes Desktop turns.

The gateway writes a lightweight turn ledger to
``~/.hermes/logs/turn-watchdog.jsonl``. This process tails that ledger outside
the agent turn and alerts when a session is running without visible progress.
It is intentionally independent from Desktop UI state: if the UI or turn thread
is wedged, this still leaves a forensic alert trail.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_IDLE_SECONDS = 45.0
DEFAULT_ALERT_COOLDOWN_SECONDS = 60.0


def hermes_home() -> Path:
    raw = os.environ.get("HERMES_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".hermes"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def load_json(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def process_snapshot() -> list[str]:
    try:
        result = subprocess.run(
            ["pgrep", "-af", "Hermes|hermes.*serve|slash_worker"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except Exception:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def desktop_notify(title: str, body: str) -> None:
    notify = shutil.which("notify-send")
    if not notify:
        return
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    try:
        subprocess.run(
            [notify, "--app-name=Hermes Watchdog", "--urgency=critical", title, body],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except Exception:
        return


@dataclass
class TurnState:
    session_id: str
    session_key: str = ""
    cwd: str = ""
    started_at: float = 0.0
    last_progress_at: float = 0.0
    last_event: str = ""
    compression: bool = False
    payload: dict[str, Any] = field(default_factory=dict)
    last_alert_at: float = 0.0

    def update(self, row: dict[str, Any]) -> None:
        now = float(row.get("monotonic") or time.monotonic())
        self.session_key = str(row.get("session_key") or self.session_key)
        self.cwd = str(row.get("cwd") or self.cwd)
        self.last_event = str(row.get("event") or self.last_event)
        self.last_progress_at = now
        self.payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if not self.started_at:
            self.started_at = float(row.get("turn_started_at") or now)
        self.compression = bool(row.get("compression_started_at")) or (
            self.payload.get("component") == "compression"
            and self.payload.get("event") in {"start", "heartbeat"}
        )


def is_terminal(row: dict[str, Any]) -> bool:
    event = str(row.get("event") or "")
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    if event == "message.complete":
        return True
    if event == "diagnostic.event" and payload.get("component") == "turn":
        return payload.get("event") in {"complete", "error", "finally", "idle_timeout"}
    if event == "diagnostic.event" and payload.get("component") == "compression":
        return payload.get("event") == "timeout"
    return False


def is_progress(row: dict[str, Any]) -> bool:
    event = str(row.get("event") or "")
    if event in {"session.info"}:
        return False
    if event == "diagnostic.event":
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        return payload.get("component") in {"turn", "compression"}
    return True


class LedgerTail:
    def __init__(self, path: Path, from_end: bool) -> None:
        self.path = path
        self.offset = 0
        if from_end and path.exists():
            self.offset = path.stat().st_size

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.offset:
            self.offset = 0
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self.offset)
                lines = handle.readlines()
                self.offset = handle.tell()
        except OSError:
            return []
        return [row for line in lines if (row := load_json(line))]


def build_alert(state: TurnState, idle: float, elapsed: float, idle_seconds: float) -> dict[str, Any]:
    return {
        "ts": utc_now(),
        "severity": "error",
        "component": "live_watchdog",
        "event": "turn_stuck",
        "message": f"Hermes turn has been silent for {idle:.1f}s",
        "session_id": state.session_id,
        "session_key": state.session_key,
        "cwd": state.cwd,
        "last_event": state.last_event,
        "idle_seconds": round(idle, 3),
        "elapsed_seconds": round(elapsed, 3),
        "threshold_seconds": idle_seconds,
        "compression": state.compression,
        "payload": state.payload,
        "processes": process_snapshot(),
    }


def run(args: argparse.Namespace) -> int:
    home = Path(args.home).expanduser() if args.home else hermes_home()
    ledger = Path(args.ledger).expanduser() if args.ledger else home / "logs" / "turn-watchdog.jsonl"
    alerts = Path(args.alerts).expanduser() if args.alerts else home / "logs" / "live-watchdog-alerts.jsonl"
    latest = alerts.with_suffix(".latest.json")
    states: dict[str, TurnState] = {}
    tail = LedgerTail(ledger, from_end=args.from_end)
    stopped = False

    def stop(_signum, _frame) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(
        f"[hermes-live-watchdog] watching {ledger} "
        f"(idle>{args.idle_seconds:.1f}s, cooldown>{args.alert_cooldown:.1f}s)",
        flush=True,
    )

    while not stopped:
        now = time.monotonic()
        for row in tail.rows():
            sid = str(row.get("session_id") or "")
            if not sid:
                continue
            if is_terminal(row):
                states.pop(sid, None)
                continue
            if not is_progress(row):
                continue
            state = states.get(sid)
            if state is None:
                state = TurnState(session_id=sid)
                states[sid] = state
            state.update(row)

        for sid, state in list(states.items()):
            last = state.last_progress_at or state.started_at
            idle = now - last
            elapsed = now - (state.started_at or last)
            if idle < args.idle_seconds:
                continue
            if now - state.last_alert_at < args.alert_cooldown:
                continue
            state.last_alert_at = now
            alert = build_alert(state, idle, elapsed, args.idle_seconds)
            append_jsonl(alerts, alert)
            latest.write_text(
                json.dumps(alert, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            line = (
                f"[hermes-live-watchdog] STUCK sid={sid[:8]} "
                f"key={state.session_key} idle={idle:.1f}s last={state.last_event}"
            )
            print(line, flush=True)
            if args.notify:
                desktop_notify("Hermes turn may be stuck", line)

        if args.once:
            return 0
        time.sleep(args.interval)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default="", help="Hermes home, default ~/.hermes or HERMES_HOME")
    parser.add_argument("--ledger", default="", help="Turn watchdog ledger path")
    parser.add_argument("--alerts", default="", help="Alert JSONL path")
    parser.add_argument("--idle-seconds", type=float, default=env_float("HERMES_LIVE_WATCHDOG_IDLE_SECONDS", DEFAULT_IDLE_SECONDS))
    parser.add_argument("--alert-cooldown", type=float, default=env_float("HERMES_LIVE_WATCHDOG_ALERT_COOLDOWN_SECONDS", DEFAULT_ALERT_COOLDOWN_SECONDS))
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--from-start", dest="from_end", action="store_false", help="Read existing ledger rows first")
    parser.add_argument("--from-end", dest="from_end", action="store_true", help="Only watch new ledger rows")
    parser.set_defaults(from_end=True)
    parser.add_argument("--notify", action="store_true", help="Send desktop notifications with notify-send")
    parser.add_argument("--once", action="store_true", help="Process available rows once and exit")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args(sys.argv[1:])))
