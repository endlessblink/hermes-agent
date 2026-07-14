"""Behavior contract for the proposal-only improvement supervisor plugin."""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
import time
import types
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
    return module


class _FakeLlm:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(parsed=self.parsed)


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
