"""Every Life-Boat task that claims a regression must name it.

Two tasks reached REVIEW today with their acceptance unmet, and one of them
described a reviewer that had never executed. Nothing caught that, because no
test anywhere referenced a task id: there was no way to ask "which regression
proves BUG-6?" and get an answer.

These tests close that. A task id appearing in a test docstring is the link,
and this file checks the link exists for the tasks that have been verified.
"""

from __future__ import annotations

import pathlib
import re

import pytest


TESTS_DIR = pathlib.Path(__file__).resolve().parent
TASK_ID_RE = re.compile(r"\b((?:BUG|TASK|FEATURE|INQUIRY)-\d+)\b")

#: Tasks whose acceptance is claimed to be covered by tests in this suite.
#: Adding a task here without a regression naming it fails the build.
TRACED_TASKS = (
    "BUG-6",
    "BUG-21",
    "BUG-25",
    "BUG-30",
    "BUG-32",
    "TASK-10",
)


def _task_ids_in_tests() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in TESTS_DIR.glob("test_*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for task_id in set(TASK_ID_RE.findall(text)):
            found.setdefault(task_id, []).append(path.name)
    return found


@pytest.mark.parametrize("task_id", TRACED_TASKS)
def test_a_traced_task_has_at_least_one_regression_naming_it(task_id: str) -> None:
    found = _task_ids_in_tests()

    assert task_id in found, (
        f"{task_id} claims test coverage but no test names it. "
        "Name the task id in the test's docstring so the acceptance criterion "
        "and the regression stay connected."
    )


def test_the_traced_list_is_not_empty() -> None:
    assert TRACED_TASKS


def test_every_traced_task_id_is_well_formed() -> None:
    for task_id in TRACED_TASKS:
        assert TASK_ID_RE.fullmatch(task_id), task_id


def test_task_ids_are_discoverable_by_a_plain_search() -> None:
    """The mechanism is a grep, deliberately: no registry to fall out of date."""
    found = _task_ids_in_tests()

    assert found, "no task ids found anywhere in the gateway tests"
