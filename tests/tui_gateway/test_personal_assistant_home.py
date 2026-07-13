from types import SimpleNamespace


def _stub_context(monkeypatch, server):
    monkeypatch.setattr(server, "_personal_assistant_runtime_context", lambda: {})
    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")


def test_starts_append_episodes_to_one_canonical_session(monkeypatch, tmp_path):
    import tui_gateway.server as server

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    creates = []
    resumes = []
    submits = []
    monkeypatch.setitem(
        server._methods,
        "session.create",
        lambda rid, params: creates.append(params)
        or server._ok(
            rid,
            {"session_id": "live-created-1", "stored_session_id": "canonical-1"},
        ),
    )
    monkeypatch.setitem(
        server._methods,
        "session.resume",
        lambda rid, params: resumes.append(params)
        or server._ok(rid, {"session_id": "live-1", "resumed": "canonical-1"}),
    )
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submits.append(params)
        or server._ok(rid, {"status": "streaming"}),
    )

    first = server._methods["personal_assistant.start"](
        "r1", {"trigger": "manual", "userIntent": "plan launch"}
    )
    second = server._methods["personal_assistant.start"](
        "r2", {"trigger": "review", "userIntent": "review launch"}
    )

    assert first["result"]["canonical_session_id"] == "canonical-1"
    assert second["result"]["canonical_session_id"] == "canonical-1"
    assert len(creates) == 1
    assert resumes[0]["session_id"] == "canonical-1"
    assert [item["session_id"] for item in submits] == ["live-created-1", "live-1"]
    state = server._methods["personal_assistant.state.get"]("r3", {})["result"]["state"]
    assert [item["trigger"] for item in state["episodes"]] == ["manual", "review"]


def test_personal_assistant_prompt_sets_a_fast_foreground_contract():
    import tui_gateway.server as server

    prompt = server._personal_assistant_prompt("manual", "plan today", {})

    assert "one focused batch" in prompt
    assert "two foreground tool batches" in prompt
    assert "Do not search code, files, skills, or prior sessions" in prompt
    assert "Return a useful visible response" in prompt


def test_personal_assistant_runtime_policy_caps_only_its_agent():
    import tui_gateway.server as server

    agent = SimpleNamespace(max_iterations=60, ephemeral_system_prompt="Existing profile guidance")
    session = {"agent": agent}
    ordinary = SimpleNamespace(max_iterations=60, ephemeral_system_prompt="Ordinary guidance")
    with server._sessions_lock:
        server._sessions["policy-assistant"] = session
        server._sessions["policy-ordinary"] = {"agent": ordinary}
    try:
        server._apply_personal_assistant_runtime_policy_for_session("policy-assistant")
        server._apply_personal_assistant_runtime_policy_for_session("policy-assistant")
    finally:
        with server._sessions_lock:
            server._sessions.pop("policy-assistant", None)
            server._sessions.pop("policy-ordinary", None)

    assert session["personal_assistant"] is True
    assert agent.max_iterations == server._PERSONAL_ASSISTANT_MAX_ITERATIONS
    assert agent._foreground_tool_batch_limit == 2
    assert agent._tool_result_budget_override.default_result_size == 20_000
    assert agent._tool_result_budget_override.turn_budget == 40_000
    assert agent.ephemeral_system_prompt.count(server._PERSONAL_ASSISTANT_RESPONSIVENESS_POLICY) == 1

    assert ordinary.max_iterations == 60
    assert not hasattr(ordinary, "_tool_result_budget_override")


def test_start_applies_runtime_policy_before_submitting(monkeypatch, tmp_path):
    import tui_gateway.server as server

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    monkeypatch.setitem(
        server._methods,
        "session.create",
        lambda rid, params: server._ok(
            rid, {"session_id": "assistant-live", "stored_session_id": "assistant-home"}
        ),
    )
    applied = []
    submitted = []
    monkeypatch.setattr(
        server,
        "_apply_personal_assistant_runtime_policy_for_session",
        lambda sid: applied.append(sid),
    )
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submitted.append(params) or server._ok(rid, {"status": "streaming"}),
    )

    response = server._methods["personal_assistant.start"](
        "r1", {"trigger": "manual", "userIntent": "plan today"}
    )

    assert response["result"]["status"] == "launched"
    assert applied == ["assistant-live"]
    assert submitted[0]["session_id"] == "assistant-live"


