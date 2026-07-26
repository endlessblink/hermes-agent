import json
from types import SimpleNamespace

from agent.personal_assistant_state import PersonalAssistantStateStore
from agent.personal_assistant_timer import build_deterministic_timer_response


TASK_ID = "b3ff26a2-59d6-41ce-a6e6-a61942434542"
SESSION_ID = "6022dc9e-16c3-4e47-bf16-5d363bf4f25c"
TASK_TITLE = "להזמין כדורים דרך הקופה"
PREVIEW_DIGEST = "a" * 64
REQUEST_HASH = "b" * 64
PREVIEW_EXPIRES_AT = "2099-07-24T00:30:00+03:00"


class FakeRegistry:
    def __init__(self):
        self.active = False
        self.calls = []

    def dispatch(self, name, args):
        self.calls.append((name, dict(args)))
        if name == "flowstate_list_tasks":
            return json.dumps(
                {
                    "result": {
                        "items": [
                            {
                                "id": TASK_ID,
                                "title": TASK_TITLE,
                                "status": "todo",
                                "priority": "high",
                                "canonicalRevision": 15,
                            }
                        ],
                        "complete": True,
                        "fresh": True,
                    }
                }
            )
        if name == "flowstate_get_task":
            assert args["taskId"] == TASK_ID
            return json.dumps(
                {
                    "result": {
                        "task": {
                            "id": TASK_ID,
                            "title": TASK_TITLE,
                            "status": "todo",
                            "instances": [
                                {
                                    "status": "scheduled",
                                    "duration": 15,
                                    "scheduledDate": "2026-08-01",
                                }
                            ],
                            "canonicalRevision": 15,
                        }
                    }
                }
            )
        if name == "flowstate_get_current_timer":
            return json.dumps(
                {
                    "result": {
                        "active": self.active,
                        "session": (
                            {
                                "id": SESSION_ID,
                                "task_id": TASK_ID,
                                "duration": 900,
                                "is_active": True,
                                "canonical_revision": 1,
                            }
                            if self.active
                            else None
                        ),
                    }
                }
            )
        if name == "flowstate_start_timer":
            if args.get("preview", True):
                return json.dumps(
                    {
                        "result": {
                            "ok": True,
                            "result": "preview",
                            "sessionId": args["sessionId"],
                            "operationId": args["operationId"],
                            "previewDigest": PREVIEW_DIGEST,
                            "requestHash": REQUEST_HASH,
                            "previewExpiresAt": PREVIEW_EXPIRES_AT,
                        }
                    }
                )
            self.active = True
            return json.dumps({"result": {"ok": True, "result": "committed"}})
        if name == "flowstate_stop_timer":
            assert args["sessionId"] == SESSION_ID
            assert args["baseRevision"] == 1
            if args.get("preview", True):
                return json.dumps(
                    {
                        "result": {
                            "ok": True,
                            "result": "preview",
                            "sessionId": SESSION_ID,
                            "operationId": args["operationId"],
                            "baseRevision": 1,
                            "previewDigest": PREVIEW_DIGEST,
                            "requestHash": REQUEST_HASH,
                            "previewExpiresAt": PREVIEW_EXPIRES_AT,
                        }
                    }
                )
            self.active = False
            return json.dumps({"result": {"ok": True, "result": "committed"}})
        raise AssertionError(f"unexpected tool: {name}")


def _agent(tmp_path):
    return SimpleNamespace(
        personal_assistant_mode=True,
        personal_assistant_state_store=PersonalAssistantStateStore(tmp_path),
    )


def test_named_start_previews_then_commits_after_durable_named_approval(
    monkeypatch, tmp_path
):
    registry = FakeRegistry()
    monkeypatch.setattr("agent.personal_assistant_timer.registry", registry)
    agent = _agent(tmp_path)

    preview = build_deterministic_timer_response(
        agent, f"תתחיל את המשימה {TASK_TITLE}."
    )

    assert preview is not None
    assert TASK_TITLE in preview
    assert "15 דקות" in preview
    pending = agent.personal_assistant_state_store.get_pending_timer_action()
    assert pending["taskTitle"] == TASK_TITLE
    assert pending["previewDigest"] == PREVIEW_DIGEST
    assert pending["requestHash"] == REQUEST_HASH
    assert pending["confirmText"] in preview
    assert registry.active is False

    restarted_agent = _agent(tmp_path)
    result = build_deterministic_timer_response(
        restarted_agent, pending["confirmText"]
    )

    assert result == f"התחלתי טיימר של 15 דקות למשימה „{TASK_TITLE}”. הטיימר פעיל עכשיו."
    assert registry.active is True
    assert restarted_agent.personal_assistant_state_store.get_pending_timer_action() is None
    commit = [args for name, args in registry.calls if name == "flowstate_start_timer"][-1]
    assert commit["preview"] is False
    assert commit["previewDigest"] == PREVIEW_DIGEST
    assert commit["requestHash"] == REQUEST_HASH


def test_stop_previews_then_commits_and_verifies_inactive_timer(monkeypatch, tmp_path):
    registry = FakeRegistry()
    registry.active = True
    monkeypatch.setattr("agent.personal_assistant_timer.registry", registry)
    agent = _agent(tmp_path)

    preview = build_deterministic_timer_response(agent, "תעצור את הטיימר הנוכחי.")

    assert preview is not None
    assert TASK_TITLE in preview
    pending = agent.personal_assistant_state_store.get_pending_timer_action()
    assert pending["kind"] == "stop"
    assert registry.active is True

    result = build_deterministic_timer_response(agent, pending["confirmText"])

    assert result == f"הטיימר של „{TASK_TITLE}” נעצר. אין כרגע טיימר פעיל."
    assert registry.active is False
    assert agent.personal_assistant_state_store.get_pending_timer_action() is None


def test_unrelated_yes_does_not_commit_pending_timer(monkeypatch, tmp_path):
    registry = FakeRegistry()
    monkeypatch.setattr("agent.personal_assistant_timer.registry", registry)
    agent = _agent(tmp_path)
    build_deterministic_timer_response(agent, f"תתחיל את המשימה {TASK_TITLE}.")

    assert build_deterministic_timer_response(agent, "כן") is None
    assert registry.active is False
    assert agent.personal_assistant_state_store.get_pending_timer_action() is not None
