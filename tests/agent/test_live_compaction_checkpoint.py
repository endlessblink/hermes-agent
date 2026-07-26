import json
import threading
import time
from types import SimpleNamespace

import pytest

from agent.live_compaction_checkpoint import (
    LiveCompactionCheckpointStore,
    schedule_live_compaction_checkpoint,
)
from agent import conversation_compression
from agent.replay_cleanup import sanitize_replay_history


def _turn(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def test_checkpoint_apply_preserves_turns_appended_after_snapshot(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [
        _turn("user", "old request"),
        _turn("assistant", "old answer"),
    ]
    prepared = [
        _turn("user", "[CONTEXT COMPACTION] old work summarized"),
        _turn("assistant", "old answer"),
    ]
    store.publish("session-a", snapshot, prepared)

    newer = [
        _turn("user", "new request while summary was running"),
        _turn("assistant", "new answer"),
    ]
    applied = store.consume_if_current("session-a", snapshot + newer)

    assert applied == prepared + newer


def test_checkpoint_rejects_changed_snapshot_without_deleting_history(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [_turn("user", "original"), _turn("assistant", "answer")]
    store.publish("session-a", snapshot, [_turn("user", "summary")])

    changed = [_turn("user", "edited original"), _turn("assistant", "answer")]

    assert store.consume_if_current("session-a", changed) is None
    assert changed == [_turn("user", "edited original"), _turn("assistant", "answer")]


def test_stale_persisted_checkpoint_does_not_block_fresher_preparation(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    old_snapshot = [_turn("user", "old"), _turn("assistant", "answer")]
    store.publish("session-a", old_snapshot, [_turn("user", "old summary")])
    changed = [_turn("user", "corrected"), _turn("assistant", "answer")]

    assert schedule_live_compaction_checkpoint(
        store=store,
        session_id="session-a",
        messages=changed,
        prepare=lambda _messages: [_turn("user", "fresh summary")],
    )

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        applied = store.consume_if_current("session-a", changed)
        if applied is not None:
            break
        time.sleep(0.01)
    assert applied == [_turn("user", "fresh summary")]


def test_checkpoint_survives_process_restart(tmp_path):
    snapshot = [
        {**_turn("user", "request"), "_db_persisted": True},
        {**_turn("assistant", "answer"), "_db_persisted": True},
    ]
    prepared = [_turn("user", "summary"), _turn("assistant", "answer")]
    LiveCompactionCheckpointStore(tmp_path).publish("session-a", snapshot, prepared)

    reloaded = LiveCompactionCheckpointStore(tmp_path)

    restored_without_runtime_markers = [
        _turn("user", "request"),
        _turn("assistant", "answer"),
    ]
    assert (
        reloaded.consume_if_current("session-a", restored_without_runtime_markers)
        == prepared
    )


def test_checkpoint_survives_replay_cleanup_inside_protected_tail(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    tool_call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "terminal",
                    "arguments": '{"command":"restart service"}',
                },
            }
        ],
    }
    interrupted_result = {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "[Command interrupted] exit_code: 130",
    }
    snapshot = [
        _turn("user", "old request"),
        _turn("assistant", "old answer"),
        tool_call,
        interrupted_result,
    ]
    prepared = [
        _turn("user", "[CONTEXT COMPACTION] old work summarized"),
        tool_call,
        interrupted_result,
    ]
    store.publish("session-a", snapshot, prepared)

    recovered = sanitize_replay_history(snapshot)
    assert recovered[-1]["content"].startswith("[Orphan recovery:")

    assert store.consume_if_current("session-a", recovered) == [
        prepared[0],
        *recovered[2:],
    ]


def test_checkpoint_rejects_changed_tool_call_semantics(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [
        _turn("user", "inspect"),
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"a"}'},
                }
            ],
        },
    ]
    store.publish("session-a", snapshot, [_turn("user", "summary")])
    changed = json.loads(json.dumps(snapshot))
    changed[1]["tool_calls"][0]["function"]["arguments"] = '{"path":"b"}'

    assert store.consume_if_current("session-a", changed) is None


