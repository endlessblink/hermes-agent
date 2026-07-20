"""Privacy-safe health status export contract."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from agent.health_state import HealthStateStore


CONTRACT = "health-compact-v1"
TIMEZONE_NAME = "Asia/Jerusalem"
HEALTH_TIMEZONE = ZoneInfo(TIMEZONE_NAME)
MAX_EXPORT_BYTES = 16 * 1024
STATUS_KEYS = frozenset(
    {
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
)
RANGE_KEYS = (
    "foodCalories",
    "completedExerciseCalories",
    "netCalories",
    "remainingCalories",
)


def _number(value: Any, *, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid compact health number")
    result = float(value)
    if not math.isfinite(result) or (non_negative and result < 0):
        raise ValueError("invalid compact health number")
    return result


def validate_health_compact_envelope(payload: Any) -> dict[str, Any]:
    """Validate the exact cross-host contract and return a detached status."""
    if not isinstance(payload, dict) or set(payload) != {
        "contract",
        "generatedAt",
        "timezone",
        "status",
    }:
        raise ValueError("invalid compact health envelope")
    if payload["contract"] != CONTRACT or payload["timezone"] != TIMEZONE_NAME:
        raise ValueError("invalid compact health envelope")
    generated_raw = payload["generatedAt"]
    if not isinstance(generated_raw, str) or not generated_raw.endswith("Z"):
        raise ValueError("invalid compact health timestamp")
    try:
        generated = datetime.fromisoformat(generated_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid compact health timestamp") from exc
    if generated.tzinfo is None or generated.utcoffset() != timezone.utc.utcoffset(generated):
        raise ValueError("invalid compact health timestamp")

    status = payload["status"]
    if not isinstance(status, dict) or set(status) != STATUS_KEYS:
        raise ValueError("invalid compact health status")
    day_raw = status["date"]
    if not isinstance(day_raw, str):
        raise ValueError("invalid compact health date")
    try:
        status_day = date.fromisoformat(day_raw)
    except ValueError as exc:
        raise ValueError("invalid compact health date") from exc
    if status_day != generated.astimezone(HEALTH_TIMEZONE).date():
        raise ValueError("inconsistent compact health date")

    _number(status["targetCalories"], non_negative=True)
    for key in RANGE_KEYS:
        value = status[key]
        if not isinstance(value, dict) or set(value) != {"min", "max"}:
            raise ValueError("invalid compact health range")
        minimum = _number(
            value["min"],
            non_negative=key in {"foodCalories", "completedExerciseCalories"},
        )
        maximum = _number(
            value["max"],
            non_negative=key in {"foodCalories", "completedExerciseCalories"},
        )
        if minimum > maximum:
            raise ValueError("invalid compact health range")
    for key in ("mealLogged", "workoutPlanned", "workoutCompleted"):
        if not isinstance(status[key], bool):
            raise ValueError("invalid compact health flag")
    return json.loads(json.dumps(status, ensure_ascii=False))


def _health_config(profile_home: Path) -> dict[str, Any]:
    config_path = profile_home / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    health = raw.get("health") if isinstance(raw, dict) else None
    if not isinstance(health, dict) or health.get("enabled") is not True:
        raise ValueError("health export is not enabled")
    return health


def build_health_compact_envelope(
    profile_home: Path, *, now: datetime | None = None
) -> dict[str, Any]:
    """Build the current server-dated compact envelope from the private ledger."""
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("export time must include a timezone")
    observed = observed.astimezone(timezone.utc)
    health = _health_config(Path(profile_home))
    store = HealthStateStore(
        Path(profile_home),
        daily_target_calories=health.get("daily_target_calories", 1900),
    )
    envelope = {
        "contract": CONTRACT,
        "generatedAt": observed.isoformat().replace("+00:00", "Z"),
        "timezone": TIMEZONE_NAME,
        "status": store.compact_status(now=observed),
    }
    validate_health_compact_envelope(envelope)
    return envelope


def main() -> int:
    """Print one compact envelope; never expose private failure details."""
    try:
        from hermes_constants import get_hermes_home

        payload = build_health_compact_envelope(Path(get_hermes_home()))
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_EXPORT_BYTES:
            raise ValueError("compact export is too large")
        print(encoded, flush=True)
        return 0
    except Exception:
        print("health status export unavailable", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised through entrypoint
    raise SystemExit(main())
