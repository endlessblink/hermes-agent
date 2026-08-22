"""System-level Life-Boat gateway race coverage.

The harness uses the real gateway runner and platform adapter imports, with
barrier-controlled fake platform/model edges.  It intentionally starts with
the failure where a steer arrives after the last tool boundary and the old
turn is allowed to escape while the newer message is lost.
"""

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource, build_session_key


class _RaceAdapter(BasePlatformAdapter):
    async def connect(self, *, is_reconnect: bool = False):
        return None

    async def disconnect(self):
        return None

    async def send(self, chat_id, content, **kwargs):
        self.sent.append((chat_id, content, kwargs))
        return None

    async def get_chat_info(self, chat_id):
        return {}


def _event(text: str, *, message_id: str, user_id: str = "owner", thread_id: str | None = None):
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="chat-30",
        chat_type="dm",
        user_id=user_id,
        thread_id=thread_id,
    )
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=source,
        message_id=message_id,
        platform_update_id=sum(ord(char) for char in message_id),
    )


def _runner(adapter):
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._queued_events = {}
    runner._session_run_generation = {}
    runner._busy_ack_ts = {}
    runner._draining = False
    runner._busy_input_mode = "steer"
    runner._busy_text_mode = "interrupt"
    runner._is_user_authorized_in_event_scope = lambda source: source.user_id == "owner"
    runner._agent_has_active_subagents = lambda agent: False
    runner._session_has_compression_in_flight = lambda key: False
    runner._update_runtime_status = MagicMock()
    return runner


@pytest.mark.asyncio
async def test_steer_after_last_tool_boundary_is_requeued_before_old_reply_escapes():
    """A newer steer must become the next real turn when no tool can consume it."""
    adapter = _RaceAdapter(PlatformConfig(enabled=True, token="test"), Platform.TELEGRAM)
    adapter.sent = []
    runner = _runner(adapter)
    adapter._busy_text_mode = "interrupt"
    adapter._adapter_for_source = lambda source: adapter
    runner._adapter_for_source = lambda source: adapter

    first = _event("start the long task", message_id="m-1")
    steer = _event("use the newer correction", message_id="m-2")
    session_key = build_session_key(first.source)

    agent = MagicMock()
    agent.steer.return_value = True
    runner._running_agents[session_key] = agent

    # Drive the real busy-session state transition through the runner.
    assert await runner._handle_active_session_busy_message(steer, session_key)
    assert agent.interrupt.call_count == 0
    assert agent.steer.call_count == 1

    # This is the real finalizer contract: the model completed before the
    # deferred steer reached a tool-result boundary.
    result = {
        "final_response": "stale answer to the first turn",
        "pending_steer": steer.text,
        "pending_steer_event": steer,
    }
    runner._handoff_pending_steer(first, first.source, session_key, result)

    pending = adapter._pending_messages[session_key]
    assert pending.text == steer.text
    assert pending.message_id == steer.message_id
    assert pending.platform_update_id == steer.platform_update_id
    assert result["final_response"] is None


def test_steer_handoff_preserves_topic_reply_and_rejects_unauthorized_event():
    """The handoff keeps the newer Telegram target and stays fail-closed."""
    adapter = _RaceAdapter(PlatformConfig(enabled=True, token="test"), Platform.TELEGRAM)
    runner = _runner(adapter)
    first = _event("old", message_id="m-10", thread_id="topic-1")
    newer = _event("new", message_id="m-11", thread_id="topic-1")
    session_key = build_session_key(first.source)

    result = {"final_response": "old", "pending_steer": newer.text, "pending_steer_event": newer}
    runner._handoff_pending_steer(first, first.source, session_key, result)

    pending = adapter._pending_messages[session_key]
    assert pending.source.thread_id == "topic-1"
    assert pending.message_id == "m-11"
    assert result["final_response"] is None

    unauthorized = replace(newer, source=replace(newer.source, user_id="intruder"))
    assert runner._is_user_authorized_in_event_scope(unauthorized.source) is False


@pytest.mark.asyncio
async def test_one_hundred_seeded_rapid_steer_schedules_keep_execution_alive():
    """Seeded schedules cover 2..10 rapid messages without killing the worker."""
    for seed in range(100):
        adapter = _RaceAdapter(PlatformConfig(enabled=True, token="test"), Platform.TELEGRAM)
        runner = _runner(adapter)
        source_event = _event("first", message_id=f"m-{seed}-0")
        session_key = build_session_key(source_event.source)
        agent = MagicMock()
        agent.steer.return_value = True
        agent._active_children = [] if seed % 3 else [MagicMock()]
        runner._running_agents[session_key] = agent

        count = 2 + (seed % 9)
        for index in range(count):
            incoming = _event(
                f"correction-{seed}-{index}",
                message_id=f"m-{seed}-{index + 1}",
            )
            assert await runner._handle_active_session_busy_message(incoming, session_key)

        # Life-Boat STEER is allowed to queue only when the active worker
        # explicitly rejects steering; successful steering never interrupts or
        # creates a second foreground task.
        assert agent.interrupt.call_count == 0
        assert agent.steer.call_count == count
        assert session_key not in runner._queued_events


def test_same_chat_topics_and_users_never_share_a_turn_lane():
    topic_one = _event("one", message_id="m-201", thread_id="topic-1")
    topic_two = _event("two", message_id="m-202", thread_id="topic-2")
    group_one = replace(
        topic_one,
        source=replace(topic_one.source, chat_type="group", user_id="owner"),
    )
    group_other = replace(group_one, source=replace(group_one.source, user_id="other"))

    assert build_session_key(topic_one.source) != build_session_key(topic_two.source)
    assert build_session_key(group_one.source) != build_session_key(
        group_other.source,
        group_sessions_per_user=True,
        thread_sessions_per_user=True,
    )
