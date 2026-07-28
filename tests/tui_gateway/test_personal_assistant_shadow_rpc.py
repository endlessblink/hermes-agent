from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from agent.personal_assistant_state import PersonalAssistantStateStore
from tui_gateway import server


def test_shadow_submit_refuses_office_work(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    monkeypatch.setattr(
        server,
        "_personal_assistant_store",
        lambda _profile: PersonalAssistantStateStore(tmp_path),
    )

    response = server._methods["personal_assistant.shadow.submit"](
        "r1",
        {
            "profile": "office-work",
            "submissionId": "turn-1",
            "userIntent": "תכנן את היום",
        },
    )

    assert response["error"]["code"] == 4000
    assert PersonalAssistantStateStore(tmp_path).get_active_turn() is None


def test_shadow_submit_binds_the_generated_runtime_session(monkeypatch, tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    runtime = Mock()
    runtime.submit.return_value = {"duplicate": False}
    agent = SimpleNamespace(personal_assistant_mode=False)
    session = {
        "agent": agent,
        "personal_assistant": False,
        "session_key": "acceptance-home",
    }
    monkeypatch.setattr(
        server,
        "_current_profile_name",
        lambda: "personal-assistant-acceptance",
    )
    monkeypatch.setattr(server, "_personal_assistant_store", lambda _profile: store)
    monkeypatch.setattr(
        server,
        "_ensure_personal_assistant_shadow_runtime",
        lambda _store: runtime,
    )
    server._sessions["runtime-1"] = session

    try:
        response = server._methods["personal_assistant.shadow.submit"](
            "r1",
            {
                "eventId": "submit:turn-1",
                "profile": "personal-assistant-acceptance",
                "session_id": "runtime-1",
                "submissionId": "turn-1",
                "userIntent": "תכנן את המשך היום",
            },
        )
    finally:
        server._sessions.pop("runtime-1", None)

    assert "error" not in response
    assert response["result"]["runtime_session_id"] == "runtime-1"
    assert agent.personal_assistant_mode is True
    assert session["personal_assistant_shadow"] is True
    runtime.submit.assert_called_once_with(
        event_id="submit:turn-1",
        durable_session_id="acceptance-home",
        submission_id="turn-1",
        user_intent="תכנן את המשך היום",
        lineage_root_id="acceptance-home",
    )


def test_shadow_home_binds_the_generated_runtime_without_submitting(
    monkeypatch, tmp_path
) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    agent = SimpleNamespace(personal_assistant_mode=False)
    session = {
        "agent": agent,
        "personal_assistant": False,
        "session_key": "acceptance-home",
    }
    monkeypatch.setattr(
        server,
        "_current_profile_name",
        lambda: "personal-assistant-acceptance",
    )
    monkeypatch.setattr(server, "_personal_assistant_store", lambda _profile: store)
    monkeypatch.setattr(
        server,
        "_personal_assistant_shadow_session",
        lambda _rid, _params: (("runtime-1", session), None),
    )

    response = server._methods["personal_assistant.shadow.home"](
        "r1",
        {"profile": "personal-assistant-acceptance"},
    )

    assert "error" not in response
    assert response["result"]["canonical_session_id"] == "acceptance-home"
    assert response["result"]["session_id"] == "runtime-1"
    assert response["result"]["status"] == "ready"
    assert response["result"]["state"]["sessionId"] == "acceptance-home"


def test_shadow_action_uses_the_same_profile_runtime(monkeypatch, tmp_path) -> None:
    store = PersonalAssistantStateStore(tmp_path)
    runtime = Mock()
    runtime.submit_card_action.return_value = {"duplicate": False}
    monkeypatch.setattr(
        server,
        "_current_profile_name",
        lambda: "personal-assistant-acceptance",
    )
    monkeypatch.setattr(server, "_personal_assistant_store", lambda _profile: store)
    monkeypatch.setattr(
        server,
        "_ensure_personal_assistant_shadow_runtime",
        lambda _store: runtime,
    )

    response = server._methods["personal_assistant.shadow.action"](
        "r1",
        {
            "actionId": "answer-progress",
            "cardRevision": 2,
            "eventId": "action:turn-1:2",
            "input": {"progressReview": "שום דבר"},
            "profile": "personal-assistant-acceptance",
        },
    )

    assert "error" not in response
    runtime.submit_card_action.assert_called_once_with(
        event_id="action:turn-1:2",
        action_id="answer-progress",
        card_revision=2,
        input={"progressReview": "שום דבר"},
    )
