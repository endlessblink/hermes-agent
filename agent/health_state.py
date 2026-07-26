"""Durable, append-only health state for the personal assistant."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4
from zoneinfo import ZoneInfo

from utils import atomic_json_write

try:  # pragma: no cover - platform specific
    import fcntl
except ImportError:  # pragma: no cover - Windows only
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - platform specific
    import msvcrt
except ImportError:  # pragma: no cover - Unix only
    msvcrt = None  # type: ignore[assignment]


HEALTH_TIMEZONE = ZoneInfo("Asia/Jerusalem")
DEFAULT_DAILY_TARGET = 1900.0
SCHEMA_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return result


def _calorie_range(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError("calories must contain min and max")
    minimum = _number(value.get("min"), "calories.min")
    maximum = _number(value.get("max"), "calories.max")
    if minimum > maximum:
        raise ValueError("calories.min cannot exceed calories.max")
    return {"min": minimum, "max": maximum}


def _text(value: Any, field: str, *, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    result = value.strip()
    if len(result) > limit:
        raise ValueError(f"{field} is too long")
    return result


def _macros(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError("macros must be a small object")
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = _text(raw_key, "macro name", limit=80)
        if raw_value is None or isinstance(raw_value, bool):
            result[key] = raw_value
        elif isinstance(raw_value, (int, float)):
            number = float(raw_value)
            if not math.isfinite(number) or number < 0:
                raise ValueError(f"macro {key} must be finite and non-negative")
            result[key] = number
        elif isinstance(raw_value, str) and len(raw_value) <= 500:
            result[key] = raw_value
        else:
            raise ValueError(f"macro {key} has an unsupported value")
    return result


def _aware_datetime(value: str | datetime | None, now: datetime | None) -> datetime:
    if value is None:
        parsed = now or _utc_now()
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("occurred_at must be an ISO-8601 datetime") from exc
    else:
        raise ValueError("occurred_at must be an ISO-8601 datetime")
    if parsed.tzinfo is None:
        raise ValueError("occurred_at must include a timezone")
    return parsed


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _local_date(value: datetime) -> str:
    return value.astimezone(HEALTH_TIMEZONE).date().isoformat()


def _day(value: str | date | None, now: datetime | None) -> str:
    if value is None:
        current = _aware_datetime(None, now)
        return _local_date(current)
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise ValueError("date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("date must use YYYY-MM-DD") from exc


def _status(kind: str, value: str | None) -> str:
    allowed = {"food": {"consumed", "cancelled"}, "exercise": {"planned", "completed", "cancelled"}}
    if kind not in allowed:
        raise ValueError("kind must be food or exercise")
    result = value or ("consumed" if kind == "food" else "planned")
    if result not in allowed[kind]:
        raise ValueError(f"invalid {kind} status")
    return result


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _clean(value: float) -> float:
    result = round(value, 4)
    return 0.0 if result == 0 else result


class HealthStateStore:
    """Profile-scoped health event ledger with derived daily views."""

    def __init__(self, profile_home: Path, *, daily_target_calories: float = DEFAULT_DAILY_TARGET) -> None:
        self.path = Path(profile_home) / "state" / "health" / "ledger.json"
        self.lock_path = self.path.with_suffix(".lock")
        self.daily_target_calories = _number(daily_target_calories, "daily_target_calories")

    def _default_state(self) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "revision": 0,
            "dailyTargetCalories": self.daily_target_calories,
            "events": [],
            "idempotencyKeys": [],
            "updatedAt": None,
        }

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._default_state()
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError("health ledger cannot be read safely") from exc
        if not isinstance(raw, dict) or raw.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError("health ledger has an unsupported format")
        events = raw.get("events")
        keys = raw.get("idempotencyKeys")
        if not isinstance(events, list) or not all(isinstance(item, dict) for item in events):
            raise ValueError("health ledger events have an unsupported format")
        if not isinstance(keys, list) or not all(isinstance(item, dict) for item in keys):
            raise ValueError("health ledger request keys have an unsupported format")
        raw["dailyTargetCalories"] = _number(
            raw.get("dailyTargetCalories", self.daily_target_calories), "dailyTargetCalories"
        )
        return raw

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            if fcntl is not None:  # pragma: no branch - one branch per platform
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            elif msvcrt is not None:  # pragma: no cover - Windows only
                handle.seek(0)
                if handle.read(1) == b"":
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                elif msvcrt is not None:  # pragma: no cover - Windows only
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    def _write(self, state: dict[str, Any], now: datetime) -> None:
        state["revision"] = int(state.get("revision", 0)) + 1
        state["updatedAt"] = _iso_utc(now)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.path, state)

    @staticmethod
    def _replay(state: dict[str, Any], request_id: str, digest: str) -> dict[str, Any] | None:
        for entry in state["idempotencyKeys"]:
            if entry.get("requestId") != request_id:
                continue
            if entry.get("digest") != digest:
                raise ValueError("request_id was already used for a different operation")
            event_id = entry.get("eventId")
            event = next((item for item in state["events"] if item.get("id") == event_id), None)
            if event is None:
                raise ValueError("idempotent result is unavailable")
            return {"replayed": True, "event": copy.deepcopy(event)}
        return None

    @staticmethod
    def _remember(state: dict[str, Any], request_id: str, digest: str, event_id: str) -> None:
        state["idempotencyKeys"].append(
            {"requestId": request_id, "digest": digest, "eventId": event_id}
        )

    def record_event(
        self,
        *,
        request_id: str,
        kind: str,
        label: str,
        calories: dict[str, Any],
        macros: dict[str, Any] | None = None,
        status: str | None = None,
        occurred_at: str | datetime | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        request = _text(request_id, "request_id", limit=200)
        normalized_kind = _text(kind, "kind", limit=20).lower()
        normalized_label = _text(label, "label")
        normalized_calories = _calorie_range(calories)
        normalized_macros = _macros(macros)
        normalized_status = _status(normalized_kind, status)
        explicit_occurrence = _aware_datetime(occurred_at, None) if occurred_at is not None else None
        intent = {
            "operation": "record",
            "kind": normalized_kind,
            "label": normalized_label,
            "calories": normalized_calories,
            "macros": normalized_macros,
            "status": normalized_status,
            "occurredAt": _iso_utc(explicit_occurrence) if explicit_occurrence else None,
        }
        intent_digest = _digest(intent)
        write_time = _aware_datetime(None, now)
        with self._locked():
            state = self._read()
            replay = self._replay(state, request, intent_digest)
            if replay is not None:
                return replay
            occurrence = explicit_occurrence or write_time
            event = {
                "id": uuid4().hex,
                "kind": normalized_kind,
                "label": normalized_label,
                "calories": normalized_calories,
                "macros": normalized_macros,
                "status": normalized_status,
                "occurredAt": _iso_utc(occurrence),
                "localDate": _local_date(occurrence),
                "createdAt": _iso_utc(write_time),
                "supersedesEventId": None,
            }
            state["events"].append(event)
            self._remember(state, request, intent_digest, event["id"])
            self._write(state, write_time)
            return {"replayed": False, "event": copy.deepcopy(event)}

    def correct_event(
        self,
        *,
        request_id: str,
        event_id: str,
        label: str | None = None,
        calories: dict[str, Any] | None = None,
        macros: dict[str, Any] | None = None,
        status: str | None = None,
        occurred_at: str | datetime | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if all(value is None for value in (label, calories, macros, status, occurred_at)):
            raise ValueError("at least one correction field is required")
        request = _text(request_id, "request_id", limit=200)
        target_id = _text(event_id, "event_id", limit=200)
        normalized_label = _text(label, "label") if label is not None else None
        normalized_calories = _calorie_range(calories) if calories is not None else None
        normalized_macros = _macros(macros) if macros is not None else None
        explicit_occurrence = _aware_datetime(occurred_at, None) if occurred_at is not None else None
        intent = {
            "operation": "correct",
            "eventId": target_id,
            "label": normalized_label,
            "calories": normalized_calories,
            "macros": normalized_macros,
            "status": status,
            "occurredAt": _iso_utc(explicit_occurrence) if explicit_occurrence else None,
        }
        intent_digest = _digest(intent)
        write_time = _aware_datetime(None, now)
        with self._locked():
            state = self._read()
            replay = self._replay(state, request, intent_digest)
            if replay is not None:
                return replay
            target = next((item for item in state["events"] if item.get("id") == target_id), None)
            if target is None:
                raise ValueError("event_id was not found")
            if any(item.get("supersedesEventId") == target_id for item in state["events"]):
                raise ValueError("event was already corrected")
            normalized_status = _status(target["kind"], status or target["status"])
            occurrence = explicit_occurrence or _aware_datetime(target["occurredAt"], None)
            event = {
                "id": uuid4().hex,
                "kind": target["kind"],
                "label": normalized_label if normalized_label is not None else target["label"],
                "calories": normalized_calories if normalized_calories is not None else target["calories"],
                "macros": normalized_macros if normalized_macros is not None else target["macros"],
                "status": normalized_status,
                "occurredAt": _iso_utc(occurrence),
                "localDate": _local_date(occurrence),
                "createdAt": _iso_utc(write_time),
                "supersedesEventId": target_id,
            }
            state["events"].append(event)
            self._remember(state, request, intent_digest, event["id"])
            self._write(state, write_time)
            return {"replayed": False, "event": copy.deepcopy(event)}

    def read(self) -> dict[str, Any]:
        with self._locked():
            return copy.deepcopy(self._read())

    def get_day(
        self, local_date: str | date | None = None, *, now: datetime | None = None
    ) -> dict[str, Any]:
        selected_day = _day(local_date, now)
        state = self.read()
        superseded = {
            event.get("supersedesEventId")
            for event in state["events"]
            if event.get("supersedesEventId")
        }
        events = [
            copy.deepcopy(event)
            for event in state["events"]
            if event.get("id") not in superseded
            and event.get("localDate") == selected_day
            and event.get("status") != "cancelled"
        ]
        food = [event for event in events if event.get("kind") == "food"]
        completed = [
            event
            for event in events
            if event.get("kind") == "exercise" and event.get("status") == "completed"
        ]
        food_min = sum(event["calories"]["min"] for event in food)
        food_max = sum(event["calories"]["max"] for event in food)
        exercise_min = sum(event["calories"]["min"] for event in completed)
        exercise_max = sum(event["calories"]["max"] for event in completed)
        target = state["dailyTargetCalories"]
        net_min = food_min - exercise_max
        net_max = food_max - exercise_min
        workout = {
            "planned": any(
                event.get("kind") == "exercise" and event.get("status") == "planned"
                for event in events
            ),
            "completed": bool(completed),
        }
        return {
            "date": selected_day,
            "targetCalories": _clean(target),
            "foodCalories": {"min": _clean(food_min), "max": _clean(food_max)},
            "completedExerciseCalories": {
                "min": _clean(exercise_min),
                "max": _clean(exercise_max),
            },
            "netCalories": {"min": _clean(net_min), "max": _clean(net_max)},
            "remainingCalories": {
                "min": _clean(target - net_max),
                "max": _clean(target - net_min),
            },
            "workout": workout,
            "events": events,
        }

    def compact_status(
        self, local_date: str | date | None = None, *, now: datetime | None = None
    ) -> dict[str, Any]:
        day_state = self.get_day(local_date, now=now)
        events = day_state["events"]
        return {
            "date": day_state["date"],
            "targetCalories": day_state["targetCalories"],
            "foodCalories": day_state["foodCalories"],
            "completedExerciseCalories": day_state["completedExerciseCalories"],
            "netCalories": day_state["netCalories"],
            "remainingCalories": day_state["remainingCalories"],
            "mealLogged": any(event["kind"] == "food" for event in events),
            "workoutPlanned": any(
                event["kind"] == "exercise" and event["status"] == "planned" for event in events
            ),
            "workoutCompleted": any(
                event["kind"] == "exercise" and event["status"] == "completed" for event in events
            ),
        }