def test_stale_canonical_is_recreated_once_without_forgetting_state(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    store = PersonalAssistantStateStore(tmp_path)
    store.patch("edit", {"outcomes": [{"id": "important", "title": "important work"}]})
    store.set_canonical_session("stale")
    creates = []
    submits = []
    monkeypatch.setitem(
        server._methods,
        "session.resume",
        lambda rid, params: server._err(rid, 4007, "session not found"),
    )
    monkeypatch.setitem(
        server._methods,
        "session.create",
        lambda rid, params: creates.append(params)
        or server._ok(
            rid,
            {"session_id": "replacement-live", "stored_session_id": "replacement"},
        ),
    )
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submits.append(params)
        or server._ok(rid, {"status": "streaming"}),
    )

    response = server._methods["personal_assistant.start"]("r1", {"trigger": "contextual"})

    assert response["result"]["canonical_session_id"] == "replacement"
    assert len(creates) == 1
    assert "important work" in submits[0]["text"]
    assert store.read()["outcomes"][0]["title"] == "important work"


def test_home_returns_live_and_canonical_session_ids(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    PersonalAssistantStateStore(tmp_path).set_canonical_session("assistant-home")
    monkeypatch.setitem(
        server._methods,
        "session.resume",
        lambda rid, params: server._ok(rid, {"session_id": "assistant-live"}),
    )
    applied = []
    monkeypatch.setattr(
        server,
        "_apply_personal_assistant_runtime_policy_for_session",
        lambda sid: applied.append(sid),
    )

    response = server._methods["personal_assistant.home"]("r1", {})

    assert response["result"]["session_id"] == "assistant-live"
    assert response["result"]["canonical_session_id"] == "assistant-home"
    assert response["result"]["state"]["sessionId"] == "assistant-home"
    assert applied == ["assistant-live"]


def test_home_clears_unread_activity_persistently(monkeypatch, tmp_path):
    import tui_gateway.server as server
    from agent.personal_assistant_state import PersonalAssistantStateStore

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    store = PersonalAssistantStateStore(tmp_path)
    store.set_canonical_session("assistant-home")
    store.patch("edit", {"unreadCount": 5})
    monkeypatch.setitem(
        server._methods,
        "session.resume",
        lambda rid, params: server._ok(rid, {"session_id": "assistant-live"}),
    )

    response = server._methods["personal_assistant.home"]("r1", {})

    assert response["result"]["state"]["unreadCount"] == 0
    assert store.public()["unreadCount"] == 0


def test_start_idempotency_does_not_submit_twice(monkeypatch, tmp_path):
    import tui_gateway.server as server

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    monkeypatch.setitem(
        server._methods, "session.create", lambda rid, params: server._ok(rid, {"session_id": "one"})
    )
    monkeypatch.setitem(
        server._methods, "session.resume", lambda rid, params: server._ok(rid, {"session_id": "one"})
    )
    submits = []
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: submits.append(params) or server._ok(rid, {}),
    )

    first = server._methods["personal_assistant.start"](
        "r1", {"trigger": "manual", "idempotencyKey": "same"}
    )
    second = server._methods["personal_assistant.start"](
        "r2", {"trigger": "manual", "idempotencyKey": "same"}
    )

    assert first["result"]["status"] == "launched"
    assert second["result"]["status"] == "already_submitted"
    assert len(submits) == 1


def test_state_rpc_uses_camel_case_contract_and_returns_conflict_snapshot(monkeypatch, tmp_path):
    import tui_gateway.server as server

    _stub_context(monkeypatch, server)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    initial = server._methods["personal_assistant.state.get"]("r1", {})["result"]["state"]
    changed = server._methods["personal_assistant.state.patch"](
        "r2",
        {
            "expectedVersion": initial["version"],
            "operations": [
                {"op": "upsert", "section": "outcomes", "id": "o1", "value": {"title": "Ship"}},
                {"op": "set", "section": "capacity", "value": {"summary": "4h", "updatedAt": "now"}},
            ],
        },
    )["result"]["state"]
    assert changed["schemaVersion"] == 1
    assert changed["outcomes"][0]["id"] == "o1"
    assert changed["capacity"]["summary"] == "4h"

    conflict = server._methods["personal_assistant.state.patch"](
        "r3", {"expectedVersion": initial["version"], "operations": [{"op": "forget", "section": "outcomes", "id": "o1"}]}
    )
    assert conflict["error"]["code"] == 4091
    assert conflict["error"]["data"]["state"]["version"] == changed["version"]
