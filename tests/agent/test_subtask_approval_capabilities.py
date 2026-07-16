from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from agent.subtask_approval_capabilities import (
    ApprovalCapabilityError,
    SubtaskApprovalCapabilityRegistry,
)


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)


def _proof(**overrides):
    proof = {
        "taskId": "task-1",
        "operationId": "operation-1",
        "baseRevision": 7,
        "operations": [{"kind": "create", "clientId": "step-1", "title": "Draft"}],
        "previewDigest": "a" * 64,
        "previewExpiresAt": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "requestHash": "b" * 64,
        "proposalId": "proposal-1",
        "proposalRevision": 3,
    }
    proof.update(overrides)
    return proof


def test_capability_is_session_scoped_exact_and_replay_safe():
    registry = SubtaskApprovalCapabilityRegistry(clock=lambda: NOW)
    proof = _proof()
    token = registry.register("session-a", proof)

    registry.authorize("session-a", token, proof)
    registry.authorize("session-a", token, proof)

    with pytest.raises(ApprovalCapabilityError, match="session"):
        registry.authorize("session-b", token, proof)
    with pytest.raises(ApprovalCapabilityError, match="proof"):
        registry.authorize("session-a", token, {**proof, "baseRevision": 8})


def test_capability_expires_and_session_teardown_revokes_it():
    current = [NOW]
    registry = SubtaskApprovalCapabilityRegistry(clock=lambda: current[0])
    proof = _proof()
    expired = registry.register("session-a", proof)
    current[0] = NOW + timedelta(minutes=6)
    with pytest.raises(ApprovalCapabilityError, match="expired"):
        registry.authorize("session-a", expired, proof)

    current[0] = NOW
    first = registry.register("session-a", proof)
    second = registry.register("session-a", _proof(proposalRevision=4))
    assert registry.revoke_session("session-a") == 2
    for token in (first, second):
        with pytest.raises(ApprovalCapabilityError, match="unknown"):
            registry.authorize("session-a", token, proof)


def test_revision_invalidates_only_its_old_proposal_lineage():
    registry = SubtaskApprovalCapabilityRegistry(clock=lambda: NOW)
    proof = _proof()
    old = registry.register("session-a", proof)

    assert registry.invalidate_proposal("session-a", "task-1", "proposal-1", 3) == 1
    with pytest.raises(ApprovalCapabilityError, match="unknown"):
        registry.authorize("session-a", old, proof)
    with pytest.raises(ApprovalCapabilityError, match="invalidated"):
        registry.register("session-a", proof)

    newer = _proof(proposalRevision=4)
    registry.authorize("session-a", registry.register("session-a", newer), newer)


def test_concurrent_registration_authorization_and_invalidation_are_atomic():
    registry = SubtaskApprovalCapabilityRegistry(clock=lambda: NOW)
    proof = _proof()

    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _index: registry.register("session-a", proof), range(32)))
        list(pool.map(lambda token: registry.authorize("session-a", token, proof), tokens))

    assert len(set(tokens)) == 32
    assert registry.invalidate_proposal("session-a", "task-1", "proposal-1", 3) == 32
    for token in tokens:
        with pytest.raises(ApprovalCapabilityError, match="unknown"):
            registry.authorize("session-a", token, proof)
