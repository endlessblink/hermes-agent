from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest


def _calories(low: float, high: float | None = None) -> dict[str, float]:
    return {"min": low, "max": low if high is None else high}


def test_health_events_use_the_jerusalem_day_and_survive_reload(tmp_path):
    from agent.health_state import HealthStateStore

    store = HealthStateStore(tmp_path)
    late_utc = datetime(2026, 7, 19, 21, 30, tzinfo=timezone.utc)

    result = store.record_event(
        request_id="meal-1",
        kind="food",
        label="late yogurt",
        calories=_calories(120),
        macros={"proteinGrams": 10.5},
        now=late_utc,
    )

    assert result["event"]["localDate"] == "2026-07-20"
    reloaded = HealthStateStore(tmp_path).get_day("2026-07-20")
    assert reloaded["foodCalories"] == _calories(120)
    assert reloaded["events"][0]["label"] == "late yogurt"


def test_duplicate_request_replays_but_conflicting_reuse_is_rejected(tmp_path):
    from agent.health_state import HealthStateStore

    store = HealthStateStore(tmp_path)
    first = store.record_event(
        request_id="meal-1",
        kind="food",
        label="coffee",
        calories=_calories(200, 220),
    )
    replay = store.record_event(
        request_id="meal-1",
        kind="food",
        label="coffee",
        calories=_calories(200, 220),
    )

    assert replay["replayed"] is True
    assert replay["event"]["id"] == first["event"]["id"]
    assert len(store.read()["events"]) == 1

    with pytest.raises(ValueError, match="different operation"):
        store.record_event(
            request_id="meal-1",
            kind="food",
            label="different meal",
            calories=_calories(500),
        )


def test_correction_appends_an_immutable_replacement_without_double_counting(tmp_path):
    from agent.health_state import HealthStateStore

    store = HealthStateStore(tmp_path)
    original = store.record_event(
        request_id="meal-1",
        kind="food",
        label="coffee",
        calories=_calories(200),
        occurred_at="2026-07-19T10:00:00+03:00",
    )["event"]

    correction = store.correct_event(
        request_id="meal-1-correction",
        event_id=original["id"],
        calories=_calories(120),
    )["event"]

    raw = store.read()
    assert len(raw["events"]) == 2
    assert raw["events"][0]["id"] == original["id"]
    assert correction["supersedesEventId"] == original["id"]
    assert store.get_day("2026-07-19")["foodCalories"] == _calories(120)


def test_only_completed_exercise_changes_net_and_remaining_calories(tmp_path):
    from agent.health_state import HealthStateStore

    store = HealthStateStore(tmp_path, daily_target_calories=1900)
    store.record_event(
        request_id="meal-1",
        kind="food",
        label="lunch",
        calories=_calories(700),
        occurred_at="2026-07-19T12:00:00+03:00",
    )
    planned = store.record_event(
        request_id="workout-1",
        kind="exercise",
        label="walk",
        calories=_calories(180, 220),
        status="planned",
        occurred_at="2026-07-19T18:00:00+03:00",
    )["event"]

    before = store.get_day("2026-07-19")
    assert before["completedExerciseCalories"] == _calories(0)
    assert before["netCalories"] == _calories(700)
    assert before["remainingCalories"] == _calories(1200)
    assert before["workout"] == {"planned": True, "completed": False}

    store.correct_event(
        request_id="workout-1-complete",
        event_id=planned["id"],
        status="completed",
    )
    after = store.get_day("2026-07-19")
    assert after["completedExerciseCalories"] == _calories(180, 220)
    assert after["netCalories"] == _calories(480, 520)
    assert after["remainingCalories"] == _calories(1380, 1420)
    assert after["workout"] == {"planned": False, "completed": True}


def test_old_days_remain_queryable_after_the_current_day_changes(tmp_path):
    from agent.health_state import HealthStateStore

    store = HealthStateStore(tmp_path)
    store.record_event(
        request_id="old-meal",
        kind="food",
        label="old meal",
        calories=_calories(300),
        occurred_at="2026-07-18T12:00:00+03:00",
    )
    store.record_event(
        request_id="new-meal",
        kind="food",
        label="new meal",
        calories=_calories(450),
        occurred_at="2026-07-19T12:00:00+03:00",
    )

    assert store.get_day("2026-07-18")["foodCalories"] == _calories(300)
    assert store.get_day("2026-07-19")["foodCalories"] == _calories(450)


def test_compact_status_has_a_strict_privacy_allowlist(tmp_path):
    from agent.health_state import HealthStateStore

    store = HealthStateStore(tmp_path)
    store.record_event(
        request_id="private-meal",
        kind="food",
        label="private medical meal detail",
        calories=_calories(300, 350),
        macros={"proteinGrams": 20, "privateNote": "do not share"},
        occurred_at="2026-07-19T12:00:00+03:00",
    )

    status = store.compact_status("2026-07-19")
    assert set(status) == {
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
    encoded = json.dumps(status, sort_keys=True)
    assert "private medical meal detail" not in encoded
    assert "privateNote" not in encoded
    assert "protein" not in encoded.lower()
    assert "events" not in status


def test_cancelled_correction_removes_an_event_from_derived_totals(tmp_path):
    from agent.health_state import HealthStateStore

    store = HealthStateStore(tmp_path)
    event = store.record_event(
        request_id="meal-1",
        kind="food",
        label="mistaken entry",
        calories=_calories(400),
        occurred_at="2026-07-19T12:00:00+03:00",
    )["event"]
    store.correct_event(
        request_id="meal-1-cancel",
        event_id=event["id"],
        status="cancelled",
    )

    day = store.get_day("2026-07-19")
    assert day["foodCalories"] == _calories(0)
    assert day["events"] == []


def test_corrupt_health_history_is_never_silently_overwritten(tmp_path):
    from agent.health_state import HealthStateStore

    ledger = tmp_path / "state" / "health" / "ledger.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("not valid json", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot be read"):
        HealthStateStore(tmp_path).record_event(
            request_id="meal-1",
            kind="food",
            label="meal",
            calories=_calories(100),
        )

    assert ledger.read_text(encoding="utf-8") == "not valid json"
