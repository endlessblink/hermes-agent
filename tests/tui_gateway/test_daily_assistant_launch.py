from types import SimpleNamespace


def _install_session_stubs(monkeypatch, server, session_ids):
    submitted = []
    ids = iter(session_ids)
    monkeypatch.setitem(
        server._methods,
        "session.create",
        lambda rid, params: server._ok(rid, {"session_id": next(ids)}),
    )
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submitted.append(params)
        or server._ok(rid, {"status": "streaming"}),
    )
    return submitted


def test_personal_assistant_manual_starts_anytime_and_preserves_intent(monkeypatch):
    import tui_gateway.server as server

    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    submitted = _install_session_stubs(monkeypatch, server, ["pa-1", "pa-2"])

    first = server._methods["personal_assistant.start"](
        "r1", {"trigger": "manual", "userIntent": "Untangle the launch decision"}
    )
    second = server._methods["personal_assistant.start"](
        "r2", {"trigger": "manual", "userIntent": "Review what I promised people"}
    )

    assert first["result"]["session_id"] == "pa-1"
    assert second["result"]["session_id"] == "pa-2"
    assert "Untangle the launch decision" in submitted[0]["text"]
    assert "Review what I promised people" in submitted[1]["text"]
    assert "live FlowState" in submitted[0]["text"]
    assert '"capabilities"' in submitted[0]["text"]
    assert "list tasks" in submitted[0]["text"]
    assert "one focused question" not in submitted[0]["text"]
    assert "fixed morning" in submitted[0]["text"]


def test_personal_assistant_accepts_explicit_owner_from_another_launch_profile(monkeypatch, tmp_path):
    import tui_gateway.server as server

    monkeypatch.setattr(server, "_current_profile_name", lambda: "default")
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    submitted = _install_session_stubs(monkeypatch, server, ["pa-cross-profile"])

    response = server._methods["personal_assistant.start"](
        "r1", {"trigger": "manual", "profile": "office-work"}
    )

    assert response["result"]["session_id"] == "pa-cross-profile"
    assert submitted[0]["session_id"] == "pa-cross-profile"


def test_personal_assistant_rejects_non_owner_profile(monkeypatch):
    import tui_gateway.server as server

    monkeypatch.setattr(server, "_current_profile_name", lambda: "default")

    response = server._methods["personal_assistant.start"](
        "r1", {"trigger": "manual", "profile": "film-maker"}
    )

    assert response["error"]["code"] == 4000
    assert "office-work" in response["error"]["message"]


def test_personal_assistant_scheduled_uses_daily_claim(monkeypatch, tmp_path):
    import tui_gateway.server as server

    due = SimpleNamespace(claimed=True, status="due", local_date="2026-07-12")
    already = SimpleNamespace(claimed=False, status="already_completed", local_date="2026-07-12")
    claims = iter([due, already])
    completed = []
    monkeypatch.setattr(
        "agent.daily_assistant_lifecycle.claim_daily_planning_trigger",
        lambda *args, **kwargs: next(claims),
    )
    monkeypatch.setattr(
        "agent.daily_assistant_lifecycle.complete_daily_planning_trigger",
        lambda home, claim: completed.append(claim) or True,
    )
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    submitted = _install_session_stubs(monkeypatch, server, ["pa-1"])

    first = server._methods["personal_assistant.start"]("r1", {"trigger": "scheduled"})
    second = server._methods["personal_assistant.start"]("r2", {"trigger": "scheduled"})

    assert first["result"]["status"] == "launched"
    assert second["result"]["status"] == "already_completed"
    assert len(submitted) == 1
    assert completed == [due]


def test_daily_assistant_launch_submits_then_completes(monkeypatch, tmp_path):
    import tui_gateway.server as server

    claim = SimpleNamespace(claimed=True, status="due", local_date="2026-07-12")
    completed = []
    monkeypatch.setattr(
        "agent.daily_assistant_lifecycle.claim_daily_planning_trigger",
        lambda *args, **kwargs: claim,
    )
    monkeypatch.setattr(
        "agent.daily_assistant_lifecycle.complete_daily_planning_trigger",
        lambda home, value: completed.append((home, value)) or True,
    )
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    monkeypatch.setitem(
        server._methods,
        "session.create",
        lambda rid, params: server._ok(rid, {"session_id": "morning-1"}),
    )
    submitted = []
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submitted.append(params) or server._ok(rid, {"status": "streaming"}),
    )

    response = server._methods["daily_assistant.launch"]("r1", {})

    assert response["result"]["status"] == "launched"
    assert submitted[0]["session_id"] == "morning-1"
    assert "FlowState" in submitted[0]["text"]
    assert completed == [(tmp_path, claim)]


def test_daily_assistant_launch_abandons_failed_submission(monkeypatch, tmp_path):
    import tui_gateway.server as server

    claim = SimpleNamespace(claimed=True, status="due", local_date="2026-07-12")
    abandoned = []
    monkeypatch.setattr(
        "agent.daily_assistant_lifecycle.claim_daily_planning_trigger",
        lambda *args, **kwargs: claim,
    )
    monkeypatch.setattr(
        "agent.daily_assistant_lifecycle.abandon_daily_planning_trigger",
        lambda home, value: abandoned.append((home, value)) or True,
    )
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    monkeypatch.setitem(
        server._methods,
        "session.create",
        lambda rid, params: server._ok(rid, {"session_id": "morning-1"}),
    )
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: server._err(rid, 5000, "submit failed"),
    )

    response = server._methods["daily_assistant.launch"]("r2", {})

    assert response["error"]["message"] == "submit failed"
    assert abandoned == [(tmp_path, claim)]
