"""Regression coverage for exact-time FlowState task completion."""

import json

from agent.flowstate_timed_task_stop import build_flowstate_timed_task_stop_nudge


TOOLS = ["flowstate_create_task", "flowstate_create_work_block"]
USER = "Approve both actions and schedule the new Thursday task at 10:00."


def _call(name: str, call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
        ],
    }


def _result(name: str, call_id: str, payload: dict) -> dict:
    return {
        "role": "tool",
        "name": name,
        "tool_call_id": call_id,
        "content": json.dumps({"result": payload}),
    }


def test_timed_task_stop_guard_requires_ordered_apply_and_read_back():
    messages = [
        {"role": "user", "content": USER},
        _call("flowstate_create_task", "create"),
        _result(
            "flowstate_create_task",
            "create",
            {"ok": True, "status": "committed", "task": {"id": "task-1"}},
        ),
    ]

    nudge = build_flowstate_timed_task_stop_nudge(
        user_message=USER,
        messages=messages,
        valid_tool_names=TOOLS,
        attempts=0,
    )
    assert nudge is not None
    assert "flowstate_create_work_block" in nudge

    messages.extend(
        [
            _call("flowstate_create_work_block", "preview"),
            _result(
                "flowstate_create_work_block",
                "preview",
                {"ok": True, "status": "preview", "previewDigest": "digest"},
            ),
        ]
    )
    assert build_flowstate_timed_task_stop_nudge(
        user_message=USER,
        messages=messages,
        valid_tool_names=TOOLS,
        attempts=1,
    ) is not None

    messages.extend(
        [
            _call("flowstate_create_work_block", "apply"),
            _result(
                "flowstate_create_work_block",
                "apply",
                {
                    "ok": True,
                    "status": "committed",
                    "receipt": {
                        "status": "committed",
                        "entityId": "task-1",
                        "workBlockId": "block-1",
                        "readBack": {
                            "workBlock": {
                                "id": "block-1",
                                "taskId": "task-1",
                                "scheduledDate": "2026-07-23",
                                "scheduledTime": "10:00",
                                "duration": 15,
                                "timezone": "Asia/Jerusalem",
                            }
                        },
                    },
                },
            ),
        ]
    )
    assert build_flowstate_timed_task_stop_nudge(
        user_message=USER,
        messages=messages,
        valid_tool_names=TOOLS,
        attempts=2,
    ) is not None

    messages.extend(
        [
            _call("flowstate_get_task", "task-read"),
            _result(
                "flowstate_get_task",
                "task-read",
                {
                    "ok": True,
                    "task": {
                        "id": "task-1",
                        "canonicalRevision": 2,
                        "instances": [
                            {
                                "id": "block-1",
                                "taskId": "task-1",
                                "scheduledDate": "2026-07-23",
                                "scheduledTime": "10:00",
                                "duration": 15,
                                "timezone": "Asia/Jerusalem",
                            }
                        ],
                    },
                },
            ),
        ]
    )
    assert build_flowstate_timed_task_stop_nudge(
        user_message=USER,
        messages=messages,
        valid_tool_names=TOOLS,
        attempts=3,
    ) is not None

    messages.extend(
        [
            _call("flowstate_list_task_instances", "instance-read"),
            _result(
                "flowstate_list_task_instances",
                "instance-read",
                {
                    "ok": True,
                    "instances": [
                        {
                            "id": "block-1",
                            "taskId": "task-1",
                            "scheduledDate": "2026-07-23",
                            "scheduledTime": "10:00",
                            "duration": 15,
                            "timezone": "Asia/Jerusalem",
                        }
                    ],
                },
            ),
        ]
    )
    assert build_flowstate_timed_task_stop_nudge(
        user_message=USER,
        messages=messages,
        valid_tool_names=TOOLS,
        attempts=4,
    ) is None


def test_timed_task_stop_guard_requires_both_live_routes():
    messages = [
        _result(
            "flowstate_create_task",
            "create",
            {"ok": True, "status": "committed", "task": {"id": "task-1"}},
        )
    ]

    assert build_flowstate_timed_task_stop_nudge(
        user_message=USER,
        messages=messages,
        valid_tool_names=["flowstate_create_task"],
    ) is None
    assert build_flowstate_timed_task_stop_nudge(
        user_message=USER,
        messages=messages,
        valid_tool_names=["flowstate_create_work_block"],
    ) is None


def test_timed_task_stop_guard_rejects_apply_without_preview_receipt():
    messages = [
        {"role": "user", "content": USER},
        _result(
            "flowstate_create_task",
            "create",
            {"ok": True, "status": "committed", "task": {"id": "task-1"}},
        ),
        _result(
            "flowstate_create_work_block",
            "apply",
            {"ok": True, "status": "committed"},
        ),
        _result(
            "flowstate_get_task",
            "task-read",
            {"ok": True, "task": {"id": "task-1"}},
        ),
        _result(
            "flowstate_list_task_instances",
            "instance-read",
            {"ok": True, "instances": [{"id": "block-1"}]},
        ),
    ]

    assert build_flowstate_timed_task_stop_nudge(
        user_message=USER,
        messages=messages,
        valid_tool_names=TOOLS,
    ) is not None
