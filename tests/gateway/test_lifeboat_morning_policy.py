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


# --- Proactive contact must reach the Life-Boat topic, never another ---------

from gateway.lifeboat_morning import proactive_delivery_problems  # noqa: E402


PA_TOPIC = "telegram:-1004230590253:695"


def _proactive(name, deliver=TOPIC, enabled=True):
    return {"name": name, "enabled": enabled, "deliver": deliver}


def test_a_lifeboat_job_delivering_to_its_own_topic_is_fine() -> None:
    jobs = [_proactive("lifeboat-morning-check-in")]

    assert proactive_delivery_problems(jobs, topic=TOPIC) == ()


def test_a_lifeboat_job_delivering_elsewhere_is_reported() -> None:
    """Found live: an emotional-processing invitation aimed at the assistant topic."""
    jobs = [_proactive("הזמנה אקראית לעיבוד רגשי", deliver=PA_TOPIC)]

    problems = proactive_delivery_problems(jobs, topic=TOPIC)

    assert problems
    assert "הזמנה אקראית לעיבוד רגשי" in problems[0]


def test_a_bare_platform_target_is_reported() -> None:
    """'telegram' resolves to the home channel, which is a different topic."""
    jobs = [_proactive("סיכום ראיות יומי — Life-Boat", deliver="telegram")]

    assert proactive_delivery_problems(jobs, topic=TOPIC)


def test_a_disabled_job_is_not_reported() -> None:
    jobs = [_proactive("lifeboat-spontaneous-check-in", deliver=PA_TOPIC, enabled=False)]

    assert proactive_delivery_problems(jobs, topic=TOPIC) == ()


def test_jobs_unrelated_to_lifeboat_are_left_alone() -> None:
    jobs = [_proactive("daily-freelance-lead-prep", deliver="local")]

    assert proactive_delivery_problems(jobs, topic=TOPIC) == ()


def test_every_misrouted_job_is_named() -> None:
    jobs = [
        _proactive("lifeboat-morning-check-in", deliver=PA_TOPIC),
        _proactive("סיכום ראיות יומי — Life-Boat", deliver="telegram"),
    ]

    assert len(proactive_delivery_problems(jobs, topic=TOPIC)) == 2


# --- A job must live in the profile whose skills and topic it uses ----------

from gateway.lifeboat_morning import job_residency_problems  # noqa: E402


def test_a_lifeboat_job_in_its_own_profile_is_fine() -> None:
    jobs = [{"name": "lifeboat-morning-check-in", "enabled": True,
             "deliver": TOPIC, "skills": ["personal-coaching"]}]

    assert job_residency_problems(jobs, store="life-advisor", topic=TOPIC) == ()


def test_a_lifeboat_job_in_the_base_store_is_reported() -> None:
    """Found live: the nightly summary ran as the default profile, so the
    three skills it asked for did not exist and it said so mid-conversation."""
    jobs = [{"name": "סיכום ראיות יומי — Life-Boat", "enabled": True,
             "deliver": TOPIC,
             "skills": ["personal-coaching", "obsidian", "personal-context-governance"]}]

    problems = job_residency_problems(jobs, store="base", topic=TOPIC)

    assert problems
    assert "סיכום ראיות יומי — Life-Boat" in problems[0]


def test_a_non_lifeboat_job_in_the_base_store_is_fine() -> None:
    jobs = [{"name": "daily-freelance-lead-prep", "enabled": True, "deliver": "local"}]

    assert job_residency_problems(jobs, store="base", topic=TOPIC) == ()


def test_a_disabled_lifeboat_job_in_the_base_store_is_still_reported() -> None:
    """It will start delivering the moment it is switched on."""
    jobs = [{"name": "lifeboat-spontaneous-check-in", "enabled": False, "deliver": TOPIC}]

    assert job_residency_problems(jobs, store="base", topic=TOPIC)


def test_another_subsystems_job_aimed_here_is_not_a_residency_problem() -> None:
    """It needs its own profile's skills; moving it would break it. Whether it
    should alert into a support topic at all is the user's call, not a
    checker's."""
    jobs = [{"name": "agent-ops monitor-of-monitor watchdog", "enabled": False,
             "deliver": TOPIC}]

    assert job_residency_problems(jobs, store="base", topic=TOPIC) == ()
