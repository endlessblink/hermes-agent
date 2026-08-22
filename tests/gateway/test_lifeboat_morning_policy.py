"""The morning check-in's intended cadence lives in one editable place.

The verifier used to hardcode Mon/Wed/Fri while the job actually ran daily, so
the check failed on a disagreement about intent rather than on a fault. Intent
now has a home: daily by default, changeable to any cron expression, and
switchable off without the verifier calling that a failure.
"""

from __future__ import annotations

from gateway.lifeboat_morning import (
    DEFAULT_MORNING_SCHEDULE,
    MorningPolicy,
    morning_job_problems,
    resolve_morning_policy,
)


TOPIC = "telegram:-1004230590253:2"


def _job(expr=DEFAULT_MORNING_SCHEDULE, enabled=True, state="scheduled", deliver=TOPIC):
    return {
        "name": "lifeboat-morning-check-in",
        "enabled": enabled,
        "state": state,
        "schedule": {"expr": expr},
        "deliver": deliver,
    }


def test_the_default_cadence_is_daily() -> None:
    assert DEFAULT_MORNING_SCHEDULE == "0 9 * * *"


def test_an_unset_environment_resolves_to_the_daily_default() -> None:
    policy = resolve_morning_policy({})

    assert policy.enabled is True
    assert policy.schedule == DEFAULT_MORNING_SCHEDULE


def test_a_custom_schedule_is_honoured(monkeypatch) -> None:
    policy = resolve_morning_policy({"LIFEBOAT_MORNING_SCHEDULE": "0 7 * * 1,3,5"})

    assert policy.enabled is True
    assert policy.schedule == "0 7 * * 1,3,5"


def test_the_check_in_can_be_switched_off() -> None:
    policy = resolve_morning_policy({"LIFEBOAT_MORNING_SCHEDULE": "off"})

    assert policy.enabled is False


def test_off_is_case_and_space_insensitive() -> None:
    assert resolve_morning_policy({"LIFEBOAT_MORNING_SCHEDULE": " OFF "}).enabled is False


def test_a_job_matching_the_policy_has_no_problems() -> None:
    assert morning_job_problems(_job(), MorningPolicy(True, "0 9 * * *"), topic=TOPIC) == ()


def test_a_daily_job_is_accepted_by_default() -> None:
    """The live job runs daily; the old check called that a failure."""
    assert morning_job_problems(_job("0 9 * * *"), resolve_morning_policy({}), topic=TOPIC) == ()


def test_a_schedule_that_does_not_match_intent_is_reported() -> None:
    problems = morning_job_problems(
        _job("0 9 * * 1,3,5"), MorningPolicy(True, "0 9 * * *"), topic=TOPIC
    )

    assert any("schedule" in problem for problem in problems)


def test_a_disabled_job_is_fine_when_the_policy_says_off() -> None:
    policy = MorningPolicy(False, DEFAULT_MORNING_SCHEDULE)

    assert morning_job_problems(_job(enabled=False, state="paused"), policy, topic=TOPIC) == ()


def test_a_disabled_job_is_reported_when_the_policy_expects_it_on() -> None:
    problems = morning_job_problems(_job(enabled=False), MorningPolicy(True, "0 9 * * *"), topic=TOPIC)

    assert any("disabled" in problem for problem in problems)


def test_a_missing_job_is_reported_when_expected_on() -> None:
    problems = morning_job_problems(None, MorningPolicy(True, "0 9 * * *"), topic=TOPIC)

    assert any("missing" in problem for problem in problems)


def test_a_missing_job_is_fine_when_switched_off() -> None:
    assert morning_job_problems(None, MorningPolicy(False, "0 9 * * *"), topic=TOPIC) == ()


def test_delivery_to_the_wrong_topic_is_always_reported() -> None:
    """Cadence is the user's choice; the destination is a safety boundary."""
    problems = morning_job_problems(
        _job(deliver="telegram:-100999:5"), MorningPolicy(True, "0 9 * * *"), topic=TOPIC
    )

    assert any("topic" in problem for problem in problems)
