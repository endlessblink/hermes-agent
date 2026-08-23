"""Every review-log pattern is accounted for, and its enforcer still exists.

The classification in the Obsidian log says who owns each pattern. This says
whether anything stops it: a named check that resolves, or an explicit reason
no check can decide it. A pattern that appears in the log and in neither list
fails here, so a new entry cannot be silently unowned.
"""

from __future__ import annotations

import pytest

from scripts.verify_review_log_coverage import ENFORCED, NOT_CODE_DECIDABLE, REVIEW_LOG, run


def test_every_named_enforcer_resolves_and_no_pattern_is_unaccounted() -> None:
    if not REVIEW_LOG.exists():
        pytest.skip("the governance vault is not mounted here")
    assert run() == []


def test_a_pattern_is_never_in_both_lists() -> None:
    assert set(ENFORCED) & set(NOT_CODE_DECIDABLE) == set()
