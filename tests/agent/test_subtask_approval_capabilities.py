import threading
from datetime import datetime, timedelta, timezone

import pytest

from agent.subtask_approval_capabilities import (
    ApprovalCapabilityError,
    SubtaskApprovalCapabilityRegistry,
)


OPERATIONS = [
    {
        "kind": "create",
        "clientId": "draft-step",
        "title": "Draft the outline",
        "estimateMinutes": 20,
        "order": 0,
    }
]


def _proof(now: datetime, **overrides):
    proof = {
        "taskId": "task-1",
        "operationId": "operation-1",
        "baseRevision": 7,
        "operations": OPERATIONS,
        "previewDigest": "a" * 64,
        "previewExpiresAt": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "requestHash": "b" * 64,
        "proposalId": "proposal-1",
        "proposalRevision": 3,
    }
    proof.update(overrides)
    return proof


def _request(proof, **overrides):
    request = {
        key: proof[key]
        for key in (
            "taskId",
            "operationId",
            "baseRevision",
            "operations",
            "previewDigest",
            "previewExpiresAt",
            "requestHash",
            "proposalId",
            "proposalRevision",
        )
    }
    request.update(overrides)
    return request


def test_capability_is_session_scoped_exact_and_replay_safe():
    now = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
    registry = SubtaskApprovalCapabilityRegistry(clock=lambda: now)
    proof = _proof(now)

    capability = registry.register("session-a", proof)
    registry.authorize("session-a", capability, _request(proof))
    registry.authorize("session-a", capability, _request(proof))

    with pytest.raises(ApprovalCapabilityError, match="session"):
        registry.authorize("session-b", capability, _request(proof))
    with pytest.raises(ApprovalCapabilityError, match="proof"):
        registry.authorize(
            "session-a",
            capability,
            _request(proof, operations=[{**OPERATIONS[0], "title": "Changed"}]),
        )


def test_capability_expires_and_can_be_revoked_by_proof_or_session():
    now = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
    current = [now]
    registry = SubtaskApprovalCapabilityRegistry(clock=lambda: current[0])
    proof = _proof(now)

    capability = registry.register("session-a", proof)
    assert registry.revoke("session-a", proof) == 1
    with pytest.raises(ApprovalCapabilityError, match="unknown"):
        registry.authorize("session-a", capability, _request(proof))

    first = registry.register("session-a", proof)
    second = registry.register("session-a", {**proof, "proposalRevision": 4})
    assert registry.revoke_session("session-a") == 2
    for token in (first, second):
        with pytest.raises(ApprovalCapabilityError, match="unknown"):
            registry.authorize("session-a", token, _request(proof))

    expired = registry.register("session-a", proof)
    current[0] = now + timedelta(minutes=6)
    with pytest.raises(ApprovalCapabilityError, match="expired"):
        registry.authorize("session-a", expired, _request(proof))


def test_revision_invalidates_its_proposal_lineage_but_not_a_newer_revision():
    now = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
    registry = SubtaskApprovalCapabilityRegistry(clock=lambda: now)
    revision_three = _proof(now, proposalRevision=3)
    old_capability = registry.register("session-a", revision_three)

    assert registry.invalidate_proposal("session-a", "task-1", "proposal-1", 3) == 1
    with pytest.raises(ApprovalCapabilityError, match="unknown"):
        registry.authorize("session-a", old_capability, _request(revision_three))
    with pytest.raises(ApprovalCapabilityError, match="invalidated"):
        registry.register("session-a", revision_three)

    revision_four = _proof(now, proposalRevision=4)
    capability = registry.register("session-a", revision_four)
    registry.authorize("session-a", capability, _request(revision_four))


def test_registry_is_thread_safe_and_tokens_are_one_proof_only():
    now = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
    registry = SubtaskApprovalCapabilityRegistry(clock=lambda: now)
    proof = _proof(now)
    tokens: list[str] = []

    threads = [
        threading.Thread(target=lambda: tokens.append(registry.register("session-a", proof)))
        for _ in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(tokens) == len(set(tokens)) == 20
    for token in tokens:
        registry.authorize("session-a", token, _request(proof))
