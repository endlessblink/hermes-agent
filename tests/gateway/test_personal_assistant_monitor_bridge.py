import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from agent.personal_assistant_monitor import run_monitor_check
from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
from gateway.delivery import DeliveryRouter
from gateway.personal_assistant_monitor import (
    PersonalAssistantTelegramMonitorBridge,
    create_personal_assistant_telegram_monitor_bridge,
)
from gateway.platforms.base import SendResult
from gateway.run import GatewayRunner


NOW = datetime(2026, 7, 18, 4, 0, tzinfo=timezone.utc)


class RecordingTelegramAdapter:
    splits_long_messages = True

    def __init__(self, results=None):
        self.results = list(results or [SendResult(success=True)])
        self.sent = []

    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        return self.results.pop(0)


def configured_gateway(adapter):
    config = GatewayConfig()
    config.platforms[Platform.TELEGRAM] = PlatformConfig(enabled=True)
    config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM,
        chat_id="424242",
        name="Personal assistant",
    )
    router = DeliveryRouter(config, adapters={Platform.TELEGRAM: adapter})
    return config, router


def enqueue_deadline_risk(profile_home):
    run_monitor_check(profile_home, {"taskPressure": {"overdue": 0}}, now=NOW)
    run_monitor_check(
        profile_home,
        {"taskPressure": {"overdue": 1}},
        now=NOW + timedelta(minutes=15),
    )


def enqueue_scheduled_assessment(profile_home):
    run_monitor_check(
        profile_home,
        {"taskPressure": {"overdue": 0}},
        now=NOW.replace(hour=6),
    )


def queued_event(profile_home):
    queue_path = profile_home / "state" / "personal-assistant-monitor" / "queue.json"
    return json.loads(queue_path.read_text(encoding="utf-8"))["events"][0]


def test_bridge_starts_only_for_office_work_profile(tmp_path):
    adapter = RecordingTelegramAdapter()
    config, router = configured_gateway(adapter)

    assert (
        create_personal_assistant_telegram_monitor_bridge(
            profile_name="default",
            profile_home=tmp_path,
            config=config,
            delivery_router=router,
        )
        is None
    )
    assert isinstance(
        create_personal_assistant_telegram_monitor_bridge(
            profile_name="office-work",
            profile_home=tmp_path,
            config=config,
            delivery_router=router,
        ),
        PersonalAssistantTelegramMonitorBridge,
    )


@pytest.mark.asyncio
async def test_bridge_delivers_monitor_event_to_telegram_home(tmp_path):
    enqueue_deadline_risk(tmp_path)
    adapter = RecordingTelegramAdapter()
    config, router = configured_gateway(adapter)
    bridge = PersonalAssistantTelegramMonitorBridge(tmp_path, config, router)

    assert await bridge.deliver_once(now=NOW + timedelta(minutes=16)) is True

    assert len(adapter.sent) == 1
    chat_id, content, metadata = adapter.sent[0]
    assert chat_id == "424242"
    assert "1 overdue task" in content
    assert metadata["personal_assistant_monitor"] is True


@pytest.mark.asyncio
async def test_bridge_settles_only_after_visible_delivery_success(tmp_path):
    enqueue_deadline_risk(tmp_path)
    adapter = RecordingTelegramAdapter([SendResult(success=True)])
    config, router = configured_gateway(adapter)
    bridge = PersonalAssistantTelegramMonitorBridge(tmp_path, config, router)

    await bridge.deliver_once(now=NOW + timedelta(minutes=16))

    event = queued_event(tmp_path)
    assert event["status"] == "handled"
    assert event["disposition"] == "handled"
    assert event["lease"] is None


@pytest.mark.asyncio
async def test_bridge_releases_failed_delivery_for_retry(tmp_path):
    enqueue_deadline_risk(tmp_path)
    adapter = RecordingTelegramAdapter([
        SendResult(success=False, error="temporary Telegram outage")
    ])
    config, router = configured_gateway(adapter)
    bridge = PersonalAssistantTelegramMonitorBridge(tmp_path, config, router)

    assert await bridge.deliver_once(now=NOW + timedelta(minutes=16)) is False

    event = queued_event(tmp_path)
    assert event["status"] == "retry_wait"
    assert event["acked"] is False
    assert event["lease"] is None
    assert event["last_error"]["category"] == "delivery_failed"
    assert "temporary Telegram outage" not in json.dumps(event)


