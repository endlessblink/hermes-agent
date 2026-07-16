from datetime import datetime, timedelta, timezone

import pytest

from agent.notion_approval_capabilities import notion_approval_capabilities
from tui_gateway import server


NOW = datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)


def _decision(decision="approve", **overrides):
    expires = (NOW + timedelta(minutes=5)).isoformat()
    value = {
        "type": "notion-mutation-decision",
        "schemaVersion": 1,
        "contractVersion": "notion-bridge-v1",
        "decision": decision,
        "approval": decision == "approve",
        "tool": "notion_mutation",
        "apply": {
            "mode": "apply",
            "operation_id": "notion-op-1",
            "action": "archive_task",
            "data_source_id": "source-1",
            "page_id": "page-1",
            "preview_digest": "sha256:" + "a" * 64,
            "preview_expires_at": expires,
        },
        "previewExpiresAt": expires,
    }
    value.update(overrides)
    return value


@pytest.fixture(autouse=True)
def session():
    server._sessions["notion-ui-session"] = {"session_key": "notion-trusted-session"}
    notion_approval_capabilities.revoke_session("notion-trusted-session")
    yield
    notion_approval_capabilities.revoke_session("notion-trusted-session")
    server._sessions.pop("notion-ui-session", None)


def test_gateway_registers_exact_notion_approval(monkeypatch):
    monkeypatch.setattr(notion_approval_capabilities, "_clock", lambda: NOW)
    response = server._methods["notion.approval.register"](
        "r1", {"session_id": "notion-ui-session", "decision": _decision()}
    )
    assert response["result"] == {"status": "approved"}
    decision = _decision()
    notion_approval_capabilities.authorize_registered(
        "notion-trusted-session",
        {
            "tool": decision["tool"],
            "apply": decision["apply"],
            "previewExpiresAt": decision["previewExpiresAt"],
        },
    )


def test_gateway_registers_exact_notion_activation_with_work_block(monkeypatch):
    monkeypatch.setattr(notion_approval_capabilities, "_clock", lambda: NOW)
    expires = (NOW + timedelta(minutes=5)).isoformat()
    apply = {
        "mode": "apply",
        "operation_id": "activate-1",
        "data_source_id": "source-1",
        "page_id": "page-1",
        "task": {"title": "Prepare proposal"},
        "work_block": {
            "scheduledDate": "2026-07-16",
            "scheduledTime": "10:00",
            "duration": 25,
        },
        "preview_digest": "sha256:" + "b" * 64,
        "preview_expires_at": expires,
    }
    decision = _decision(
        tool="notion_flowstate_activate",
        apply=apply,
        previewExpiresAt=expires,
    )
    response = server._methods["notion.approval.register"](
        "r1", {"session_id": "notion-ui-session", "decision": decision}
    )
    assert response["result"] == {"status": "approved"}
    notion_approval_capabilities.authorize_registered(
        "notion-trusted-session",
        {"tool": decision["tool"], "apply": apply, "previewExpiresAt": expires},
    )

    tampered = {**apply, "work_block": {**apply["work_block"], "duration": 60}}
    with pytest.raises(Exception, match="required"):
        notion_approval_capabilities.authorize_registered(
            "notion-trusted-session",
            {"tool": decision["tool"], "apply": tampered, "previewExpiresAt": expires},
        )


@pytest.mark.parametrize(
    "decision",
    [
        _decision(approval=False),
        _decision(decision="maybe"),
        _decision(contractVersion="wrong"),
        _decision(tool="notion_flowstate_activate"),
        _decision(extra="not allowed"),
        _decision(apply={"mode": "preview"}),
    ],
)
def test_gateway_rejects_invalid_or_cross_tool_notion_decisions(decision):
    response = server._methods["notion.approval.register"](
        "r1", {"session_id": "notion-ui-session", "decision": decision}
    )
    assert response["error"]["code"] == 4002


def test_revision_revokes_matching_notion_capability(monkeypatch):
    monkeypatch.setattr(notion_approval_capabilities, "_clock", lambda: NOW)
    approved = server._methods["notion.approval.register"](
        "r1", {"session_id": "notion-ui-session", "decision": _decision()}
    )
    assert approved["result"] == {"status": "approved"}
    revised = server._methods["notion.approval.register"](
        "r2",
        {
            "session_id": "notion-ui-session",
            "decision": _decision("revise", correction="Keep the task open"),
        },
    )
    assert revised["result"] == {"revoked": 1, "status": "revised"}
    with pytest.raises(Exception, match="required"):
        decision = _decision()
        notion_approval_capabilities.authorize_registered(
            "notion-trusted-session",
            {
                "tool": decision["tool"],
                "apply": decision["apply"],
                "previewExpiresAt": decision["previewExpiresAt"],
            },
        )


def test_revision_without_correction_is_a_valid_request_for_fresh_preview(monkeypatch):
    monkeypatch.setattr(notion_approval_capabilities, "_clock", lambda: NOW)
    response = server._methods["notion.approval.register"](
        "r1",
        {
            "session_id": "notion-ui-session",
            "decision": _decision("revise", correction=""),
        },
    )
    assert response["result"] == {"revoked": 0, "status": "revised"}


def test_session_teardown_revokes_notion_capability(monkeypatch):
    monkeypatch.setattr(notion_approval_capabilities, "_clock", lambda: NOW)
    approved = server._methods["notion.approval.register"](
        "r1", {"session_id": "notion-ui-session", "decision": _decision()}
    )
    assert approved["result"] == {"status": "approved"}
    monkeypatch.setattr(server, "_finalize_session", lambda *_args, **_kwargs: None)
    server._teardown_session(server._sessions["notion-ui-session"])
    decision = _decision()
    with pytest.raises(Exception, match="required"):
        notion_approval_capabilities.authorize_registered(
            "notion-trusted-session",
            {
                "tool": decision["tool"],
                "apply": decision["apply"],
                "previewExpiresAt": decision["previewExpiresAt"],
            },
        )
