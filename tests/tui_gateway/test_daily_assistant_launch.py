from types import SimpleNamespace


def _day_plan(task_id="task-alpha", title="Alpha"):
    return (
        '```hermes-ui\n{"type":"day-timeline","date":"2026-07-12",'
        f'"blocks":[{{"id":"block-alpha","label":"{title}","taskId":"{task_id}"}}]}}\n```'
    )


def _record_complete_daily_coverage(profile_home):
    from agent.personal_assistant_state import PersonalAssistantStateStore

    return PersonalAssistantStateStore(profile_home).record_coverage_receipt(
        cadence="daily",
        scope_fingerprint="complete-daily-scope",
        sources=[{"id": "flowstate", "status": "fresh", "revision": "sequence-1"}],
        expected_item_ids=[],
        reviewed_item_ids=[],
        risk_item_ids=[],
        unresolved_item_ids=[],
    )


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
    session = {"personal_assistant": True}
    monkeypatch.setitem(server._sessions, "pa-1", session)

    first = server._methods["personal_assistant.start"]("r1", {"trigger": "scheduled"})
    second = server._methods["personal_assistant.start"]("r2", {"trigger": "scheduled"})

    assert first["result"]["status"] == "launched"
    assert second["result"]["status"] == "already_completed"
    assert len(submitted) == 1
    assert completed == []

    _record_complete_daily_coverage(tmp_path)
    server._finish_personal_assistant_daily_delivery(
        session,
        status="complete",
        has_visible_response=True,
        response=_day_plan(),
    )
    assert completed == [due]


def test_scheduled_planning_carries_arbitrary_configured_task_sources(monkeypatch, tmp_path):
    import tui_gateway.server as server

    tmp_path.joinpath("config.yaml").write_text(
        "personal_assistant:\n"
        "  task_sources:\n"
        "    - id: alpha\n"
        "      inventory_tool: alpha_inventory\n"
        "    - id: beta\n"
        "      inventory_tool: beta_inventory\n",
        encoding="utf-8",
    )
    claim = SimpleNamespace(claimed=True, status="due", local_date="2026-07-12")
    monkeypatch.setattr(
        "agent.daily_assistant_lifecycle.claim_daily_planning_trigger",
        lambda *args, **kwargs: claim,
    )
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(server, "_profile_home", lambda profile: tmp_path)
    monkeypatch.setattr(server, "_current_profile_name", lambda: "office-work")
    submitted = _install_session_stubs(monkeypatch, server, ["configured-sources"])
    session = {"personal_assistant": True}
    monkeypatch.setitem(server._sessions, "configured-sources", session)

    response = server._methods["daily_assistant.launch"]("r1", {})

    assert response["result"]["status"] == "launched"
    assert '"id": "alpha"' in submitted[0]["text"]
    assert '"inventoryTool": "beta_inventory"' in submitted[0]["text"]
    assert session["personal_assistant_daily_delivery"]["expected_task_source_ids"] == [
        "alpha",
        "beta",
    ]
    from agent.personal_assistant_state import PersonalAssistantStateStore

    assert PersonalAssistantStateStore(tmp_path).read()["task_source_manifest"] == [
        {"id": "alpha", "inventoryTool": "alpha_inventory", "available": False},
        {"id": "beta", "inventoryTool": "beta_inventory", "available": False},
    ]


def test_daily_assistant_launch_completes_only_after_a_visible_persisted_response(monkeypatch, tmp_path):
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
    session = {"personal_assistant": True}
    monkeypatch.setitem(server._sessions, "morning-1", session)
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
    assert completed == []

    _record_complete_daily_coverage(tmp_path)
    server._finish_personal_assistant_daily_delivery(
        session,
        status="complete",
        has_visible_response=True,
        response=_day_plan(),
    )

    assert completed == [(tmp_path, claim)]
    from agent.personal_assistant_state import PersonalAssistantStateStore

    assert PersonalAssistantStateStore(tmp_path).read()["episode_summaries"][0]["status"] == "completed"


def test_daily_assistant_visible_interview_question_releases_claim_for_retry(monkeypatch, tmp_path):
    import tui_gateway.server as server

    claim = SimpleNamespace(claimed=True, status="due", local_date="2026-07-12")
    completed = []
    abandoned = []
    monkeypatch.setattr(
        "agent.daily_assistant_lifecycle.complete_daily_planning_trigger",
        lambda home, value: completed.append((home, value)) or True,
    )
    monkeypatch.setattr(
        "agent.daily_assistant_lifecycle.abandon_daily_planning_trigger",
        lambda home, value: abandoned.append((home, value)) or True,
    )
    session = {
        "personal_assistant": True,
        "personal_assistant_daily_delivery": {
            "profile_home": str(tmp_path),
            "claim": claim,
        },
    }

    server._finish_personal_assistant_daily_delivery(
        session,
        status="complete",
        has_visible_response=True,
        response="Which task should we clarify first?",
    )

    assert completed == []
    assert abandoned == [(tmp_path, claim)]