@pytest.mark.asyncio
async def test_bridge_defers_while_agent_is_busy_without_spending_attempt(tmp_path):
    enqueue_deadline_risk(tmp_path)
    adapter = RecordingTelegramAdapter([SendResult(success=True)])
    config, router = configured_gateway(adapter)
    bridge = PersonalAssistantTelegramMonitorBridge(
        tmp_path,
        config,
        router,
        is_busy=lambda: True,
    )

    assert await bridge.deliver_once(now=NOW + timedelta(minutes=16)) is False

    event = queued_event(tmp_path)
    assert adapter.sent == []
    assert event["status"] == "retry_wait"
    assert event["attempts"] == 0
    assert event["lease"] is None


@pytest.mark.asyncio
async def test_bridge_does_not_redeliver_a_settled_event(tmp_path):
    enqueue_deadline_risk(tmp_path)
    adapter = RecordingTelegramAdapter([SendResult(success=True)])
    config, router = configured_gateway(adapter)
    bridge = PersonalAssistantTelegramMonitorBridge(tmp_path, config, router)

    assert await bridge.deliver_once(now=NOW + timedelta(minutes=16)) is True
    assert await bridge.deliver_once(now=NOW + timedelta(minutes=17)) is False
    assert len(adapter.sent) == 1


@pytest.mark.asyncio
async def test_bridge_suppresses_daily_assessment_owned_by_morning_cron(tmp_path):
    enqueue_scheduled_assessment(tmp_path)
    adapter = RecordingTelegramAdapter([SendResult(success=True)])
    config, router = configured_gateway(adapter)
    bridge = PersonalAssistantTelegramMonitorBridge(tmp_path, config, router)

    assert await bridge.deliver_once(now=NOW.replace(hour=6, minute=1)) is False

    event = queued_event(tmp_path)
    assert adapter.sent == []
    assert event["status"] == "suppressed"
    assert event["disposition"] == "suppressed"


@pytest.mark.asyncio
async def test_runner_schedules_office_work_monitor_bridge(monkeypatch, tmp_path):
    started = asyncio.Event()

    class FakeBridge:
        async def run(self, keep_running):
            assert keep_running() is True
            started.set()

    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config, runner.delivery_router = configured_gateway(
        RecordingTelegramAdapter()
    )
    runner._running = True
    runner._running_agents = {}
    runner._background_tasks = set()
    monkeypatch.setattr(runner, "_active_profile_name", lambda: "office-work")
    monkeypatch.setattr(
        "gateway.personal_assistant_monitor.create_personal_assistant_telegram_monitor_bridge",
        lambda **kwargs: FakeBridge(),
    )

    assert runner._start_personal_assistant_telegram_monitor_bridge(tmp_path) is True
    await asyncio.wait_for(started.wait(), timeout=1)
    assert runner._personal_assistant_telegram_monitor_task in runner._background_tasks


@pytest.mark.asyncio
async def test_default_multiplexer_schedules_office_work_monitor_bridge(
    monkeypatch, tmp_path
):
    office_home = tmp_path / "profiles" / "office-work"
    office_home.mkdir(parents=True)
    started = asyncio.Event()
    seen = {}

    class FakeBridge:
        async def run(self, keep_running):
            started.set()

    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config, runner.delivery_router = configured_gateway(
        RecordingTelegramAdapter()
    )
    runner.config.multiplex_profiles = True
    runner.config.multiplex_served_profiles = ["life-advisor", "office-work"]
    runner._running = True
    runner._running_agents = {}
    runner._background_tasks = set()
    monkeypatch.setattr(runner, "_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        "hermes_cli.profiles.profiles_to_serve",
        lambda **_kwargs: [("default", tmp_path), ("office-work", office_home)],
    )

    def create_bridge(**kwargs):
        seen.update(kwargs)
        return FakeBridge()

    monkeypatch.setattr(
        "gateway.personal_assistant_monitor.create_personal_assistant_telegram_monitor_bridge",
        create_bridge,
    )

    assert runner._start_personal_assistant_telegram_monitor_bridge(tmp_path) is True
    await asyncio.wait_for(started.wait(), timeout=1)
    assert seen["profile_name"] == "office-work"
    assert seen["profile_home"] == office_home
