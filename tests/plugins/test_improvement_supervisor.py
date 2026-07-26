"""Behavior contract for the proposal-only improvement supervisor plugin."""

from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
import threading
import time
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "plugins" / "improvement-supervisor"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_IMPROVEMENT_RUNTIME_POLL", "0")
    yield home


def _load_package():
    package_name = "hermes_plugins.improvement_supervisor"
    for name in list(sys.modules):
        if name == package_name or name.startswith(f"{package_name}."):
            sys.modules.pop(name, None)
    if "hermes_plugins" not in sys.modules:
        namespace = types.ModuleType("hermes_plugins")
        namespace.__path__ = []
        sys.modules["hermes_plugins"] = namespace
    spec = importlib.util.spec_from_file_location(
        package_name,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = package_name
    module.__path__ = [str(PLUGIN_DIR)]
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    module._set_runtime_snapshot_for_tests(
        lambda _root, _digest, fingerprint: REPO_ROOT
        / ".test-repair-snapshots"
        / fingerprint[:12]
    )
    return module


class _FakeLlm:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(parsed=self.parsed)


class _FakeKanban:
    def __init__(self):
        self.tasks = {}
        self.created = []
        self.attachments = []
        self.comments = []
        self.board_metadata = []

    class _Connection:
        def close(self):
            return None

    def connect(self, *, board=None):
        self.board = board
        return self._Connection()

    def write_board_metadata(self, board, **kwargs):
        self.board_metadata.append((board, kwargs))
        return {"slug": board, **kwargs}

    def read_board_metadata(self, board):
        if self.board_metadata:
            return {"slug": board, **self.board_metadata[-1][1]}
        return {"slug": board, "dispatcher_mode": "generic"}

    def get_task(self, _conn, task_id):
        return self.tasks.get(task_id)

    def create_task(self, _conn, **kwargs):
        self.created.append(kwargs)
        task_id = f"t_repair{len(self.created)}"
        self.tasks[task_id] = SimpleNamespace(
            id=task_id,
            status="ready",
            created_by=kwargs.get("created_by"),
            tenant=kwargs.get("tenant"),
        )
        return task_id

    def store_attachment_bytes(self, _conn, task_id, filename, data, **kwargs):
        self.attachments.append((task_id, filename, data, kwargs))
        return len(self.attachments)

    def add_comment(self, _conn, task_id, author, body):
        self.comments.append((task_id, author, body))
        return len(self.comments)


def _proposal(**overrides):
    value = {
        "should_propose": True,
        "category": "runtime_failure",
        "title": "Terminal retries fail after reconnect",
        "summary": "The same terminal operation failed after a reconnect.",
        "dedup_key": "terminal-reconnect-retry",
        "confidence": "high",
        "evidence": "A terminal call returned a connection error.",
        "next_check": "Reproduce a reconnect followed by one terminal call.",
    }
    value.update(overrides)
    return value


def _rich_watchdog_incident(event_id="watchdog-rich-1", **overrides):
    value = {
        "schema_version": 1,
        "event_id": event_id,
        "event": "watchdog_incident",
        "observed_at": "2026-07-20T14:00:00+00:00",
        "severity": "error",
        "failure": {
            "taxonomy": "queue.acceptance",
            "component": "desktop_composer",
            "code": "queue_push_rejected",
            "message": "Composer rejected a queued follow-up",
        },
        "source": {
            "repo_root": str(REPO_ROOT),
            "revision": "abc123",
            "dirty": True,
            "runtime_build_id": "source-abc123-def456",
            "source_manifest_digest": "a" * 64,
        },
        "conversation": {
            "phase": "thinking",
            "idle_seconds": 31.5,
            "waiting": False,
        },
        "queue": {"depth": 0, "head_age_seconds": 0, "state": "rejected"},
        "renderer": {"artifact_type": "task-profile-review", "status": "ready"},
        "retry_history": [
            {"attempt": index, "classification": "timeout", "delay_seconds": index}
            for index in range(10)
        ],
        "logs": [
            "Authorization: Bearer sk-proj-abcdefghijklmnopqrstuvwxyz0123456789 "
            + ("x" * 800)
            for _ in range(40)
        ],
    }
    value.update(overrides)
    return value


def test_successful_ordinary_turn_does_not_call_model(_isolated_home):
    plugin = _load_package()
    llm = _FakeLlm(_proposal())
    plugin._set_llm_for_tests(llm)

    plugin._on_post_tool_call(
        tool_name="read_file",
        status="ok",
        turn_id="turn-ok",
        result="done",
    )
    plugin._on_post_llm_call(
        turn_id="turn-ok",
        session_id="session-a",
        user_message="Summarize this file.",
        assistant_response="Here is the summary.",
    )

    assert llm.calls == []
    assert plugin.store.list_proposals() == []


def test_tool_request_repairs_duplicate_clarify_choices_and_records_safe_incident(
    _isolated_home,
):
    plugin = _load_package()
    question = "כמה משימות לא מאופיינות יש?"
    duplicate = "רנדר לי HTML עם רשימה"

    result = plugin._on_tool_request(
        tool_name="clarify",
        args={"question": question, "choices": [duplicate, duplicate]},
        session_id="session-private",
        turn_id="turn-private",
        tool_call_id="clarify-private",
    )

    assert result["args"] == {"question": question, "choices": [duplicate]}
    assert plugin.store.list_proposals() == []
    plugin._on_post_tool_call(
        tool_name="clarify",
        status="ok",
        tool_call_id="clarify-private",
        turn_id="turn-private",
        session_id="session-private",
    )
    proposals = plugin.store.list_proposals()
    assert len(proposals) == 1
    assert proposals[0]["confidence"] == "high"
    assert proposals[0]["dedup_key"] == "clarify-duplicate-choices"
    assert proposals[0]["authority"] == "runtime_repaired"
    assert proposals[0]["containment_occurrences"] == 1
    persisted = plugin.store.proposals_path().read_text(encoding="utf-8")
    assert question not in persisted
    assert duplicate not in persisted
    assert "original=2 distinct=1 removed=1" in persisted
    assert "1 repaired live" in plugin._handle_slash("status")
    assert "live containment: applied" in plugin._handle_slash(
        f"show {proposals[0]['id']}"
    ).lower()


def test_blocked_repair_attempt_does_not_claim_runtime_success(_isolated_home):
    plugin = _load_package()
    plugin._on_tool_request(
        tool_name="clarify",
        args={"question": "Pick", "choices": ["Same", "Same"]},
        tool_call_id="clarify-blocked",
        turn_id="turn-blocked",
        session_id="session-a",
    )

    plugin._on_post_tool_call(
        tool_name="clarify",
        status="blocked",
        tool_call_id="clarify-blocked",
        turn_id="turn-blocked",
        session_id="session-a",
    )

    assert plugin.store.list_proposals() == []


def test_live_repairs_are_isolated_when_sessions_reuse_tool_call_ids(
    _isolated_home,
):
    plugin = _load_package()
    shared_id = "acp_call_1"
    plugin._on_tool_request(
        tool_name="clarify",
        args={"question": "A", "choices": ["Same", "Same"]},
        tool_call_id=shared_id,
        turn_id="turn-a",
        session_id="session-a",
    )
    plugin._on_tool_request(
        tool_name="clarify",
        args={"question": "B", "choices": ["Again", "Again", "Again"]},
        tool_call_id=shared_id,
        turn_id="turn-b",
        session_id="session-b",
    )

    plugin._on_post_tool_call(
        tool_name="clarify",
        status="ok",
        tool_call_id=shared_id,
        turn_id="turn-a",
        session_id="session-a",
    )
    first = plugin.store.list_proposals()[0]
    assert first["evidence"] == "original=2 distinct=1 removed=1"
    assert first["containment_occurrences"] == 1

    plugin._on_post_tool_call(
        tool_name="clarify",
        status="ok",
        tool_call_id=shared_id,
        turn_id="turn-b",
        session_id="session-b",
    )
    merged = plugin.store.list_proposals()[0]
    assert merged["evidence"] == "original=3 distinct=1 removed=2"
    assert merged["occurrences"] == 2
    assert merged["containment_occurrences"] == 2
    assert "2 repaired live" in plugin._handle_slash("status")


def test_tool_request_leaves_distinct_clarify_choices_untouched(_isolated_home):
    plugin = _load_package()
    args = {"question": "Pick one", "choices": ["One", "Two"]}

    assert plugin._on_tool_request(tool_name="clarify", args=args) is None
    assert plugin.store.list_proposals() == []


def test_register_installs_real_time_tool_request_middleware(_isolated_home):
    plugin = _load_package()

    class _Context:
        llm = _FakeLlm(_proposal())

        def __init__(self):
            self.middleware = []

        def register_hook(self, *_args, **_kwargs):
            return None

        def register_command(self, *_args, **_kwargs):
            return None

        def register_middleware(self, kind, callback):
            self.middleware.append((kind, callback))

    context = _Context()
    plugin.register(context)

    assert context.middleware == [("tool_request", plugin._on_tool_request)]


@pytest.mark.parametrize("status", ["blocked", "cancelled"])
def test_intentional_non_execution_is_not_an_improvement_signal(
    status, _isolated_home
):
    plugin = _load_package()
    llm = _FakeLlm(_proposal())
    plugin._set_llm_for_tests(llm)
    plugin._on_post_tool_call(
        tool_name="terminal",
        status=status,
        error_message="user or policy stopped this action",
        turn_id=f"turn-{status}",
    )
    plugin._on_post_llm_call(
        turn_id=f"turn-{status}",
        session_id="session-a",
        user_message="Thanks.",
        assistant_response="The action was not run.",
    )

    assert llm.calls == []


def test_recovered_tool_failure_does_not_trigger_review(_isolated_home):
    plugin = _load_package()
    llm = _FakeLlm(_proposal())
    plugin._set_llm_for_tests(llm)
    plugin._on_post_tool_call(
        tool_name="terminal",
        status="error",
        error_message="temporary disconnect",
        turn_id="turn-recovered",
        session_id="session-a",
    )
    plugin._on_post_tool_call(
        tool_name="terminal",
        status="ok",
        result="done",
        turn_id="turn-recovered",
        session_id="session-a",
    )
    plugin._on_post_llm_call(
        turn_id="turn-recovered",
        session_id="session-a",
        user_message="Run the check.",
        assistant_response="The retry succeeded.",
    )

    assert llm.calls == []


def test_failure_signal_is_bounded_and_redacted(_isolated_home):
    plugin = _load_package()
    plugin._on_post_tool_call(
        tool_name="terminal",
        status="error",
        error_type="ConnectionError",
        error_message=(
            "Authorization: Bearer secret-value password=hunter2 "
            "ghp_abc123def456ghi789jkl "
            "https://example.test/callback?access_token=query-secret "
            + "x" * 2000
        ),
        turn_id="turn-error",
    )

    signals = plugin._drain_signals_for_tests("turn-error")
    assert len(signals) == 1
    encoded = json.dumps(signals)
    assert "secret-value" not in encoded
    assert "hunter2" not in encoded
    assert "ghp_abc123def456ghi789jkl" not in encoded
    assert "query-secret" not in encoded
    assert "[REDACTED]" in encoded
    assert len(signals[0]["message"]) <= 500


def test_qualifying_turn_creates_private_pending_proposal(_isolated_home):
    plugin = _load_package()
    llm = _FakeLlm(_proposal())
    plugin._set_llm_for_tests(llm)

    plugin._on_post_tool_call(
        tool_name="terminal",
        status="error",
        error_type="ConnectionError",
        error_message="socket closed",
        turn_id="turn-1",
    )
    assert plugin._review_turn_for_tests(
        turn_id="turn-1",
        session_id="session-a",
        user_message="It still does not work after reconnecting.",
        assistant_response="I could not complete the command.",
    ) is True

    proposals = plugin.store.list_proposals()
    assert len(proposals) == 1
    assert proposals[0]["status"] == "pending"
    assert proposals[0]["authority"] == "proposal_only"
    assert proposals[0]["occurrences"] == 1
    assert llm.calls[0]["purpose"] == "improvement_supervisor_review"

    path = plugin.store.proposals_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "parsed",
    [
        None,
        {},
        _proposal(confidence="low"),
        _proposal(should_propose=False),
        _proposal(category="invented"),
    ],
)
def test_invalid_or_low_confidence_review_is_ignored(parsed, _isolated_home):
    plugin = _load_package()
    plugin._set_llm_for_tests(_FakeLlm(parsed))

    assert plugin._review_turn_for_tests(
        turn_id="turn-invalid",
        session_id="session-a",
        user_message="This is still broken.",
        assistant_response="I see the failure.",
    ) is False
    assert plugin.store.list_proposals() == []


