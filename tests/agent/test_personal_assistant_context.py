from agent.personal_assistant_context import build_personal_assistant_runtime_context


def test_context_describes_only_registered_capabilities_in_user_vocabulary():
    context = build_personal_assistant_runtime_context(
        registered_tool_names={
            "flowstate_get_assistant_context",
            "flowstate_list_tasks",
            "flowstate_create_task",
            "flowstate_list_subtasks",
            "flowstate_subtask_batch",
            "flowstate_control_timer",
        },
        flowstate_available=True,
        assistant_context_result={"taskPressure": {"overdue": 2}},
    )

    assert context["source"] == "FlowState"
    assert context["availability"] == "available"
    assert context["capabilities"] == {
        "understand what needs attention": ["read assistant context", "list tasks"],
        "organize my tasks": ["create tasks"],
        "break large tasks into steps": ["read subtasks", "preview or atomically apply an approved ordered subtask plan"],
        "control my focus session": ["preview or apply an approved timer start, pause, resume, or stop"],
    }
    assert context["live_context"] == {"taskPressure": {"overdue": 2}}


def test_unavailable_context_fails_safe_without_echoing_error_details():
    context = build_personal_assistant_runtime_context(
        registered_tool_names={"flowstate_list_tasks"},
        flowstate_available=False,
        assistant_context_result={"error": "Bearer secret-token connection failed"},
    )

    assert context["availability"] == "unavailable"
    assert context["live_context"] == {}
    assert "secret-token" not in str(context)


def test_live_context_redacts_credentials_at_any_depth():
    context = build_personal_assistant_runtime_context(
        registered_tool_names={"flowstate_get_assistant_context"},
        flowstate_available=True,
        assistant_context_result={
            "taskPressure": {"overdue": 1},
            "apiToken": "top-secret",
            "projectSignals": [{"name": "Launch", "authorization": "Bearer abc123"}],
        },
    )

    assert context["live_context"] == {
        "taskPressure": {"overdue": 1},
        "projectSignals": [{"name": "Launch"}],
    }
    assert "top-secret" not in str(context)
    assert "abc123" not in str(context)