def test_daily_assistant_plan_without_fresh_coverage_releases_claim_for_retry(monkeypatch, tmp_path):
    import tui_gateway.server as server

    claim = SimpleNamespace(claimed=True, status="due", local_date="2026-07-12")
    completed = []
    abandoned = []
    monkeypatch.setattr(
        "agent.daily_assistant_lifecycle.complete_daily_planning_trigger",
        lambda home, value: completed.append((home, value)) or True,
    )
    monkeypatch.setattr(
        "agent.daily_assistant_lifecycle.abandon_daily_planning_trigger",
        lambda home, value: abandoned.append((home, value)) or True,
    )
    session = {
        "personal_assistant": True,
        "personal_assistant_daily_delivery": {
            "profile_home": str(tmp_path),
            "claim": claim,
            "prior_coverage_receipt_id": None,
        },
    }

    server._finish_personal_assistant_daily_delivery(
        session,
        status="complete",
        has_visible_response=True,
        response=_day_plan(),
    )

    assert completed == []
    assert abandoned == [(tmp_path, claim)]


def test_daily_assistant_plan_missing_a_configured_source_releases_claim_for_retry(
    monkeypatch, tmp_path
):
    import tui_gateway.server as server

    claim = SimpleNamespace(claimed=True, status="due", local_date="2026-07-12")
    completed = []
    abandoned = []
    monkeypatch.setattr(
        "agent.daily_assistant_lifecycle.complete_daily_planning_trigger",
        lambda home, value: completed.append((home, value)) or True,
    )
    monkeypatch.setattr(
        "agent.daily_assistant_lifecycle.abandon_daily_planning_trigger",
        lambda home, value: abandoned.append((home, value)) or True,
    )
    session = {
        "personal_assistant": True,
        "personal_assistant_daily_delivery": {
            "profile_home": str(tmp_path),
            "claim": claim,
            "prior_coverage_receipt_id": None,
            "expected_task_source_ids": ["alpha", "beta"],
        },
    }
    from agent.personal_assistant_state import PersonalAssistantStateStore

    PersonalAssistantStateStore(tmp_path).record_coverage_receipt(
        cadence="daily",
        scope_fingerprint="only-alpha-was-reviewed",
        sources=[{"id": "alpha", "status": "fresh", "revision": "1"}],
        expected_item_ids=[],
        reviewed_item_ids=[],
        risk_item_ids=[],
        unresolved_item_ids=[],
    )

    server._finish_personal_assistant_daily_delivery(
        session,
        status="complete",
        has_visible_response=True,
        response=_day_plan(),
    )

    assert completed == []
    assert abandoned == [(tmp_path, claim)]


def test_daily_assistant_completed_plan_records_recommendations(monkeypatch, tmp_path):
    import tui_gateway.server as server

    claim = SimpleNamespace(claimed=True, status="due", local_date="2026-07-12")
    recorded = []
    monkeypatch.setattr(
        "agent.daily_assistant_lifecycle.complete_daily_planning_trigger",
        lambda home, value: True,
    )
    monkeypatch.setattr(
        "agent.suggestion_gate.record_recommendations",
        lambda state_dir, recommendations: recorded.append((state_dir, recommendations))
        or len(recommendations),
    )
    session = {
        "personal_assistant": True,
        "personal_assistant_daily_delivery": {
            "profile_home": str(tmp_path),
            "claim": claim,
            "prior_coverage_receipt_id": None,
            "expected_task_source_ids": ["flowstate"],
        },
    }
    _record_complete_daily_coverage(tmp_path)

    server._finish_personal_assistant_daily_delivery(
        session,
        status="complete",
        has_visible_response=True,
        response=(
            '```hermes-ui\n{"type":"day-timeline","date":"2026-07-12",'
            '"blocks":[{"id":"a","label":"Alpha","taskId":"task-alpha"}]}\n```'
        ),
    )

    assert recorded == [
        (
            tmp_path / "state" / "personal-assistant",
            [{"taskId": "task-alpha", "title": "Alpha", "surface": "day-timeline"}],
        )
    ]


def test_daily_assistant_failed_turn_releases_claim_for_retry(monkeypatch, tmp_path):
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
        lambda rid, params: server._ok(rid, {"session_id": "morning-failed"}),
    )
    session = {"personal_assistant": True}
    monkeypatch.setitem(server._sessions, "morning-failed", session)
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: server._ok(rid, {"status": "streaming"}),
    )

    response = server._methods["daily_assistant.launch"]("r1", {})
    assert response["result"]["status"] == "launched"

    server._finish_personal_assistant_daily_delivery(
        session,
        status="error",
        has_visible_response=False,
    )

    assert abandoned == [(tmp_path, claim)]
    from agent.personal_assistant_state import PersonalAssistantStateStore

    assert PersonalAssistantStateStore(tmp_path).read()["episode_summaries"][0]["status"] == "failed"


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
    monkeypatch.setitem(server._sessions, "morning-1", {"personal_assistant": True})
    monkeypatch.setitem(
        server._methods,
        "prompt.submit",
        lambda rid, params: server._err(rid, 5000, "submit failed"),
    )

    response = server._methods["daily_assistant.launch"]("r2", {})

    assert response["error"]["message"] == "submit failed"
    assert abandoned == [(tmp_path, claim)]