def test_duplicate_proposals_merge_and_dismissal_latches(_isolated_home):
    plugin = _load_package()
    llm = _FakeLlm(_proposal())
    plugin._set_llm_for_tests(llm)

    for turn_id in ("turn-a", "turn-b"):
        assert plugin._review_turn_for_tests(
            turn_id=turn_id,
            session_id="session-a",
            user_message="This is still broken.",
            assistant_response="The reconnect failed.",
        ) is True

    proposal = plugin.store.list_proposals()[0]
    assert proposal["occurrences"] == 2
    assert plugin.store.dismiss_proposal(proposal["id"]) is True

    assert plugin._review_turn_for_tests(
        turn_id="turn-c",
        session_id="session-a",
        user_message="This is still broken.",
        assistant_response="The reconnect failed again.",
    ) is True
    proposal = plugin.store.list_proposals()[0]
    assert proposal["occurrences"] == 3
    assert proposal["status"] == "dismissed"


def test_unicode_dedup_keys_do_not_collapse_unrelated_proposals(_isolated_home):
    plugin = _load_package()
    first = plugin.store.record_proposal(
        _proposal(category="missing_capability", dedup_key="חסר חיפוש", title="א")
    )
    second = plugin.store.record_proposal(
        _proposal(category="missing_capability", dedup_key="חסר תזמון", title="ב")
    )

    assert first["issue_key"] != second["issue_key"]
    assert len(plugin.store.list_proposals()) == 2


