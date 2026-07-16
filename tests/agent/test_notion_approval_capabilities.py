from datetime import datetime, timedelta, timezone

import pytest

from agent.notion_approval_capabilities import (
    NotionApprovalCapabilityError,
    NotionApprovalCapabilityRegistry,
)


NOW = datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)


def proof(**overrides):
    value = {
        "tool": "notion_mutation",
        "apply": {
            "mode": "apply",
            "operation_id": "notion-op-1",
            "action": "set_status",
            "data_source_id": "source-1",
            "page_id": "page-1",
            "status_property": "Status",
            "status_name": "In progress",
            "preview_digest": "sha256:" + "a" * 64,
            "preview_expires_at": (NOW + timedelta(minutes=5)).isoformat(),
        },
        "previewExpiresAt": (NOW + timedelta(minutes=5)).isoformat(),
    }
    value.update(overrides)
    return value


def test_capability_is_session_scoped_exact_and_expires():
    registry = NotionApprovalCapabilityRegistry(clock=lambda: NOW)
    token = registry.register("session-1", proof())
    registry.authorize("session-1", token, proof())
    registry.authorize_registered("session-1", proof())

    with pytest.raises(NotionApprovalCapabilityError, match="another session"):
        registry.authorize("session-2", token, proof())
    with pytest.raises(NotionApprovalCapabilityError, match="required"):
        registry.authorize_registered("session-2", proof())
    changed = proof()
    changed["apply"]["status_name"] = "Done"
    with pytest.raises(NotionApprovalCapabilityError, match="does not match"):
        registry.authorize("session-1", token, changed)

    registry._clock = lambda: NOW + timedelta(minutes=6)
    with pytest.raises(NotionApprovalCapabilityError, match="expired"):
        registry.authorize("session-1", token, proof())
    with pytest.raises(NotionApprovalCapabilityError, match="required"):
        registry.authorize_registered("session-1", proof())


def test_revoke_session_removes_only_that_sessions_capabilities():
    registry = NotionApprovalCapabilityRegistry(clock=lambda: NOW)
    first = registry.register("session-1", proof())
    second = registry.register("session-2", proof())
    assert registry.revoke_session("session-1") == 1
    with pytest.raises(NotionApprovalCapabilityError, match="unknown"):
        registry.authorize("session-1", first, proof())
    registry.authorize("session-2", second, proof())


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"tool": "unknown", "apply": {"mode": "apply"}, "previewExpiresAt": "x"},
        proof(apply={"mode": "preview"}),
        proof(previewExpiresAt=(NOW - timedelta(seconds=1)).isoformat()),
    ],
)
def test_registration_rejects_invalid_or_expired_proof(invalid):
    registry = NotionApprovalCapabilityRegistry(clock=lambda: NOW)
    with pytest.raises(NotionApprovalCapabilityError):
        registry.register("session", invalid)
