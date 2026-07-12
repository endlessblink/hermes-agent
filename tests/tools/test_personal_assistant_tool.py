import json


def test_propose_capture_is_deduplicated_and_visible_in_assistant_state(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    args = {
        "section": "commitments",
        "title": "Send the proposal",
        "evidence": "I promised to send it tomorrow.",
        "sourceSessionId": "chat-1",
    }

    first = json.loads(pat._handle_propose_capture(args))
    second = json.loads(pat._handle_propose_capture(args))
    state = json.loads(pat._handle_get_state({}))

    assert first["result"]["proposal"]["id"] == second["result"]["proposal"]["id"]
    assert first["result"]["stateVersion"] == second["result"]["stateVersion"]
    assert len(state["result"]["state"]["captureProposals"]) == 1
    assert state["result"]["state"]["captureProposals"][0]["status"] == "pending"


def test_repeated_capture_does_not_reset_a_reviewed_proposal(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    args = {
        "section": "commitments",
        "title": "Send the proposal",
        "evidence": "I promised to send it tomorrow.",
        "sourceSessionId": "chat-1",
    }
    proposal = json.loads(pat._handle_propose_capture(args))["result"]["proposal"]
    pat._handle_state_change(
        {
            "operations": [
                {
                    "op": "upsert",
                    "section": "captureProposals",
                    "id": proposal["id"],
                    "value": {"status": "accepted"},
                }
            ],
            "preview": False,
            "requestId": "accept-capture",
        }
    )

    repeated = json.loads(pat._handle_propose_capture(args))

    assert repeated["result"]["proposal"]["status"] == "accepted"


def test_idempotency_key_is_bound_to_the_approved_operations(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    base = {
        "operations": [
            {
                "op": "upsert",
                "section": "outcomes",
                "id": "outcome-1",
                "value": {"title": "First"},
            }
        ],
        "preview": False,
        "requestId": "approved-change",
    }
    assert "result" in json.loads(pat._handle_state_change(base))

    conflicting = json.loads(
        pat._handle_state_change(
            {
                **base,
                "operations": [
                    {
                        "op": "upsert",
                        "section": "outcomes",
                        "id": "outcome-1",
                        "value": {"title": "Different"},
                    }
                ],
            }
        )
    )

    assert "already used for different operations" in conflicting["error"]


def test_state_change_previews_by_default_and_apply_requires_request_id(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    operation = {
        "op": "upsert",
        "section": "outcomes",
        "id": "outcome-1",
        "value": {"title": "Ship the proposal", "status": "active"},
    }

    preview = json.loads(pat._handle_state_change({"operations": [operation]}))
    rejected = json.loads(
        pat._handle_state_change({"operations": [operation], "preview": False})
    )
    state = json.loads(pat._handle_get_state({}))

    assert preview["result"]["preview"] is True
    assert "requestId is required" in rejected["error"]
    assert state["result"]["state"]["outcomes"] == []


def test_state_change_applies_once_with_optimistic_version(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    operation = {
        "op": "upsert",
        "section": "preferences",
        "id": "preference-1",
        "value": {"title": "Keep plans compact"},
    }

    applied = json.loads(
        pat._handle_state_change(
            {
                "expectedVersion": 0,
                "operations": [operation],
                "preview": False,
                "requestId": "approved-change-1",
            }
        )
    )
    replay = json.loads(
        pat._handle_state_change(
            {
                "expectedVersion": 0,
                "operations": [operation],
                "preview": False,
                "requestId": "approved-change-1",
            }
        )
    )

    assert applied["result"]["preview"] is False
    assert applied["result"]["state"]["preferences"][0]["id"] == "preference-1"
    assert replay["result"]["replayed"] is True


def test_tools_fail_closed_outside_office_work(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("default", tmp_path))

    result = json.loads(pat._handle_get_state({}))

    assert "office-work" in result["error"]


def test_personal_assistant_toolset_exposes_state_parity_tools():
    from toolsets import get_toolset

    assert set(get_toolset("personal_assistant")["tools"]) == {
        "personal_assistant_get_state",
        "personal_assistant_propose_capture",
        "personal_assistant_state_change",
    }


def test_tool_registration_is_scoped_to_office_work(monkeypatch, tmp_path):
    import tools.personal_assistant_tool as pat

    monkeypatch.setattr(pat, "_profile_context", lambda: ("default", tmp_path))
    assert pat._check_office_work_profile() is False
    monkeypatch.setattr(pat, "_profile_context", lambda: ("office-work", tmp_path))
    assert pat._check_office_work_profile() is True


def test_personal_assistant_can_be_configured_but_is_off_by_default():
    from hermes_cli.tools_config import CONFIGURABLE_TOOLSETS, _DEFAULT_OFF_TOOLSETS

    assert "personal_assistant" in {entry[0] for entry in CONFIGURABLE_TOOLSETS}
    assert "personal_assistant" in _DEFAULT_OFF_TOOLSETS