def test_model_output_is_redacted_and_audited(_isolated_home):
    plugin = _load_package()
    plugin._set_llm_for_tests(
        _FakeLlm(
            _proposal(
                summary="authorization=private-token was printed",
                evidence="Bearer another-secret appeared in the failure",
                dedup_key="authorization=dedup-secret",
            )
        )
    )

    assert plugin._review_turn_for_tests(
        turn_id="turn-redacted-output",
        session_id="session-a",
        user_message="This is still broken.",
        assistant_response="I see the failure.",
    ) is True

    persisted = plugin.store.proposals_path().read_text(encoding="utf-8")
    assert "private-token" not in persisted
    assert "another-secret" not in persisted
    assert "dedup-secret" not in persisted
    assert "[REDACTED]" in persisted
    audit_lines = plugin.store.audit_path().read_text(encoding="utf-8").splitlines()
    audit = [json.loads(line) for line in audit_lines]
    assert audit[-1]["event"] == "proposal_created"
    assert set(audit[-1]) == {"ts", "event", "proposal_id", "status"}


def test_accept_command_changes_state_but_executes_nothing(_isolated_home):
    plugin = _load_package()
    plugin._set_llm_for_tests(_FakeLlm(_proposal()))
    plugin._review_turn_for_tests(
        turn_id="turn-command",
        session_id="session-a",
        user_message="This is still broken.",
        assistant_response="The reconnect failed.",
    )
    proposal = plugin.store.list_proposals()[0]

    response = plugin._handle_slash(f"accept {proposal['id']}")

    assert "normal foreground task" in response
    assert plugin.store.get_proposal(proposal["id"])["status"] == "accepted"
    assert not (PLUGIN_DIR / ".git").exists()


def test_profile_state_isolated(_isolated_home, tmp_path, monkeypatch):
    plugin = _load_package()
    plugin._set_llm_for_tests(_FakeLlm(_proposal()))
    plugin._review_turn_for_tests(
        turn_id="turn-profile-a",
        session_id="session-a",
        user_message="This is still broken.",
        assistant_response="The reconnect failed.",
    )
    assert len(plugin.store.list_proposals()) == 1

    other_home = tmp_path / "other-profile"
    other_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(other_home))
    assert plugin.store.list_proposals() == []


def test_post_llm_hook_is_non_blocking(_isolated_home, monkeypatch):
    plugin = _load_package()
    started = []

    class _Thread:
        def __init__(self, *, target, name, daemon):
            started.append((target, name, daemon))

        def start(self):
            return None

    monkeypatch.setattr(plugin.threading, "Thread", _Thread)
    plugin._set_llm_for_tests(_FakeLlm(_proposal()))

    before = time.monotonic()
    plugin._on_post_llm_call(
        turn_id="turn-thread",
        session_id="session-a",
        user_message="This is still broken.",
        assistant_response="I see it.",
    )
    elapsed = time.monotonic() - before

    assert elapsed < 0.1
    assert len(started) == 1
    assert started[0][1].startswith("hermes-improvement-review-")
    assert started[0][2] is True


def test_background_review_keeps_originating_profile_scope(
    _isolated_home, tmp_path, monkeypatch
):
    plugin = _load_package()
    targets = []

    class _Thread:
        def __init__(self, *, target, name, daemon):
            targets.append(target)

        def start(self):
            return None

    monkeypatch.setattr(plugin.threading, "Thread", _Thread)
    from agent import secret_scope

    llm = _FakeLlm(_proposal())
    llm.scopes = []
    original_complete = llm.complete_structured

    def complete_with_scope(**kwargs):
        llm.scopes.append(secret_scope.current_secret_scope())
        return original_complete(**kwargs)

    llm.complete_structured = complete_with_scope
    plugin._set_llm_for_tests(llm)
    secret_token = secret_scope.set_secret_scope({"OPENAI_API_KEY": "profile-a-key"})
    try:
        plugin._on_post_llm_call(
            turn_id="turn-profile-thread",
            session_id="session-a",
            user_message="This is still broken.",
            assistant_response="I see it.",
        )
    finally:
        secret_scope.reset_secret_scope(secret_token)

    other_home = tmp_path / "other-thread-profile"
    other_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(other_home))
    targets[0]()

    origin_store = (
        _isolated_home / "state" / "improvement-supervisor" / "proposals.json"
    )
    other_store = other_home / "state" / "improvement-supervisor" / "proposals.json"
    assert origin_store.exists()
    assert not other_store.exists()
    assert llm.scopes == [{"OPENAI_API_KEY": "profile-a-key"}]


def test_review_spawning_has_hard_resource_cap(_isolated_home, monkeypatch):
    plugin = _load_package()
    started = []

    class _Thread:
        def __init__(self, *, target, name, daemon):
            started.append(target)

        def start(self):
            return None

    monkeypatch.setattr(plugin.threading, "Thread", _Thread)
    plugin._set_llm_for_tests(_FakeLlm(_proposal()))

    for index in range(10):
        plugin._on_post_llm_call(
            turn_id=f"turn-cap-{index}",
            session_id="session-a",
            user_message="This is still broken.",
            assistant_response="I see it.",
        )

    assert 1 <= len(started) <= plugin.MAX_CONCURRENT_REVIEWS


def test_registers_repair_middleware_observers_and_slash_command(_isolated_home):
    plugin = _load_package()
    hooks = []
    commands = []
    middleware = []

    class _Context:
        llm = _FakeLlm(_proposal())

        def register_hook(self, name, callback):
            hooks.append((name, callback))

        def register_command(self, name, handler, description="", args_hint=""):
            commands.append((name, handler, description, args_hint))

        def register_middleware(self, kind, callback):
            middleware.append((kind, callback))

    plugin.register(_Context())

    assert {name for name, _ in hooks} == {
        "post_tool_call",
        "api_request_error",
        "post_llm_call",
        "on_session_end",
    }
    assert [name for name, *_ in commands] == ["improvements"]
    assert middleware == [("tool_request", plugin._on_tool_request)]