def test_checkpoint_rejects_changed_compaction_strategy(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [_turn("user", "request"), _turn("assistant", "answer")]
    store.publish(
        "session-a",
        snapshot,
        [_turn("user", "summary")],
        strategy_fingerprint="model-a-config-a",
    )

    assert (
        store.consume_if_current(
            "session-a",
            snapshot,
            strategy_fingerprint="model-b-config-a",
        )
        is None
    )
    assert store.peek("session-a") is None


def test_background_schedule_is_single_flight_and_returns_immediately(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [_turn("user", "request"), _turn("assistant", "answer")]
    started = threading.Event()
    release = threading.Event()
    calls = []

    def prepare(messages):
        calls.append(messages)
        started.set()
        assert release.wait(2)
        return [_turn("user", "prepared summary")]

    before = time.monotonic()
    assert schedule_live_compaction_checkpoint(
        store=store,
        session_id="session-a",
        messages=snapshot,
        prepare=prepare,
    )
    assert time.monotonic() - before < 0.2
    assert started.wait(1)

    assert not schedule_live_compaction_checkpoint(
        store=store,
        session_id="session-a",
        messages=snapshot,
        prepare=prepare,
    )
    release.set()

    deadline = time.monotonic() + 2
    while store.peek("session-a") is None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert len(calls) == 1
    assert json.loads(store.peek("session-a").read_text())["snapshot_length"] == 2
    assert not schedule_live_compaction_checkpoint(
        store=store,
        session_id="session-a",
        messages=snapshot,
        prepare=prepare,
    )


def test_foreground_uses_prepared_checkpoint_without_calling_summarizer(
    tmp_path, monkeypatch
):
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [_turn("user", "old"), _turn("assistant", "answer")]
    prepared = [_turn("user", "summary"), _turn("assistant", "answer")]
    store.publish("session-a", snapshot, prepared)
    compressor = SimpleNamespace(
        _last_compression_made_progress=False,
        _last_summary_fallback_used=False,
        _last_compress_aborted=False,
    )

    def fail_if_called(*args, **kwargs):
        pytest.fail("foreground summarizer must not run when a checkpoint is ready")

    compressor.compress = fail_if_called
    agent = SimpleNamespace(session_id="session-a", context_compressor=compressor)
    monkeypatch.setattr(
        conversation_compression, "_live_checkpoint_store", lambda _agent: store
    )

    newer = [_turn("user", "new"), _turn("assistant", "new answer")]
    result = conversation_compression._compress_messages(
        agent,
        snapshot + newer,
        approx_tokens=120,
        focus_topic=None,
        force=False,
    )

    assert result == prepared + newer
    assert compressor._last_compression_made_progress is True
    assert compressor._last_summary_fallback_used is False


def test_foreground_checkpoint_miss_uses_bounded_local_compaction(
    tmp_path, monkeypatch
):
    calls = []

    class Compressor:
        summary_mode = "llm"
        _last_summary_fallback_used = False
        _last_compress_aborted = False
        _last_summary_error = None
        _last_compression_made_progress = False
        compression_count = 0

        def compress(self, messages, **kwargs):
            calls.append((self.summary_mode, kwargs))
            if self.summary_mode != "drop":
                pytest.fail("checkpoint miss must not launch a foreground summary")
            return [_turn("user", "bounded local handoff"), messages[-1]]

    compressor = Compressor()
    agent = SimpleNamespace(
        session_id="session-a",
        platform="desktop",
        api_mode="codex_responses",
        context_compressor=compressor,
        compression_background_checkpoint_enabled=True,
    )
    monkeypatch.setattr(
        conversation_compression,
        "_live_checkpoint_store",
        lambda _agent: LiveCompactionCheckpointStore(tmp_path),
    )
    messages = [_turn("user", "old"), _turn("assistant", "recent exact state")]

    result = conversation_compression._compress_messages(
        agent,
        messages,
        approx_tokens=120,
        focus_topic=None,
        force=False,
    )

    assert result == [
        _turn("user", "bounded local handoff"),
        _turn("assistant", "recent exact state"),
    ]
    assert calls == [("drop", {"current_tokens": 120, "force": True})]
    assert compressor.summary_mode == "llm"


def test_background_does_not_publish_static_fallback_summary(tmp_path, monkeypatch):
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [_turn("user", "old"), _turn("assistant", "answer")]

    class FallbackCompressor:
        threshold_tokens = 100
        last_real_prompt_tokens = 70
        last_prompt_tokens = 70
        _last_summary_fallback_used = False
        _last_compress_aborted = False

        def compress(self, messages, **kwargs):
            self._last_summary_fallback_used = True
            return [_turn("user", "static fallback")]

    agent = SimpleNamespace(
        session_id="session-a",
        platform="desktop",
        context_compressor=FallbackCompressor(),
        compression_enabled=True,
        compression_background_checkpoint_enabled=True,
        compression_background_checkpoint_ratio=0.70,
    )
    monkeypatch.setattr(
        conversation_compression, "_live_checkpoint_store", lambda _agent: store
    )

    assert conversation_compression.schedule_background_compaction_checkpoint(
        agent, snapshot
    )

    deadline = time.monotonic() + 2
    while store.peek("session-a") is None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert store.peek("session-a") is None


def test_background_checkpoint_uses_fast_local_summary(monkeypatch, tmp_path):
    calls = []
    store = LiveCompactionCheckpointStore(tmp_path)

    class Compressor:
        threshold_tokens = 100
        last_real_prompt_tokens = 70
        last_prompt_tokens = 70
        summary_mode = "llm"
        _last_summary_fallback_used = False
        _last_compress_aborted = False
        _last_summary_error = None

        def compress(self, messages, **kwargs):
            calls.append((self.summary_mode, kwargs))
            return [_turn("user", "prepared locally")]

    agent = SimpleNamespace(
        session_id="session-a",
        platform="desktop",
        context_compressor=Compressor(),
        compression_enabled=True,
        compression_background_checkpoint_enabled=True,
        compression_background_checkpoint_ratio=0.70,
    )
    monkeypatch.setattr(
        conversation_compression, "_live_checkpoint_store", lambda _agent: store
    )

    assert conversation_compression.schedule_background_compaction_checkpoint(
        agent, [_turn("user", "old"), _turn("assistant", "answer")]
    )

    deadline = time.monotonic() + 2
    while store.peek("session-a") is None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert calls == [("drop", {"current_tokens": 70, "force": True})]
    assert agent.context_compressor.summary_mode == "llm"
    assert store.peek("session-a") is not None


def test_foreground_hydrates_summary_state_from_prepared_checkpoint(
    tmp_path, monkeypatch
):
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [_turn("user", "old"), _turn("assistant", "answer")]
    prepared = [
        _turn("system", "stable prompt"),
        _turn("user", "[CONTEXT COMPACTION]\nfresh prepared summary"),
        _turn("assistant", "answer"),
    ]
    store.publish("session-a", snapshot, prepared)

    class StatefulCompressor:
        _previous_summary = "stale summary"
        _last_compression_made_progress = False
        _last_summary_fallback_used = False
        _last_compress_aborted = False
        compression_count = 0

        @staticmethod
        def _find_latest_context_summary(messages):
            return 1, "fresh prepared summary"

        def compress(self, *args, **kwargs):
            pytest.fail("prepared checkpoint must avoid foreground summarization")

    compressor = StatefulCompressor()
    agent = SimpleNamespace(session_id="session-a", context_compressor=compressor)
    monkeypatch.setattr(
        conversation_compression, "_live_checkpoint_store", lambda _agent: store
    )

    result = conversation_compression._compress_messages(
        agent,
        snapshot,
        approx_tokens=120,
        focus_topic=None,
        force=False,
    )

    assert result == prepared
    assert compressor._previous_summary == "fresh prepared summary"


def test_post_turn_scheduler_starts_only_above_soft_watermark(tmp_path, monkeypatch):
    calls = []
    store = LiveCompactionCheckpointStore(tmp_path)
    compressor = SimpleNamespace(
        threshold_tokens=100,
        last_real_prompt_tokens=69,
        last_prompt_tokens=69,
        protect_last_n=10,
    )
    agent = SimpleNamespace(
        session_id="session-a",
        platform="desktop",
        context_compressor=compressor,
        compression_enabled=True,
        compression_background_checkpoint_enabled=True,
        compression_background_checkpoint_ratio=0.70,
    )
    monkeypatch.setattr(
        conversation_compression, "_live_checkpoint_store", lambda _agent: store
    )
    monkeypatch.setattr(
        conversation_compression,
        "schedule_live_compaction_checkpoint",
        lambda **kwargs: calls.append(kwargs) or True,
    )

    assert not conversation_compression.schedule_background_compaction_checkpoint(
        agent, [_turn("user", "one"), _turn("assistant", "two")]
    )
    compressor.last_real_prompt_tokens = 70
    assert conversation_compression.schedule_background_compaction_checkpoint(
        agent, [_turn("user", "one"), _turn("assistant", "two")]
    )
    assert len(calls) == 1
