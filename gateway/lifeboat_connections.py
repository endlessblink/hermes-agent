"""Find the parts of the Life-Boat system that nothing is talking to.

Every defect found on 2026-08-22 was found by following breakage: a screenshot,
a failing test, an error in a log. That works, but only for things that break
loudly. It missed a weekly rollup that stopped being written two weeks earlier,
a 15KB pattern note nothing reads, and three rollup levels that were never
created -- none of which raise an error, because a thing that quietly does
nothing looks exactly like a thing that is fine.

The one exception that session was an orphaned module, caught because "exists
in one tree and not the other" is a condition a machine can test. This applies
that shape to the artifacts the bot keeps.

An orphan is not automatically a bug. It is either something to connect or
something to delete. The audit's job is to force that decision instead of
leaving it unasked for another two weeks.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Artifact:
    """One thing the system keeps, and who produces and consumes it."""

    name: str
    exists: bool = True
    #: Whether the system is supposed to have this at all. A rollup level that
    #: was never built is expected-and-absent; a retired note is neither.
    expected: bool = True
    produced_by: tuple[str, ...] = field(default_factory=tuple)
    consumed_by: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Finding:
    """An artifact that is not fully connected, and why."""

    artifact: str
    reasons: tuple[str, ...]


def orphan_reasons(artifact: Artifact) -> tuple[str, ...]:
    """Return every way this artifact is disconnected, or an empty tuple."""
    if not artifact.exists:
        if not artifact.expected:
            return ()
        return ("it does not exist, and the system expects it to",)

    reasons: list[str] = []
    if not artifact.produced_by:
        reasons.append("nothing produces it, so it can only go stale")
    if not artifact.consumed_by:
        reasons.append("nothing reads it, so keeping it changes nothing")
    return tuple(reasons)


def audit_connections(artifacts) -> tuple[Finding, ...]:
    """Return a finding for every artifact that is not fully connected."""
    findings: list[Finding] = []
    for artifact in artifacts or ():
        reasons = orphan_reasons(artifact)
        if reasons:
            findings.append(Finding(artifact=artifact.name, reasons=reasons))
    return tuple(findings)