def test_runtime_recovery_inbox_becomes_a_private_supervisor_incident(_isolated_home):
    plugin = _load_package()
    inbox = (
        _isolated_home
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    inbox.parent.mkdir(parents=True)
    inbox.write_text(
        json.dumps(
            {
                "event_id": "safe-event-1",
                "event": "flowstate_connector_recovery",
                "action": "launch",
                "outcome": "repaired",
                "reason": "flowstate_health_verified",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert "1 repaired live" in plugin._handle_slash("status")
    proposals = plugin.store.list_proposals()
    assert len(proposals) == 1
    assert proposals[0]["dedup_key"] == "flowstate-connector-recovery"
    assert proposals[0]["authority"] == "runtime_repaired"
    assert "safe-event-1" not in plugin.store.proposals_path().read_text(encoding="utf-8")

    # Re-reading the durable inbox must not inflate occurrence counts.
    plugin._handle_slash("status")
    assert plugin.store.list_proposals()[0]["occurrences"] == 1


def test_rich_watchdog_incident_creates_one_bounded_worktree_repair_task(
    _isolated_home,
):
    plugin = _load_package()
    kanban = _FakeKanban()
    plugin._set_kanban_for_tests(kanban)
    inbox = (
        _isolated_home
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    inbox.parent.mkdir(parents=True)
    inbox.write_text(
        json.dumps(_rich_watchdog_incident(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    plugin._handle_slash("status")
    plugin._handle_slash("status")

    assert len(kanban.created) == 1
    created = kanban.created[0]
    assert created["workspace_kind"] == "dir"
    assert created["max_retries"] == 1
    assert 60 <= created["max_runtime_seconds"] <= 1800
    assert created["created_by"] == "improvement-supervisor"
    assert created["tenant"] == "improvement-supervisor"
    assert created["executor_kind"] == "codex-repair"
    assert created["initial_status"] == "running"
    assert kanban.board_metadata == [
        ("hermes-repairs", {"dispatcher_mode": "repair-only"})
    ]
    assert created["idempotency_key"].startswith("improvement-repair:")
    assert created["branch_name"] is None
    assert "must not merge, deploy, restart" in created["body"].lower()
    assert "regression test" in created["body"].lower()
    assert kanban.board == "hermes-repairs"

    assert len(kanban.attachments) == 1
    task_id, filename, raw_bundle, attachment_kwargs = kanban.attachments[0]
    assert task_id == "t_repair1"
    assert filename.startswith("watchdog-incident-")
    assert attachment_kwargs["max_bytes"] == 64 * 1024
    assert len(raw_bundle) <= 64 * 1024
    assert b"sk-proj-abcdefghijklmnopqrstuvwxyz0123456789" not in raw_bundle
    bundle = json.loads(raw_bundle)
    assert bundle["schema_version"] == 1
    assert bundle["failure"]["taxonomy"] == "queue.acceptance"
    assert len(bundle["retry_history"]) == 5
    assert len(bundle["logs"]) == 20
    assert set(bundle) == {
        "schema_version",
        "event_id",
        "event",
        "observed_at",
        "severity",
        "fingerprint",
        "failure",
        "source",
        "conversation",
        "tool",
        "queue",
        "persistence",
        "reconnect",
        "renderer",
        "backend",
        "retry_history",
        "logs",
    }
    assert kanban.comments == [
        (
            "t_repair1",
            "improvement-supervisor",
            "Watchdog incident queue.acceptance (error) attached; occurrence watchdog-rich-1.",
        )
    ]
    lifecycle = plugin.store.get_repair_lifecycle()
    assert lifecycle["schemaVersion"] == 1
    assert lifecycle["taskId"] == "t_repair1"
    assert lifecycle["status"] == "queued"
    assert lifecycle["outcomeCode"] == "incident_admitted"
    assert lifecycle["updatedAt"].endswith("Z")
    execution = plugin.store.get_repair_execution()
    assert execution["task_id"] == "t_repair1"
    assert execution["state"] == "admitted"
    assert execution["source_digest"] == "a" * 64


def test_enabled_plugin_ingests_watchdog_incident_without_slash_command(
    _isolated_home, monkeypatch
):
    plugin = _load_package()
    kanban = _FakeKanban()
    plugin._set_kanban_for_tests(kanban)
    monkeypatch.setenv("HERMES_IMPROVEMENT_RUNTIME_POLL", "0.01")
    inbox = (
        _isolated_home
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    inbox.parent.mkdir(parents=True)
    inbox.write_text(
        json.dumps(_rich_watchdog_incident(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    class _Context:
        llm = _FakeLlm(_proposal())

        def register_hook(self, *_args, **_kwargs):
            return None

        def register_command(self, *_args, **_kwargs):
            return None

        def register_middleware(self, *_args, **_kwargs):
            return None

    plugin.register(_Context())
    deadline = time.monotonic() + 1
    while not kanban.created and time.monotonic() < deadline:
        time.sleep(0.01)
    plugin._stop_runtime_ingest_for_tests()

    assert len(kanban.created) == 1
    assert kanban.created[0]["workspace_kind"] == "dir"
    assert kanban.created[0]["branch_name"] is None


def test_register_starts_only_one_runtime_poller(_isolated_home, monkeypatch):
    plugin = _load_package()
    monkeypatch.setenv("HERMES_IMPROVEMENT_RUNTIME_POLL", "5")
    started = []

    class _Thread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon
            self.alive = False

        def start(self):
            self.alive = True
            started.append(self)

        def is_alive(self):
            return self.alive

    class _Context:
        llm = _FakeLlm(_proposal())

        def register_hook(self, *_args, **_kwargs):
            return None

        def register_command(self, *_args, **_kwargs):
            return None

        def register_middleware(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(plugin.threading, "Thread", _Thread)

    plugin.register(_Context())
    plugin.register(_Context())

    assert len(started) == 1
    assert started[0].daemon is True
    assert started[0].name == "hermes-improvement-runtime-ingest"


def test_runtime_poller_survives_one_tick_failure(_isolated_home, monkeypatch):
    plugin = _load_package()
    calls = []

    def _poll():
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise RuntimeError("temporary poll failure")

    class _Stop:
        def __init__(self):
            self.waits = 0

        def is_set(self):
            return self.waits >= 2

        def wait(self, interval):
            assert interval == 5
            self.waits += 1
            return self.is_set()

    monkeypatch.setattr(plugin, "_poll_runtime_event_homes", _poll)

    plugin._run_runtime_ingest_poller(_Stop(), 5)

    assert calls == [1, 2]


def test_runtime_poller_discovers_root_and_named_profile_inboxes(
    _isolated_home, monkeypatch
):
    plugin = _load_package()
    profile_home = _isolated_home / "profiles" / "office-work"
    homes = (_isolated_home, profile_home)
    for index, home in enumerate(homes):
        inbox = home / "state" / "improvement-supervisor" / "runtime-events.jsonl"
        inbox.parent.mkdir(parents=True)
        inbox.write_text(
            json.dumps(
                _rich_watchdog_incident(
                    f"profile-warning-{index}",
                    severity="warning",
                    failure={
                        "taxonomy": f"profile.failure.{index}",
                        "component": "runtime",
                        "code": "warning",
                    },
                )
            )
            + "\n",
            encoding="utf-8",
        )

    plugin._poll_runtime_event_homes()

    assert plugin.get_hermes_home() == _isolated_home
    for home in homes:
        proposals = json.loads(
            (home / "state" / "improvement-supervisor" / "proposals.json").read_text(
                encoding="utf-8"
            )
        )["proposals"]
        assert len(proposals) == 1


def test_runtime_ingest_uses_profile_cross_process_lock(_isolated_home, monkeypatch):
    plugin = _load_package()
    entered = []

    @contextmanager
    def _lock():
        entered.append("entered")
        yield

    monkeypatch.setattr(plugin.store, "runtime_event_ingest_lock", _lock)

    plugin._ingest_runtime_events()

    assert entered == ["entered"]


def test_transient_repair_feed_failure_retries_before_checkpointing(
    _isolated_home,
):
    plugin = _load_package()

    class _FlakyKanban(_FakeKanban):
        def __init__(self):
            super().__init__()
            self.failures = 1

        def create_task(self, _conn, **kwargs):
            if self.failures:
                self.failures -= 1
                raise RuntimeError("temporary database failure")
            return super().create_task(_conn, **kwargs)

    kanban = _FlakyKanban()
    plugin._set_kanban_for_tests(kanban)
    root = _isolated_home / "state" / "improvement-supervisor"
    root.mkdir(parents=True)
    (root / "runtime-events.jsonl").write_text(
        json.dumps(_rich_watchdog_incident("retry-me")) + "\n",
        encoding="utf-8",
    )

    plugin._ingest_runtime_events()

    assert not (root / "runtime-events-seen.json").exists()
    assert plugin.store.list_proposals() == []

    plugin._ingest_runtime_events()

    assert len(kanban.created) == 1
    assert json.loads((root / "runtime-events-seen.json").read_text()) == ["retry-me"]
    assert plugin.store.list_proposals()[0]["occurrences"] == 1


def test_deferred_global_admission_is_retried_then_attached(_isolated_home):
    plugin = _load_package()
    kanban = _FakeKanban()
    plugin._set_kanban_for_tests(kanban)
    claim = plugin.store.try_claim_repair_admission("another-incident")
    root = _isolated_home / "state" / "improvement-supervisor"
    root.mkdir(parents=True)
    (root / "runtime-events.jsonl").write_text(
        json.dumps(_rich_watchdog_incident("defer-me")) + "\n",
        encoding="utf-8",
    )

    plugin._ingest_runtime_events()

    assert not (root / "runtime-events-seen.json").exists()
    assert plugin.store.list_proposals() == []

    kanban.tasks["existing-repair"] = SimpleNamespace(status="ready")
    assert plugin.store.commit_repair_admission(
        claim["admission"]["token"], "existing-repair"
    )
    plugin._ingest_runtime_events()

    assert [item[0] for item in kanban.attachments] == ["existing-repair"]
    assert json.loads((root / "runtime-events-seen.json").read_text()) == ["defer-me"]


@pytest.mark.parametrize(
    "terminal_status",
    ["blocked", "crashed", "timed_out", "failed", "cancelled", "released"],
)
def test_terminal_repair_task_does_not_pin_global_admission(
    terminal_status, _isolated_home
):
    plugin = _load_package()
    kanban = _FakeKanban()
    plugin._set_kanban_for_tests(kanban)
    claim = plugin.store.try_claim_repair_admission("old-incident")
    kanban.tasks["old-repair"] = SimpleNamespace(status=terminal_status)
    assert plugin.store.commit_repair_admission(
        claim["admission"]["token"], "old-repair"
    )

    outcome, task_id = plugin._feed_repair_incident_outcome(
        plugin._normalize_watchdog_incident(
            _rich_watchdog_incident(f"after-{terminal_status}")
        )
    )

    assert outcome == plugin._REPAIR_FEED_HANDLED
    assert task_id == "t_repair1"
    assert plugin.store.get_repair_admission()["task_id"] == "t_repair1"


def test_slash_and_poller_concurrently_ingest_one_event_once(_isolated_home):
    plugin = _load_package()
    kanban = _FakeKanban()
    plugin._set_kanban_for_tests(kanban)
    root = _isolated_home / "state" / "improvement-supervisor"
    root.mkdir(parents=True)
    (root / "runtime-events.jsonl").write_text(
        json.dumps(_rich_watchdog_incident("concurrent-event")) + "\n",
        encoding="utf-8",
    )
    barrier = threading.Barrier(3)

    def _ingest():
        barrier.wait()
        plugin._ingest_runtime_events()

    workers = [threading.Thread(target=_ingest) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=2)

    assert all(not worker.is_alive() for worker in workers)
    assert len(kanban.created) == 1
    assert len(kanban.attachments) == 1
    assert plugin.store.list_proposals()[0]["occurrences"] == 1


def test_seen_checkpoint_keeps_recent_encounter_order(_isolated_home):
    plugin = _load_package()
    root = _isolated_home / "state" / "improvement-supervisor"
    root.mkdir(parents=True)
    old_ids = [f"z-{index:04d}" for index in range(4000)]
    (root / "runtime-events-seen.json").write_text(json.dumps(old_ids), encoding="utf-8")
    (root / "runtime-events.jsonl").write_text(
        json.dumps(
            {
                "event_id": "a-new",
                "event": "flowstate_connector_recovery",
                "action": "none",
                "outcome": "auth_required",
                "reason": "sign_in_required",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    plugin._ingest_runtime_events()

    checkpoint = json.loads((root / "runtime-events-seen.json").read_text())
    assert len(checkpoint) == 4000
    assert checkpoint[-1] == "a-new"
    assert "z-0000" not in checkpoint


def test_distinct_rich_incidents_feed_the_one_active_repair_task(_isolated_home):
    plugin = _load_package()
    kanban = _FakeKanban()
    plugin._set_kanban_for_tests(kanban)
    inbox = (
        _isolated_home
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    inbox.parent.mkdir(parents=True)
    first = _rich_watchdog_incident("watchdog-rich-a")
    second = _rich_watchdog_incident(
        "watchdog-rich-b",
        failure={
            "taxonomy": "renderer.artifact_invalid",
            "component": "desktop_renderer",
            "code": "profile_fields_empty",
            "message": "The interactive card was rejected",
        },
    )
    inbox.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in (first, second))
        + "\n",
        encoding="utf-8",
    )

    plugin._handle_slash("status")

    assert len(kanban.created) == 1
    assert [item[0] for item in kanban.attachments] == ["t_repair1", "t_repair1"]
    assert len(kanban.comments) == 2
    admission = plugin.store.get_repair_admission()
    assert admission["task_id"] == "t_repair1"
    assert admission["status"] == "task_created"


def test_repair_admission_is_global_across_profiles(
    _isolated_home, tmp_path, monkeypatch
):
    plugin = _load_package()
    root = tmp_path / "shared-hermes"
    profile_a = root / "profiles" / "a"
    profile_b = root / "profiles" / "b"
    profile_a.mkdir(parents=True)
    profile_b.mkdir(parents=True)

    monkeypatch.setenv("HERMES_HOME", str(profile_a))
    first = plugin.store.try_claim_repair_admission("first")
    monkeypatch.setenv("HERMES_HOME", str(profile_b))
    second = plugin.store.try_claim_repair_admission("second")

    assert first["claimed"] is True
    assert second["claimed"] is False
    assert second["admission"]["token"] == first["admission"]["token"]
    assert plugin.store.repair_admission_path().parent == (
        root / "state" / "improvement-supervisor-global"
    )


def test_repair_execution_state_is_private_and_monotonic(_isolated_home):
    plugin = _load_package()

    assert plugin.store.initialize_repair_execution(
        "task-1",
        fingerprint="f" * 64,
        source_digest="a" * 64,
        snapshot_path="/private/snapshot",
    )
    assert plugin.store.transition_repair_execution(
        "task-1",
        "launching",
        run_id=7,
        unit_name="hermes-repair-task-1-7.service",
    )
    assert not plugin.store.transition_repair_execution("task-1", "admitted")
    execution = plugin.store.get_repair_execution()
    assert execution["state"] == "launching"
    assert execution["run_id"] == 7
    assert execution["snapshot_path"] == "/private/snapshot"
    assert plugin.store.repair_execution_path().stat().st_mode & 0o777 == 0o600


def _setup_admitted_real_repair(plugin, kb, home):
    snapshot = home / "snapshot-real"
    snapshot.mkdir()
    subprocess.run(["git", "-C", str(snapshot), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(snapshot), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(snapshot), "config", "user.name", "Test"], check=True)
    (snapshot / "agent").mkdir()
    (snapshot / "agent" / "repair.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "-C", str(snapshot), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(snapshot), "commit", "-qm", "baseline"], check=True)
    kb.create_board("hermes-repairs", dispatcher_mode="repair-only")
    with kb.connect(board="hermes-repairs") as conn:
        task_id = kb.create_task(
            conn,
            title="repair",
            body="Prepare a tested candidate only.",
            assignee="codex-repair",
            executor_kind="codex-repair",
            workspace_kind="dir",
            workspace_path=str(snapshot),
            initial_status="running",
            max_runtime_seconds=1800,
        )
        kb.store_attachment_bytes(
            conn,
            task_id,
            "watchdog-incident-test.json",
            b'{"schema_version":1}',
            board="hermes-repairs",
            max_bytes=64 * 1024,
        )
    claim = plugin.store.try_claim_repair_admission("f" * 64)
    assert plugin.store.commit_repair_admission(claim["admission"]["token"], task_id)
    assert plugin.store.initialize_repair_execution(
        task_id,
        fingerprint="f" * 64,
        source_digest="a" * 64,
        snapshot_path=str(snapshot),
    )
    assert plugin.store.transition_repair_lifecycle(
        task_id, "queued", outcome_code="incident_admitted"
    )
    return task_id, snapshot


def test_repair_tick_fails_closed_before_claim_without_tokenless_proxy(
    _isolated_home, monkeypatch
):
    from hermes_cli import kanban_db as kb

    plugin = _load_package()
    plugin._set_kanban_for_tests(kb)
    monkeypatch.delenv("HERMES_CODEX_REPAIR_PROXY", raising=False)
    snapshot = _isolated_home / "snapshot"
    snapshot.mkdir()
    kb.create_board("hermes-repairs", dispatcher_mode="repair-only")
    with kb.connect(board="hermes-repairs") as conn:
        task_id = kb.create_task(
            conn,
            title="repair",
            assignee="codex-repair",
            executor_kind="codex-repair",
            workspace_kind="dir",
            workspace_path=str(snapshot),
            initial_status="running",
        )
    claim = plugin.store.try_claim_repair_admission("f" * 64)
    assert plugin.store.commit_repair_admission(claim["admission"]["token"], task_id)
    assert plugin.store.initialize_repair_execution(
        task_id,
        fingerprint="f" * 64,
        source_digest="a" * 64,
        snapshot_path=str(snapshot),
    )
    assert plugin.store.transition_repair_lifecycle(
        task_id, "queued", outcome_code="incident_admitted"
    )

    assert plugin._tick_repair_worker() == "model_proxy_unavailable"

    with kb.connect(board="hermes-repairs") as conn:
        assert kb.get_task(conn, task_id).status == "blocked"
    assert plugin.store.get_repair_execution()["state"] == "rejected"
    assert plugin.store.get_repair_lifecycle()["status"] == "failed"
    assert plugin.store.get_repair_admission() == {}


def test_repair_tick_reconciles_legacy_admission_without_execution_state(
    _isolated_home,
):
    from hermes_cli import kanban_db as kb

    plugin = _load_package()
    plugin._set_kanban_for_tests(kb)
    snapshot = _isolated_home / "legacy-snapshot"
    snapshot.mkdir()
    kb.create_board("hermes-repairs", dispatcher_mode="repair-only")
    with kb.connect(board="hermes-repairs") as conn:
        task_id = kb.create_task(
            conn,
            title="legacy repair",
            executor_kind="codex-repair",
            workspace_kind="dir",
            workspace_path=str(snapshot),
            initial_status="running",
        )
    claim = plugin.store.try_claim_repair_admission("f" * 64)
    assert plugin.store.commit_repair_admission(claim["admission"]["token"], task_id)

    assert plugin._tick_repair_worker() == "legacy_admission_rejected"
    assert plugin.store.get_repair_admission() == {}
    assert plugin.store.get_repair_lifecycle()["status"] == "failed"
    with kb.connect(board="hermes-repairs") as conn:
        assert kb.get_task(conn, task_id).status == "blocked"


def test_repair_tick_launches_one_bounded_unit_and_adopts_it_on_next_tick(
    _isolated_home, monkeypatch
):
    from hermes_cli import kanban_db as kb

    plugin = _load_package()
    plugin._set_kanban_for_tests(kb)
    snapshot = _isolated_home / "snapshot"
    snapshot.mkdir()
    (snapshot / ".git").mkdir()
    kb.create_board("hermes-repairs", dispatcher_mode="repair-only")
    with kb.connect(board="hermes-repairs") as conn:
        task_id = kb.create_task(
            conn,
            title="repair",
            body="Prepare a tested candidate only.",
            assignee="codex-repair",
            executor_kind="codex-repair",
            workspace_kind="dir",
            workspace_path=str(snapshot),
            initial_status="running",
            max_runtime_seconds=1800,
        )
        kb.store_attachment_bytes(
            conn,
            task_id,
            "watchdog-incident-test.json",
            b'{"schema_version":1}',
            board="hermes-repairs",
            max_bytes=64 * 1024,
        )
    claim = plugin.store.try_claim_repair_admission("f" * 64)
    assert plugin.store.commit_repair_admission(claim["admission"]["token"], task_id)
    assert plugin.store.initialize_repair_execution(
        task_id,
        fingerprint="f" * 64,
        source_digest="a" * 64,
        snapshot_path=str(snapshot),
    )
    assert plugin.store.transition_repair_lifecycle(
        task_id, "queued", outcome_code="incident_admitted"
    )
    monkeypatch.setenv("HERMES_CODEX_REPAIR_BIN", "/usr/bin/codex")
    monkeypatch.setenv("HERMES_CODEX_REPAIR_PROXY", "http://127.0.0.1:43123/v1")
    commands = []
    unit_active = {"value": True}

    def run(argv):
        commands.append(argv)
        if argv == ["/usr/bin/codex", "--help"]:
            return 0, "--ask-for-approval"
        if argv == ["/usr/bin/codex", "exec", "--help"]:
            return 0, "--ephemeral --ignore-user-config --ignore-rules --output-schema --output-last-message --json"
        if argv == ["systemd-run", "--user", "--version"]:
            return 0, "systemd 258"
        if argv[:3] == ["systemctl", "--user", "show"]:
            if unit_active["value"]:
                return 0, "LoadState=loaded\nActiveState=active\nSubState=running\nResult=success\nExecMainStatus=0\n"
            return 0, "LoadState=loaded\nActiveState=failed\nSubState=failed\nResult=exit-code\nExecMainStatus=1\n"
        return 0, "Running as unit"

    assert plugin._tick_repair_worker(run=run) == "running"
    assert plugin._tick_repair_worker(run=run) == "running"

    launches = [argv for argv in commands if argv and argv[0] == "systemd-run" and "--no-block" in argv]
    assert len(launches) == 1
    assert len([argv for argv in commands if argv[:3] == ["systemctl", "--user", "show"]]) == 1
    with kb.connect(board="hermes-repairs") as conn:
        assert kb.get_task(conn, task_id).status == "running"
    execution = plugin.store.get_repair_execution()
    assert execution["state"] == "running"
    assert execution["unit_name"].startswith("hermes-repair-")
    assert Path(execution["output_dir"]).is_dir()
    assert Path(execution["manifest_path"]).name == "manifest.json"
    assert plugin.store.get_repair_lifecycle()["status"] == "running"

    unit_active["value"] = False
    assert plugin._tick_repair_worker(run=run) == "worker_failed"
    with kb.connect(board="hermes-repairs") as conn:
        assert kb.get_task(conn, task_id).status == "blocked"
    assert plugin.store.get_repair_execution()["state"] == "gave_up"
    assert plugin.store.get_repair_lifecycle()["status"] == "failed"
    assert plugin.store.get_repair_admission() == {}


def test_repair_tick_reconciles_missing_unit_after_restart(
    _isolated_home, monkeypatch
):
    from hermes_cli import kanban_db as kb

    plugin = _load_package()
    plugin._set_kanban_for_tests(kb)
    task_id, _snapshot = _setup_admitted_real_repair(plugin, kb, _isolated_home)
    monkeypatch.setenv("HERMES_CODEX_REPAIR_BIN", "/usr/bin/codex")
    monkeypatch.setenv("HERMES_CODEX_REPAIR_PROXY", "http://127.0.0.1:43123/v1")
    launched = {"value": False}

    def run(argv):
        if argv == ["/usr/bin/codex", "--help"]:
            return 0, "--ask-for-approval"
        if argv == ["/usr/bin/codex", "exec", "--help"]:
            return 0, "--ephemeral --ignore-user-config --ignore-rules --output-schema --output-last-message --json"
        if argv == ["systemd-run", "--user", "--version"]:
            return 0, "systemd 258"
        if argv[:3] == ["systemctl", "--user", "show"]:
            return 1, "Unit could not be found."
        launched["value"] = True
        return 0, "Running as unit"

    assert plugin._tick_repair_worker(run=run) == "running"
    assert launched["value"] is True
    assert plugin._tick_repair_worker(run=run) == "worker_orphaned"
    assert plugin.store.get_repair_execution()["state"] == "gave_up"
    assert plugin.store.get_repair_lifecycle()["outcomeCode"] == "worker_orphaned"
    assert plugin.store.get_repair_admission() == {}
    with kb.connect(board="hermes-repairs") as conn:
        assert kb.get_task(conn, task_id).status == "blocked"


def test_repair_tick_stops_whole_unit_when_persisted_deadline_expires(
    _isolated_home, monkeypatch
):
    from hermes_cli import kanban_db as kb

    plugin = _load_package()
    plugin._set_kanban_for_tests(kb)
    task_id, _snapshot = _setup_admitted_real_repair(plugin, kb, _isolated_home)
    monkeypatch.setenv("HERMES_CODEX_REPAIR_BIN", "/usr/bin/codex")
    monkeypatch.setenv("HERMES_CODEX_REPAIR_PROXY", "http://127.0.0.1:43123/v1")
    calls = []

    def run(argv):
        calls.append(argv)
        if argv == ["/usr/bin/codex", "--help"]:
            return 0, "--ask-for-approval"
        if argv == ["/usr/bin/codex", "exec", "--help"]:
            return 0, "--ephemeral --ignore-user-config --ignore-rules --output-schema --output-last-message --json"
        if argv == ["systemd-run", "--user", "--version"]:
            return 0, "systemd 258"
        if argv[:3] == ["systemctl", "--user", "show"]:
            return 0, "LoadState=loaded\nActiveState=active\nSubState=running\nResult=success\nExecMainStatus=0\n"
        return 0, "ok"

    assert plugin._tick_repair_worker(run=run) == "running"
    assert plugin.store.transition_repair_execution(
        task_id, "running", deadline_at="1"
    )
    assert plugin._tick_repair_worker(run=run) == "worker_timed_out"
    assert any(call[:3] == ["systemctl", "--user", "stop"] for call in calls)
    assert plugin.store.get_repair_lifecycle()["status"] == "timed_out"
    assert plugin.store.get_repair_admission() == {}


def test_successful_worker_is_sealed_before_entering_verification(
    _isolated_home, monkeypatch
):
    from hermes_cli import kanban_db as kb

    plugin = _load_package()
    plugin._set_kanban_for_tests(kb)
    task_id, snapshot = _setup_admitted_real_repair(
        plugin, kb, _isolated_home
    )
    monkeypatch.setenv("HERMES_CODEX_REPAIR_BIN", "/usr/bin/codex")
    monkeypatch.setenv("HERMES_CODEX_REPAIR_PROXY", "http://127.0.0.1:43123/v1")

    calls = []

    def run(argv):
        calls.append(argv)
        if argv == ["/usr/bin/codex", "--help"]:
            return 0, "--ask-for-approval"
        if argv == ["/usr/bin/codex", "exec", "--help"]:
            return 0, "--ephemeral --ignore-user-config --ignore-rules --output-schema --output-last-message --json"
        if argv == ["systemd-run", "--user", "--version"]:
            return 0, "systemd 258"
        if argv[:3] == ["systemctl", "--user", "show"]:
            return 0, "LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\nExecMainStatus=0\n"
        return 0, "Running as unit"

    assert plugin._tick_repair_worker(run=run) == "running"
    execution = plugin.store.get_repair_execution()
    base = subprocess.check_output(
        ["git", "-C", str(snapshot), "rev-parse", "HEAD"], text=True
    ).strip()
    (snapshot / "agent" / "repair.py").write_text("VALUE = 2\n")
    (snapshot / "tests" / "agent").mkdir(parents=True)
    (snapshot / "tests" / "agent" / "test_repair.py").write_text(
        "from agent.repair import VALUE\n\n\ndef test_value():\n    assert VALUE == 2\n"
    )
    subprocess.run(["git", "-C", str(snapshot), "add", "-A"], check=True)
    Path(execution["manifest_path"]).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "summary": "Repair the value.",
                "changed_files": ["agent/repair.py", "tests/agent/test_repair.py"],
                "tests": [
                    {"command": "pytest", "status": "passed", "output": "1 passed"}
                ],
                "remaining_failures": [],
                "base_commit": base,
                "head_commit": base,
            }
        )
    )

    assert plugin._tick_repair_worker(run=run) == "verifying"
    execution = plugin.store.get_repair_execution()
    assert execution["state"] == "verifying"
    assert Path(execution["patch_path"]).is_file()
    assert plugin.store.get_repair_lifecycle()["status"] == "verifying"
    assert any("-verify.service" in "\n".join(call) for call in calls)
    with kb.connect(board="hermes-repairs") as conn:
        assert kb.get_task(conn, task_id).status == "running"

    assert plugin._tick_repair_worker(run=run) == "candidate_ready"
    assert plugin.store.get_repair_execution()["state"] == "candidate_ready"
    assert plugin.store.get_repair_lifecycle()["status"] == "candidate_ready"
    assert plugin.store.get_repair_admission() == {}
    with kb.connect(board="hermes-repairs") as conn:
        assert kb.get_task(conn, task_id).status == "done"


def test_unverified_source_repository_does_not_spawn_repair(_isolated_home):
    plugin = _load_package()
    kanban = _FakeKanban()
    plugin._set_kanban_for_tests(kanban)
    event = _rich_watchdog_incident(
        "watchdog-bad-source",
        source={"repo_root": "relative/repository", "revision": "abc123"},
    )
    bundle = plugin._normalize_watchdog_incident(event)

    assert plugin._feed_repair_incident(bundle) is None
    assert kanban.created == []
    assert plugin.store.get_repair_admission() == {}


def test_warning_rich_incident_records_proposal_without_spawning_repair(
    _isolated_home,
):
    plugin = _load_package()
    kanban = _FakeKanban()
    plugin._set_kanban_for_tests(kanban)
    inbox = (
        _isolated_home
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    inbox.parent.mkdir(parents=True)
    inbox.write_text(
        json.dumps(
            _rich_watchdog_incident(
                "watchdog-warning-1",
                severity="warning",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    plugin._handle_slash("status")

    assert kanban.created == []
    proposal = plugin.store.list_proposals()[0]
    assert proposal["dedup_key"].startswith("watchdog-")
    assert proposal["authority"] == "proposal_only"


def test_auth_required_runtime_event_is_recorded_but_never_claimed_repaired(
    _isolated_home,
):
    plugin = _load_package()
    inbox = (
        _isolated_home
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    inbox.parent.mkdir(parents=True)
    inbox.write_text(
        json.dumps(
            {
                "event_id": "safe-event-2",
                "event": "flowstate_connector_recovery",
                "action": "none",
                "outcome": "auth_required",
                "reason": "flowstate_sign_in_required",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    plugin._handle_slash("status")
    proposal = plugin.store.list_proposals()[0]
    assert proposal["authority"] == "proposal_only"
    assert proposal["containment_occurrences"] == 0


def test_restart_replay_event_is_recorded_as_live_containment(_isolated_home):
    plugin = _load_package()
    inbox = (
        _isolated_home
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    inbox.parent.mkdir(parents=True)
    inbox.write_text(
        json.dumps(
            {
                "event_id": "restart-safe-1",
                "event": "restart_interrupted_turn_replayed",
                "action": "replay",
                "outcome": "repaired",
                "reason": "durable_pending_turn_matched",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    plugin._handle_slash("status")
    proposal = plugin.store.list_proposals()[0]
    assert proposal["dedup_key"] == "restart-interrupted-turn-recovery"
    assert proposal["authority"] == "runtime_repaired"


def test_automatic_stuck_turn_containment_is_not_claimed_as_repaired(_isolated_home):
    plugin = _load_package()
    inbox = (
        _isolated_home
        / "state"
        / "improvement-supervisor"
        / "runtime-events.jsonl"
    )
    inbox.parent.mkdir(parents=True)
    inbox.write_text(
        json.dumps(
            {
                "event_id": "stuck-safe-1",
                "event": "stuck_turn_automatically_stopped",
                "action": "interrupt",
                "outcome": "contained",
                "reason": "turn_idle_timeout",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    plugin._handle_slash("status")
    proposal = plugin.store.list_proposals()[0]
    assert proposal["dedup_key"] == "stuck-turn-automatic-recovery"
    assert proposal["authority"] == "proposal_only"
    assert proposal["containment_occurrences"] == 0
    assert "contained" in proposal["title"].lower()


def test_interrupted_session_discards_unreviewed_signals(_isolated_home):
    plugin = _load_package()
    plugin._on_post_tool_call(
        tool_name="terminal",
        status="error",
        error_message="interrupted",
        turn_id="turn-interrupted",
        session_id="session-interrupted",
    )

    plugin._on_session_end(session_id="session-interrupted", interrupted=True)

    assert plugin._drain_signals_for_tests("turn-interrupted") == []


def test_bundled_plugin_is_opt_in_and_loads_when_enabled(_isolated_home):
    import yaml

    from hermes_cli.plugins import PluginManager

    manager = PluginManager()
    manager.discover_and_load()
    discovered = manager._plugins["improvement-supervisor"]
    assert discovered.enabled is False
    assert discovered.error and "not enabled" in discovered.error

    (_isolated_home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": ["improvement-supervisor"]}}),
        encoding="utf-8",
    )
    manager = PluginManager()
    manager.discover_and_load()
    loaded = manager._plugins["improvement-supervisor"]
    assert loaded.enabled is True
    assert set(loaded.hooks_registered) == {
        "post_tool_call",
        "api_request_error",
        "post_llm_call",
        "on_session_end",
    }
    assert loaded.commands_registered == ["improvements"]
    result = manager.invoke_middleware(
        "tool_request",
        tool_name="clarify",
        args={"question": "Pick", "choices": ["Same", "Same"]},
    )
    assert result[0]["args"] == {"question": "Pick", "choices": ["Same"]}
