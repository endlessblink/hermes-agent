from datetime import datetime, timedelta, timezone

import pytest

from agent.subtask_approval_capabilities import subtask_approval_capabilities
from tui_gateway import server


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
OPERATIONS = [{"kind": "create", "clientId": "step-1", "title": "Draft", "order": 0}]


def _decision(decision="approve", **overrides):
    value = {
        "type": "flowstate-mutation-decision",
        "schemaVersion": 1,
        "decision": decision,
        "approval": decision == "approve",
        "action": "subtask_batch",
        "contractVersion": "task-v1",
        "taskId": "task-1",
        "operationId": "operation-1",
        "baseRevision": 7,
        "operations": OPERATIONS,
        "previewDigest": "a" * 64,
        "previewExpiresAt": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "requestHash": "b" * 64,
        "proposalId": "proposal-1",
        "proposalRevision": 3,
    }
    value.update(overrides)
    return value


@pytest.fixture(autouse=True)
def session():
    server._sessions["ui-session"] = {
        "session_key": "trusted-session-key",
        "personal_assistant": True,
    }
    subtask_approval_capabilities.revoke_session("trusted-session-key")
    yield
    subtask_approval_capabilities.revoke_session("trusted-session-key")
    server._sessions.pop("ui-session", None)


def test_gateway_registers_only_exact_approved_decision(monkeypatch):
    monkeypatch.setattr(subtask_approval_capabilities, "_clock", lambda: NOW)

    response = server._methods["flowstate.approval.register"](
        "r1", {"session_id": "ui-session", "decision": _decision()}
    )
    capability = response["result"]["approvalCapability"]
    assert isinstance(capability, str) and len(capability) >= 32
    subtask_approval_capabilities.authorize(
        "trusted-session-key",
        capability,
        {
            key: _decision()[key]
            for key in (
                "taskId", "operationId", "baseRevision", "operations",
                "previewDigest", "previewExpiresAt", "requestHash",
                "proposalId", "proposalRevision",
            )
        },
    )


def test_gateway_rejects_missing_session_and_expired_approval(monkeypatch):
    missing = server._methods["flowstate.approval.register"](
        "r1", {"session_id": "missing", "decision": _decision()}
    )
    assert missing["error"]["code"] == 4001

    monkeypatch.setattr(
        subtask_approval_capabilities,
        "_clock",
        lambda: NOW + timedelta(minutes=10),
    )
    expired = server._methods["flowstate.approval.register"](
        "r2", {"session_id": "ui-session", "decision": _decision()}
    )
    assert expired["error"]["code"] == 4002


@pytest.mark.parametrize(
    "decision",
    [
        _decision(correction="Make it shorter"),
        _decision(approval=False),
        _decision(decision="maybe"),
        _decision(extra="not allowed"),
        _decision(operations=[{"kind": "create", "clientId": " step-1", "title": "Draft"}]),
        _decision(requestHash=int("1" * 64)),
        _decision(previewExpiresAt="2026-07-16T08:05+00"),
        _decision(operationId="x" * 161),
        _decision(taskId="x" * 161),
        _decision(proposalId="x" * 121),
        _decision(baseRevision=2**53),
    ],
)
def test_gateway_rejects_noncanonical_or_nonapproved_decisions(decision):
    response = server._methods["flowstate.approval.register"](
        "r1", {"session_id": "ui-session", "decision": decision}
    )

    assert response["error"]["code"] == 4002
    assert "approvalCapability" not in str(response)


def test_gateway_revise_revokes_prior_exact_proof(monkeypatch):
    monkeypatch.setattr(subtask_approval_capabilities, "_clock", lambda: NOW)
    approved = server._methods["flowstate.approval.register"](
        "r1", {"session_id": "ui-session", "decision": _decision()}
    )
    capability = approved["result"]["approvalCapability"]

    revised = server._methods["flowstate.approval.register"](
        "r2",
        {
            "session_id": "ui-session",
            "decision": _decision("revise", correction="Split the review step"),
        },
    )

    assert revised["result"] == {"revoked": 1, "status": "revised"}
    with pytest.raises(Exception, match="unknown"):
        subtask_approval_capabilities.authorize(
            "trusted-session-key",
            capability,
            {
                key: _decision()[key]
                for key in (
                    "taskId", "operationId", "baseRevision", "operations",
                    "previewDigest", "previewExpiresAt", "requestHash",
                    "proposalId", "proposalRevision",
                )
            },
        )


def test_gateway_accepts_explicit_blank_refresh_and_blocks_old_proof(monkeypatch):
    monkeypatch.setattr(subtask_approval_capabilities, "_clock", lambda: NOW)

    refreshed = server._methods["flowstate.approval.register"](
        "r1",
        {
            "session_id": "ui-session",
            "decision": _decision("revise", correction=""),
        },
    )
    assert refreshed["result"] == {"revoked": 0, "status": "revised"}

    stale = server._methods["flowstate.approval.register"](
        "r2", {"session_id": "ui-session", "decision": _decision()}
    )
    assert stale["error"]["code"] == 4002


def test_trusted_breakdown_revision_invalidates_older_preview_lineage(monkeypatch):
    monkeypatch.setattr(subtask_approval_capabilities, "_clock", lambda: NOW)
    approved = server._methods["flowstate.approval.register"](
        "r1", {"session_id": "ui-session", "decision": _decision()}
    )
    capability = approved["result"]["approvalCapability"]
    revision = {
        "action": "revise",
        "approval": False,
        "proposalId": "proposal-1",
        "proposalRevision": 3,
        "schemaVersion": 1,
        "scope": "working-session",
        "steps": [{"clientId": "step-1", "doneEnough": "Reviewable", "title": "Draft"}],
        "stoppingRule": "Stop at a reviewable draft.",
        "targetOutcome": "A reviewable draft",
        "task": {"baseRevision": 7, "id": "task-1", "title": "Prepare launch"},
        "type": "task-breakdown-revision",
    }

    revised = server._methods["flowstate.approval.register"](
        "r2", {"session_id": "ui-session", "decision": revision}
    )
    assert revised["result"] == {"revoked": 1, "status": "revised"}

    with pytest.raises(Exception, match="unknown"):
        subtask_approval_capabilities.authorize(
            "trusted-session-key",
            capability,
            {
                key: _decision()[key]
                for key in (
                    "taskId", "operationId", "baseRevision", "operations",
                    "previewDigest", "previewExpiresAt", "requestHash",
                    "proposalId", "proposalRevision",
                )
            },
        )


def test_gateway_session_teardown_revokes_capabilities(monkeypatch):
    monkeypatch.setattr(subtask_approval_capabilities, "_clock", lambda: NOW)
    approved = server._methods["flowstate.approval.register"](
        "r1", {"session_id": "ui-session", "decision": _decision()}
    )
    capability = approved["result"]["approvalCapability"]
    monkeypatch.setattr(server, "_finalize_session", lambda *_args, **_kwargs: None)
    def _failed_unregister(_key):
        raise RuntimeError("notifier cleanup failed")

    monkeypatch.setattr("tools.approval.unregister_gateway_notify", _failed_unregister)

    server._teardown_session(server._sessions["ui-session"])

    with pytest.raises(Exception, match="unknown"):
        subtask_approval_capabilities.authorize(
            "trusted-session-key",
            capability,
            {
                key: _decision()[key]
                for key in (
                    "taskId", "operationId", "baseRevision", "operations",
                    "previewDigest", "previewExpiresAt", "requestHash",
                    "proposalId", "proposalRevision",
                )
            },
        )
