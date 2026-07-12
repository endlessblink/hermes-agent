from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


JERUSALEM = ZoneInfo("Asia/Jerusalem")


def test_office_work_claims_once_after_nine_and_persists_across_processes(tmp_path):
    from agent.daily_assistant_lifecycle import (
        claim_daily_planning_trigger,
        complete_daily_planning_trigger,
    )

    now = datetime(2026, 7, 12, 9, 0, tzinfo=JERUSALEM)

    first = claim_daily_planning_trigger("office-work", tmp_path, now=now)
    second = claim_daily_planning_trigger("office-work", tmp_path, now=now)

    assert first.claimed
    assert first.local_date == "2026-07-12"
    assert first.reason == "scheduled"
    assert second.status == "already_claimed"

    assert complete_daily_planning_trigger(tmp_path, first)
    assert claim_daily_planning_trigger(
        "office-work", tmp_path, now=now
    ).status == "already_completed"


def test_launch_catches_up_after_nine_but_not_before(tmp_path):
    from agent.daily_assistant_lifecycle import claim_daily_planning_trigger

    before = datetime(2026, 7, 12, 8, 59, tzinfo=JERUSALEM)
    after = datetime(2026, 7, 12, 13, 15, tzinfo=JERUSALEM)

    assert claim_daily_planning_trigger(
        "office-work", tmp_path, now=before, on_launch=True
    ).status == "not_due"

    trigger = claim_daily_planning_trigger(
        "office-work", tmp_path, now=after, on_launch=True
    )
    assert trigger.claimed
    assert trigger.reason == "launch_catch_up"


def test_other_profiles_never_claim_daily_planning(tmp_path):
    from agent.daily_assistant_lifecycle import claim_daily_planning_trigger

    now = datetime(2026, 7, 12, 10, 0, tzinfo=JERUSALEM)

    assert claim_daily_planning_trigger("default", tmp_path, now=now).status == "ineligible_profile"
    assert claim_daily_planning_trigger("coder", tmp_path, now=now).status == "ineligible_profile"


def test_timezone_boundary_uses_jerusalem_local_date(tmp_path):
    from agent.daily_assistant_lifecycle import claim_daily_planning_trigger

    # 22:30 UTC is already the next calendar day in Jerusalem in July.
    now = datetime(2026, 7, 11, 22, 30, tzinfo=timezone.utc)

    result = claim_daily_planning_trigger("office-work", tmp_path, now=now)

    assert result.status == "not_due"
    assert result.local_date == "2026-07-12"


def test_concurrent_consumers_create_only_one_daily_claim(tmp_path):
    from agent.daily_assistant_lifecycle import claim_daily_planning_trigger

    now = datetime(2026, 7, 12, 9, 0, tzinfo=JERUSALEM)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: claim_daily_planning_trigger(
                    "office-work", tmp_path, now=now
                ),
                range(16),
            )
        )

    assert sum(result.claimed for result in results) == 1
    assert sum(result.status == "already_claimed" for result in results) == 15


def test_failed_delivery_can_abandon_reservation_and_retry(tmp_path):
    from agent.daily_assistant_lifecycle import (
        abandon_daily_planning_trigger,
        claim_daily_planning_trigger,
    )

    now = datetime(2026, 7, 12, 9, 0, tzinfo=JERUSALEM)
    first = claim_daily_planning_trigger("office-work", tmp_path, now=now)

    assert abandon_daily_planning_trigger(tmp_path, first)
    retry = claim_daily_planning_trigger("office-work", tmp_path, now=now)

    assert retry.claimed
    assert retry.reservation_id != first.reservation_id


def test_crash_stale_reservation_is_reclaimed(tmp_path):
    from agent.daily_assistant_lifecycle import claim_daily_planning_trigger

    first_time = datetime(2026, 7, 12, 9, 0, tzinfo=JERUSALEM)
    first = claim_daily_planning_trigger("office-work", tmp_path, now=first_time)

    retry = claim_daily_planning_trigger(
        "office-work", tmp_path, now=first_time + timedelta(minutes=16)
    )

    assert first.claimed
    assert retry.claimed
    assert retry.reservation_id != first.reservation_id
