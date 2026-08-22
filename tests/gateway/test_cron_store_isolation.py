"""No test may write into the developer's real cron store.

cron.jobs resolves its directory once, at import time, the same way
hermes_state resolved its database path. Test modules import it during
collection, before the per-test HERMES_HOME exists, so jobs created by tests
landed in the live ~/.hermes/cron/jobs.json and in the life-advisor profile's.

That is not a tidiness problem. The running gateway schedules whatever is in
those files, so fixture jobs named "boom", "pin test" and "claim job" were
being executed on the user's machine and failing every tick.
"""

from __future__ import annotations

from pathlib import Path

import cron.jobs as cron_jobs


def _real_home() -> Path:
    return Path.home() / ".hermes"


def test_the_cron_directory_is_not_the_real_one() -> None:
    assert Path(cron_jobs.HERMES_DIR).resolve() != _real_home().resolve()


def test_the_jobs_file_is_not_the_real_one() -> None:
    assert Path(cron_jobs.JOBS_FILE).resolve() != (_real_home() / "cron" / "jobs.json").resolve()


def test_the_jobs_file_lives_under_the_test_home() -> None:
    import os

    assert str(os.environ["HERMES_HOME"]) in str(cron_jobs.JOBS_FILE)


def test_derived_cron_paths_follow_the_same_home() -> None:
    jobs_file = Path(cron_jobs.JOBS_FILE)

    assert jobs_file.parent == Path(cron_jobs.CRON_DIR)
    assert Path(cron_jobs.CRON_DIR).parent == Path(cron_jobs.HERMES_DIR)


def test_creating_a_job_does_not_touch_the_real_store() -> None:
    real = _real_home() / "cron" / "jobs.json"
    before = real.read_text(encoding="utf-8") if real.is_file() else None

    cron_jobs.save_jobs([{"name": "isolation-probe", "schedule": {"expr": "0 9 * * *"}}])

    after = real.read_text(encoding="utf-8") if real.is_file() else None
    assert after == before, "a test wrote into the developer's real cron store"
