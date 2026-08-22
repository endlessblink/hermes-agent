"""Nothing in the Life-Boat system should exist with nobody reading or writing it.

Every defect found tonight was found by following breakage. The orphaned
reviewer module was different: a discovery check caught it because "exists in
one place and not the other" is a condition a machine can test. This applies
the same idea to the artifacts the bot keeps — journals, rollups, queues,
pattern notes — so a thing that nothing produces, or nothing consumes, shows up
as a finding rather than waiting for someone to notice.

An orphan is not automatically a bug. It is either something to connect or
something to delete, and the audit's job is to force that decision rather than
leave it unasked.
"""

from __future__ import annotations

import pytest

from gateway.lifeboat_connections import (
    Artifact,
    audit_connections,
    orphan_reasons,
)


def _artifact(name, *, produced_by=(), consumed_by=(), exists=True, expected=True):
    return Artifact(
        name=name,
        exists=exists,
        expected=expected,
        produced_by=tuple(produced_by),
        consumed_by=tuple(consumed_by),
    )


def test_a_fully_connected_artifact_has_no_findings() -> None:
    artifact = _artifact("daily journal", produced_by=("nightly summary",), consumed_by=("check-in",))

    assert orphan_reasons(artifact) == ()


def test_an_artifact_nothing_writes_is_reported() -> None:
    """The weekly rollup: one stale file, no job producing it."""
    artifact = _artifact("weekly rollup", produced_by=(), consumed_by=("check-in",))

    assert "nothing produces it" in " ".join(orphan_reasons(artifact))


def test_an_artifact_nothing_reads_is_reported() -> None:
    """A note kept faithfully that never reaches a conversation."""
    artifact = _artifact("patterns note", produced_by=("hand",), consumed_by=())

    assert "nothing reads it" in " ".join(orphan_reasons(artifact))


def test_an_expected_artifact_that_does_not_exist_is_reported() -> None:
    """Monthly, quarterly and yearly rollups: expected and absent."""
    artifact = _artifact("monthly rollup", exists=False, produced_by=(), consumed_by=())

    assert "does not exist" in " ".join(orphan_reasons(artifact))


def test_an_artifact_that_is_neither_expected_nor_present_is_ignored() -> None:
    artifact = _artifact("retired thing", exists=False, expected=False)

    assert orphan_reasons(artifact) == ()


def test_an_artifact_with_no_producer_and_no_consumer_reports_both() -> None:
    artifact = _artifact("stray note")

    reasons = " ".join(orphan_reasons(artifact))
    assert "nothing produces it" in reasons
    assert "nothing reads it" in reasons


def test_the_audit_returns_a_finding_per_orphan() -> None:
    artifacts = [
        _artifact("daily journal", produced_by=("nightly",), consumed_by=("check-in",)),
        _artifact("weekly rollup", consumed_by=("check-in",)),
        _artifact("patterns note", produced_by=("hand",)),
    ]

    findings = audit_connections(artifacts)

    assert len(findings) == 2
    assert all(f.artifact != "daily journal" for f in findings)


def test_a_finding_names_the_artifact_and_the_reasons() -> None:
    findings = audit_connections([_artifact("weekly rollup", consumed_by=("check-in",))])

    assert findings[0].artifact == "weekly rollup"
    assert findings[0].reasons


def test_an_empty_inventory_is_clean() -> None:
    assert audit_connections([]) == ()


def test_findings_are_stable_in_order() -> None:
    artifacts = [_artifact("b"), _artifact("a")]

    assert [f.artifact for f in audit_connections(artifacts)] == ["b", "a"]
