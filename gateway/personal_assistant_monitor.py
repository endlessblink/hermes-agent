"""Telegram delivery bridge for personal-assistant monitor candidates.

The producer in :mod:`agent.personal_assistant_monitor` remains the sole owner
of event identity, leases, retry policy, and deduplication.  This module only
turns one leased event into a visible message through the gateway's existing
delivery router.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from agent.personal_assistant_monitor import (
    defer_candidate_event,
    record_monitor_consumer_owner,
    lease_candidate_event,
    record_monitor_health,
    retry_candidate_event,
    settle_candidate_event,
)
from gateway.config import GatewayConfig, HomeChannel, Platform
from gateway.delivery import DeliveryRouter, DeliveryTarget


logger = logging.getLogger(__name__)


def _route_value(mapping: Any, key: str) -> Any:
    if not isinstance(mapping, Mapping):
        return None
    if key in mapping:
        return mapping[key]
    return next(
        (value for candidate, value in mapping.items() if str(candidate) == key),
        None,
    )


def _office_work_target(
    config: GatewayConfig,
    home: HomeChannel,
) -> DeliveryTarget | None:
    chat_id = str(home.chat_id)
    thread_id = str(home.thread_id) if home.thread_id else None
    target = DeliveryTarget(
        platform=Platform.TELEGRAM,
        chat_id=chat_id,
        thread_id=thread_id,
        is_explicit=True,
    )
    if not config.multiplex_profiles:
        return target

    all_routes = config.profile_routes
    telegram_routes = _route_value(all_routes, Platform.TELEGRAM.value)
    if not isinstance(telegram_routes, Mapping) or not telegram_routes:
        logger.error(
            "Personal-assistant Telegram delivery requires office-work profile routes"
        )
        return None

    topic_map = _route_value(telegram_routes.get("topics"), chat_id)
    if thread_id is not None:
        routed_profile = _route_value(topic_map, thread_id)
    else:
        routed_profile = _route_value(telegram_routes.get("chats"), chat_id)
    if routed_profile == "office-work":
        return target

    office_topics = sorted(
        {
            str(candidate_thread)
            for candidate_thread, profile in (
                topic_map.items() if isinstance(topic_map, Mapping) else ()
            )
            if profile == "office-work"
        }
    )
    if len(office_topics) != 1:
        logger.error(
            "Personal-assistant Telegram target is not uniquely routed to office-work "
            "(chat=%s, configured_thread=%s, candidate_count=%s)",
            chat_id,
            thread_id,
            len(office_topics),
        )
        return None

    corrected_thread = office_topics[0]
    logger.warning(
        "Corrected stale personal-assistant Telegram topic %s to office-work topic %s",
        thread_id,
        corrected_thread,
    )
    return DeliveryTarget(
        platform=Platform.TELEGRAM,
        chat_id=chat_id,
        thread_id=corrected_thread,
        is_explicit=True,
    )


def _format_event(event: dict[str, Any]) -> str:
    kind = str(event.get("kind") or "context_change")
    evidence = event.get("evidence") if isinstance(event.get("evidence"), dict) else {}
    title = str(evidence.get("title") or "").strip()

    if kind == "deadline_risk":
        count = int(evidence.get("overdue") or 0)
        noun = "task" if count == 1 else "tasks"
        return f"⚠️ FlowState update: {count} overdue {noun} need attention."
    if kind == "material_schedule_drift":
        minutes = abs(int(evidence.get("minutes") or 0))
        return f"🗓️ FlowState update: your schedule has drifted by {minutes} minutes."
    if kind == "blocker":
        subject = f" “{title}”" if title else ""
        return f"🚧 FlowState update:{subject} is now blocked."
    if kind == "changed_high_priority":
        subject = f" “{title}”" if title else " A task"
        return f"🔴 FlowState update:{subject} is now high priority."
    if kind == "uncategorized_tasks":
        count = int(evidence.get("count") or 0)
        noun = "task" if count == 1 else "tasks"
        return f"📥 FlowState update: {count} uncategorized {noun} may need organizing."
    if kind == "scheduled_assessment":
        return "☀️ Daily planning check: review today’s priorities and schedule."
    return "🔔 Your personal assistant detected a consequential context change."


class PersonalAssistantTelegramMonitorBridge:
    """Lease and visibly deliver one monitor event at a time to Telegram."""

    def __init__(
        self,
        profile_home: Path,
        config: GatewayConfig,
        delivery_router: DeliveryRouter,
        *,
        is_busy: Callable[[], bool] | None = None,
        poll_interval: float = 5.0,
    ) -> None:
        self.profile_home = Path(profile_home)
        self.config = config
        self.delivery_router = delivery_router
        self.is_busy = is_busy or (lambda: False)
        self.poll_interval = max(0.1, float(poll_interval))

    def _target(self) -> DeliveryTarget | None:
        home = self.config.get_home_channel(Platform.TELEGRAM)
        if home is None or not home.chat_id:
            return None
        return _office_work_target(self.config, home)

    async def deliver_once(self, *, now: datetime | None = None) -> bool:
        target = self._target()
        if target is None:
            return False
        record_monitor_consumer_owner(
            self.profile_home,
            "telegram-gateway",
            now=now,
        )

        event = lease_candidate_event(
            self.profile_home,
            "telegram-gateway",
            now=now,
        )
        if event is None:
            return False

        event_id = str(event.get("id") or "")
        lease_id = str(event.get("lease_id") or "")
        if event.get("kind") == "scheduled_assessment":
            settle_candidate_event(
                self.profile_home,
                event_id,
                lease_id,
                "suppressed",
            )
            record_monitor_health(
                self.profile_home,
                component="consumer",
                event="daily_assessment_suppressed",
                status="available",
                count=1,
                now=now,
            )
            return False
        if self.is_busy():
            defer_candidate_event(
                self.profile_home,
                event_id,
                lease_id,
                now=now,
            )
            record_monitor_health(
                self.profile_home,
                component="consumer",
                event="events_deferred",
                status="retry_wait",
                count=1,
                now=now,
            )
            return False

        result = await self.delivery_router.deliver(
            _format_event(event),
            [target],
            metadata={
                "personal_assistant_monitor": True,
                "event_id": event_id,
            },
        )
        delivered = bool(result.get(target.to_string(), {}).get("success"))
        if delivered:
            settled = settle_candidate_event(
                self.profile_home,
                event_id,
                lease_id,
                "handled",
            )
            record_monitor_health(
                self.profile_home,
                component="consumer",
                event="event_handled",
                status="available",
                count=1,
                now=now,
            )
            return settled

        retry_candidate_event(
            self.profile_home,
            event_id,
            lease_id,
            {"category": "delivery_failed", "code": "telegram_send_failed"},
            now=now,
        )
        record_monitor_health(
            self.profile_home,
            component="consumer",
            event="delivery_failed",
            status="retry_wait",
            count=1,
            now=now,
        )
        return False

    async def run(self, keep_running: Callable[[], bool]) -> None:
        """Poll until the owning gateway stops."""
        while keep_running():
            try:
                await self.deliver_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Personal-assistant Telegram monitor delivery failed",
                    exc_info=True,
                )
            await asyncio.sleep(self.poll_interval)


def create_personal_assistant_telegram_monitor_bridge(
    *,
    profile_name: str,
    profile_home: Path,
    config: GatewayConfig,
    delivery_router: DeliveryRouter,
    is_busy: Callable[[], bool] | None = None,
) -> PersonalAssistantTelegramMonitorBridge | None:
    """Return the bridge only for the dedicated office-work Telegram gateway."""
    if profile_name != "office-work":
        return None
    home = config.get_home_channel(Platform.TELEGRAM)
    if home is None or not home.chat_id:
        return None
    if Platform.TELEGRAM not in delivery_router.adapters:
        return None
    return PersonalAssistantTelegramMonitorBridge(
        profile_home,
        config,
        delivery_router,
        is_busy=is_busy,
    )
