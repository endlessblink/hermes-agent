from datetime import datetime, timedelta, timezone

import pytest

from agent.subtask_approval_capabilities import subtask_approval_capabilities
from tui_gateway import server


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)


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
        "operations": [{"kind": "create", "clientId": "step-1", "title": "Draft"}],
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
    server._sessions["ui-session"] = {"session_key": "trusted-session-key"}
    subtask_approval_capabilities.revoke_session("trusted-session-key")
    yield
    subtask_approval_capabilities.revoke_session("trusted-session-key")
    server._sessions.pop("ui-session", None)


def test_gateway_registers_only_exact_approved_decision(monkeypatch):
    monkeypatch.setattr(subtask_approval_capabilities, "_clock", lambda: NOW)
    response = server._methods["flowstate.approval.register"](
        "r1", {"session_id": "ui-session", "decision": _decision()}
    )
    token = response["result"]["approvalCapability"]
    proof = {key: _decision()[key] for key in (
        "taskId", "operationId", "baseRevision", "operations", "previewDigest",
        "previewExpiresAt", "requestHash", "proposalId", "proposalRevision",
    )}
    subtask_approval_capabilities.authorize("trusted-session-key", token, proof)


@pytest.mark.parametrize(
    "decision",
    [
        _decision(approval=False),
        _decision(decision="maybe"),
        _decision(extra="not allowed"),
        _decision(operations=[{"kind": "create", "clientId": " step-1", "title": "Draft"}]),
        _decision(decision="revise", approval=False, correction="Fix it", requestHash=7),
    ],
)
def test_gateway_rejects_noncanonical_or_nonapproved_decisions(decision):
    response = server._methods["flowstate.approval.register"](
        "r1", {"session_id": "ui-session", "decision": decision}
    )
    assert response["error"]["code"] == 4002


def test_gateway_revise_revokes_prior_proof_and_teardown_revokes_session(monkeypatch):
    monkeypatch.setattr(subtask_approval_capabilities, "_clock", lambda: NOW)
    approved = server._methods["flowstate.approval.register"](
        "r1", {"session_id": "ui-session", "decision": _decision()}
    )
    token = approved["result"]["approvalCapability"]
    revised = server._methods["flowstate.approval.register"](
        "r2", {
            "session_id": "ui-session",
            "decision": _decision("revise", correction="Split the review step"),
        },
    )
    assert revised["result"] == {"revoked": 1, "status": "revised"}

    approved = server._methods["flowstate.approval.register"](
        "r3", {"session_id": "ui-session", "decision": _decision(proposalRevision=4)}
    )
    newer_token = approved["result"]["approvalCapability"]
    monkeypatch.setattr(server, "_finalize_session", lambda *_args, **_kwargs: None)
    server._teardown_session(server._sessions["ui-session"])

    for capability in (token, newer_token):
        with pytest.raises(Exception, match="unknown"):
            subtask_approval_capabilities.authorize(
                "trusted-session-key", capability, _decision(proposalRevision=4)
            )


def test_breakdown_revision_invalidates_older_preview_lineage(monkeypatch):
    monkeypatch.setattr(subtask_approval_capabilities, "_clock", lambda: NOW)
    approved = server._methods["flowstate.approval.register"](
        "r1", {"session_id": "ui-session", "decision": _decision()}
    )
    token = approved["result"]["approvalCapability"]
    revision = {
        "type": "task-breakdown-revision",
        "schemaVersion": 1,
        "action": "revise",
        "approval": False,
        "proposalId": "proposal-1",
        "proposalRevision": 3,
        "scope": "working-session",
        "targetOutcome": "A reviewable draft",
        "stoppingRule": "Stop at a reviewable draft.",
        "task": {"id": "task-1", "title": "Prepare launch", "baseRevision": 7},
        "steps": [{
            "clientId": "step-1",
            "title": "Draft",
            "doneEnough": "Reviewable",
            "optional": False,
        }],
    }

    response = server._methods["flowstate.approval.register"](
        "r2", {"session_id": "ui-session", "decision": revision}
    )
    assert response["result"] == {"revoked": 1, "status": "revised"}
    with pytest.raises(Exception, match="unknown"):
        subtask_approval_capabilities.authorize("trusted-session-key", token, _decision())


def test_gateway_keeps_client_and_subtask_identity_namespaces_distinct(monkeypatch):
    monkeypatch.setattr(subtask_approval_capabilities, "_clock", lambda: NOW)
    operations = [
        {"kind": "create", "clientId": "same-id", "title": "Draft"},
        {"kind": "update", "subtaskId": "same-id", "title": "Review"},
    ]
    response = server._methods["flowstate.approval.register"](
        "r1",
        {"session_id": "ui-session", "decision": _decision(operations=operations)},
    )
    assert isinstance(response["result"]["approvalCapability"], str)


def test_minimal_exact_ui_breakdown_revision_is_accepted(monkeypatch):
    monkeypatch.setattr(subtask_approval_capabilities, "_clock", lambda: NOW)
    revision = {
        "type": "task-breakdown-revision",
        "schemaVersion": 1,
        "action": "revise",
        "approval": False,
        "proposalId": "proposal-1",
        "proposalRevision": 3,
        "scope": "next-move",
        "task": {"id": "task-1", "title": "Prepare launch", "baseRevision": 7},
        "steps": [{
            "clientId": "step-1",
            "title": "Draft",
            "doneEnough": "Reviewable",
            "optional": False,
        }],
    }
    response = server._methods["flowstate.approval.register"](
        "r1", {"session_id": "ui-session", "decision": revision}
    )
    assert response["result"] == {"revoked": 0, "status": "revised"}
