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


def _turn(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def test_tool_iteration_checkpoint_hook_passes_complete_tool_result(monkeypatch):
    from agent.conversation_loop import _schedule_checkpoint_after_tool_iteration

    calls = []
    monkeypatch.setattr(
        conversation_compression,
        "schedule_background_compaction_checkpoint",
        lambda agent, messages: calls.append((agent, messages)) or True,
    )
    agent = SimpleNamespace()
    messages = [
        _turn("user", "search"),
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "search result"},
    ]

    assert _schedule_checkpoint_after_tool_iteration(agent, messages)
    assert calls == [(agent, messages)]


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
        {**_turn("user", "request"), "timestamp": 1784491628.0},
        {**_turn("assistant", "answer"), "timestamp": 1784491629.0},
    ]
    assert (
        reloaded.consume_if_current("session-a", restored_without_runtime_markers)
        == prepared
    )


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


def test_large_snapshot_scheduling_stays_off_the_visible_reply_path(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [
        _turn("user" if index % 2 == 0 else "assistant", "x" * 400)
        for index in range(2500)
    ]
    started = threading.Event()
    release = threading.Event()

    def prepare(_messages):
        started.set()
        assert release.wait(2)
        return [_turn("user", "prepared")]

    before = time.monotonic()
    assert schedule_live_compaction_checkpoint(
        store=store,
        session_id="large-session",
        messages=snapshot,
        prepare=prepare,
    )
    assert time.monotonic() - before < 0.2
    assert started.wait(1)
    release.set()


def test_older_worker_cannot_overwrite_newer_checkpoint(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    older = [_turn("user", "one"), _turn("assistant", "one answer")]
    newer = older + [_turn("user", "two"), _turn("assistant", "two answer")]
    old_started = threading.Event()
    release_old = threading.Event()

    def prepare_old(_messages):
        old_started.set()
        assert release_old.wait(2)
        return [_turn("user", "old prepared")]

    assert schedule_live_compaction_checkpoint(
        store=store,
        session_id="session-a",
        messages=older,
        prepare=prepare_old,
    )
    assert old_started.wait(1)

    store.publish("session-a", newer, [_turn("user", "new prepared")])
    release_old.set()
    time.sleep(0.1)

    assert store.consume_if_current("session-a", newer) == [
        _turn("user", "new prepared")
    ]


def test_background_preparation_is_globally_bounded(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [_turn("user", "one"), _turn("assistant", "answer")]
    started = threading.Event()
    release = threading.Event()

    def blocking_prepare(_messages):
        started.set()
        assert release.wait(2)
        return [_turn("user", "prepared")]

    assert schedule_live_compaction_checkpoint(
        store=store,
        session_id="session-a",
        messages=snapshot,
        prepare=blocking_prepare,
    )
    assert started.wait(1)

    before = time.monotonic()
    assert not schedule_live_compaction_checkpoint(
        store=store,
        session_id="session-b",
        messages=snapshot,
        prepare=lambda _messages: [_turn("user", "other")],
    )
    assert time.monotonic() - before < 0.2

    release.set()
    deadline = time.monotonic() + 2
    while store.peek("session-a") is None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert schedule_live_compaction_checkpoint(
        store=store,
        session_id="session-b",
        messages=snapshot,
        prepare=lambda _messages: [_turn("user", "other")],
    )


def test_refresh_keeps_last_good_checkpoint_until_replacement_ready(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    older = [_turn("user", "one"), _turn("assistant", "one answer")]
    newer = older + [_turn("user", "two"), _turn("assistant", "two answer")]
    store.publish(
        "session-a",
        older,
        [_turn("user", "old prepared")],
        snapshot_tokens=70,
    )
    refresh_started = threading.Event()
    release_refresh = threading.Event()

    def prepare_refresh(_messages):
        refresh_started.set()
        assert release_refresh.wait(2)
        return [_turn("user", "fresh prepared")]

    assert schedule_live_compaction_checkpoint(
        store=store,
        session_id="session-a",
        messages=newer,
        prepare=prepare_refresh,
        replace_current=True,
        snapshot_tokens=85,
    )
    assert refresh_started.wait(1)
    assert store.is_current("session-a", newer)

    release_refresh.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        coverage = store.current_coverage("session-a", newer)
        if coverage and coverage["snapshot_tokens"] == 85:
            break
        time.sleep(0.01)

    assert store.consume_if_current("session-a", newer) == [
        _turn("user", "fresh prepared")
    ]


def test_refresh_cannot_publish_after_last_good_checkpoint_was_consumed(tmp_path):
    store = LiveCompactionCheckpointStore(tmp_path)
    older = [_turn("user", "one"), _turn("assistant", "one answer")]
    newer = older + [_turn("user", "two"), _turn("assistant", "two answer")]
    store.publish(
        "session-a",
        older,
        [_turn("user", "old prepared")],
        snapshot_tokens=70,
    )
    coverage = store.current_coverage("session-a", newer)
    started = threading.Event()
    release = threading.Event()

    def prepare_refresh(_messages):
        started.set()
        assert release.wait(2)
        return [_turn("user", "fresh prepared")]

    assert schedule_live_compaction_checkpoint(
        store=store,
        session_id="session-a",
        messages=newer,
        prepare=prepare_refresh,
        replace_current=True,
        expected_record_id=coverage["record_id"],
        snapshot_tokens=85,
    )
    assert started.wait(1)
    assert store.consume_if_current("session-a", newer) == [
        _turn("user", "old prepared"),
        *newer[len(older):],
    ]

    release.set()
    time.sleep(0.1)
    assert store.peek("session-a") is None


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


def test_scheduler_accounts_for_new_tool_output_not_in_last_provider_usage(
    tmp_path, monkeypatch
):
    calls = []
    store = LiveCompactionCheckpointStore(tmp_path)
    compressor = SimpleNamespace(
        threshold_tokens=100,
        last_real_prompt_tokens=40,
        last_prompt_tokens=40,
        protect_last_n=10,
    )
    agent = SimpleNamespace(
        session_id="session-a",
        platform="desktop",
        context_compressor=compressor,
        compression_enabled=True,
        compression_background_checkpoint_enabled=True,
        compression_background_checkpoint_ratio=0.50,
        tools=[],
        _cached_system_prompt="system",
    )
    monkeypatch.setattr(
        conversation_compression, "_live_checkpoint_store", lambda _agent: store
    )
    monkeypatch.setattr(
        conversation_compression,
        "estimate_request_tokens_rough",
        lambda *args, **kwargs: 60,
    )
    monkeypatch.setattr(
        conversation_compression,
        "schedule_live_compaction_checkpoint",
        lambda **kwargs: calls.append(kwargs) or True,
    )

    assert conversation_compression.schedule_background_compaction_checkpoint(
        agent,
        [
            _turn("user", "one"),
            _turn("assistant", "tool call"),
            _turn("tool", "large new output"),
        ],
    )
    assert calls[0]["snapshot_tokens"] == 60


def test_post_turn_scheduler_refreshes_checkpoint_after_pressure_growth(
    tmp_path, monkeypatch
):
    calls = []
    store = LiveCompactionCheckpointStore(tmp_path)
    snapshot = [_turn("user", "one"), _turn("assistant", "two")]
    store.publish(
        "session-a",
        snapshot,
        [_turn("user", "prepared")],
        snapshot_tokens=70,
    )
    compressor = SimpleNamespace(
        threshold_tokens=100,
        last_real_prompt_tokens=85,
        last_prompt_tokens=85,
        protect_last_n=10,
    )
    agent = SimpleNamespace(
        session_id="session-a",
        platform="desktop",
        context_compressor=compressor,
        compression_enabled=True,
        compression_background_checkpoint_enabled=True,
        compression_background_checkpoint_ratio=0.70,
        compression_background_checkpoint_refresh_ratio=0.10,
    )
    monkeypatch.setattr(
        conversation_compression, "_live_checkpoint_store", lambda _agent: store
    )
    monkeypatch.setattr(
        conversation_compression,
        "schedule_live_compaction_checkpoint",
        lambda **kwargs: calls.append(kwargs) or True,
    )

    assert conversation_compression.schedule_background_compaction_checkpoint(
        agent, snapshot
    )
    assert calls[0]["replace_current"] is True
    assert calls[0]["snapshot_tokens"] == 85
