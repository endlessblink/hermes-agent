"""Broker for agent → pinned-browser-tab control.

The browser extension already pushes page context *up* to Hermes. This module
is the missing path back *down*: an agent tool submits a command, the extension
long-polls for it, executes it against the tab the user pinned in the side
panel, and posts the result back.

Why a broker instead of reusing ``browser_*``: those tools drive a separate
headless/cloud browser session, which is a different browser with different
cookies — it hits login walls and bot challenges on pages the user is already
authenticated to. Commands here run in the user's real, already-authenticated
tab, and never silently fall back to the headless path.

Threading model. Tool handlers run synchronously on the agent's thread while
the HTTP routes run on the aiohttp event loop. Everything here is plain
``threading`` (no loop affinity): the async side waits via
``run_in_executor``, so the broker can be created before any loop exists and
survives loop restarts.

Security envelope enforced here (not in the HTTP layer, so tests cover it):
  - a command is only ever delivered to the channel that owns the target tab
  - a channel must re-announce its pinned tab; a stale tab id fails loudly
  - results are matched by opaque command id and consumed exactly once
  - every wait is bounded, so a disconnected extension fails fast and visibly
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# A channel that has not polled or heartbeat within this window is considered
# gone. Long-polls refresh it, so this only trips when the panel is closed.
CHANNEL_TTL_SECONDS = 90.0

# Upper bound on how long a tool blocks waiting for the extension. The
# extension's own executor deadline must stay below this so a slow page
# surfaces as an extension-side error rather than a broker timeout.
DEFAULT_COMMAND_TIMEOUT_SECONDS = 30.0
MAX_COMMAND_TIMEOUT_SECONDS = 120.0

# Bound the queue so a wedged extension cannot grow memory without limit.
MAX_PENDING_COMMANDS = 32


class BrowserControlError(RuntimeError):
    """A control command could not be delivered or completed."""


class NoBrowserChannelError(BrowserControlError):
    """No extension channel is connected for the requested owner."""


class TabMismatchError(BrowserControlError):
    """The requested tab is not the one the connected channel has pinned."""


class CommandTimeoutError(BrowserControlError):
    """The extension did not return a result before the deadline."""


@dataclass
class _Command:
    id: str
    action: str
    params: Dict[str, Any]
    tab_id: int
    created_at: float
    done: threading.Event = field(default_factory=threading.Event)
    result: Optional[Dict[str, Any]] = None
    dispatched: bool = False


@dataclass
class _Channel:
    """One connected side panel, pinned to exactly one tab."""

    channel_id: str
    tab_id: Optional[int] = None
    window_id: Optional[int] = None
    url: str = ""
    title: str = ""
    last_seen: float = field(default_factory=monotonic)
    pending: List[_Command] = field(default_factory=list)

    def alive(self, now: Optional[float] = None) -> bool:
        return (now or monotonic()) - self.last_seen <= CHANNEL_TTL_SECONDS


class BrowserControlBroker:
    """Routes control commands to the connected extension side panel."""

    def __init__(self) -> None:
        self._lock = threading.Condition()
        self._channels: Dict[str, _Channel] = {}
        self._inflight: Dict[str, _Command] = {}

    # ── extension side ────────────────────────────────────────────────

    def announce(
        self,
        channel_id: str,
        *,
        tab_id: Optional[int] = None,
        window_id: Optional[int] = None,
        url: str = "",
        title: str = "",
    ) -> Dict[str, Any]:
        """Register/refresh a channel and the tab it currently has pinned.

        Called on side-panel connect, on every pin change, and as a heartbeat.
        Re-announcing with a different tab id cancels nothing already queued —
        queued commands carry their own tab id and fail on mismatch at delivery,
        which is what makes a mid-flight tab switch visible instead of silently
        acting on the wrong page.
        """
        clean_id = _clean_channel_id(channel_id)
        with self._lock:
            channel = self._channels.get(clean_id)
            if channel is None:
                channel = _Channel(channel_id=clean_id)
                self._channels[clean_id] = channel
            channel.tab_id = _clean_tab_id(tab_id)
            channel.window_id = _clean_tab_id(window_id)
            channel.url = str(url or "")[:2048]
            channel.title = str(title or "")[:512]
            channel.last_seen = monotonic()
            self._lock.notify_all()
            return {
                "channel_id": channel.channel_id,
                "tab_id": channel.tab_id,
                "connected": True,
            }

    def disconnect(self, channel_id: str) -> None:
        """Drop a channel and fail everything queued for it."""
        clean_id = _clean_channel_id(channel_id)
        with self._lock:
            channel = self._channels.pop(clean_id, None)
            if channel is None:
                return
            for command in channel.pending:
                command.result = {
                    "ok": False,
                    "error": "browser_channel_closed",
                    "message": "The browser side panel disconnected before this command ran.",
                }
                command.done.set()
                self._inflight.pop(command.id, None)
            channel.pending.clear()
            self._lock.notify_all()

    def take_command(self, channel_id: str, timeout: float = 25.0) -> Optional[Dict[str, Any]]:
        """Block until a command is queued for this channel, or time out.

        Returns the wire form for the extension, or ``None`` when the poll
        expired with nothing to do (the extension simply polls again).
        """
        clean_id = _clean_channel_id(channel_id)
        deadline = monotonic() + max(0.0, float(timeout))
        with self._lock:
            channel = self._channels.get(clean_id)
            if channel is None:
                # A poll from an unknown channel implicitly registers it, so a
                # panel that reconnects after a gateway restart recovers
                # without waiting for its next announce.
                channel = _Channel(channel_id=clean_id)
                self._channels[clean_id] = channel
            while True:
                channel.last_seen = monotonic()
                for command in channel.pending:
                    if command.dispatched:
                        continue
                    command.dispatched = True
                    return {
                        "command_id": command.id,
                        "action": command.action,
                        "params": dict(command.params),
                        "tab_id": command.tab_id,
                    }
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return None
                self._lock.wait(remaining)

    def resolve(self, command_id: str, result: Dict[str, Any]) -> bool:
        """Deliver the extension's result to the waiting tool. Consume-once."""
        with self._lock:
            command = self._inflight.pop(str(command_id or ""), None)
            if command is None:
                return False
            command.result = dict(result or {})
            command.done.set()
            for chan in self._channels.values():
                if command in chan.pending:
                    chan.pending.remove(command)
                    chan.last_seen = monotonic()
                    break
            self._lock.notify_all()
        return True

    # ── agent side ────────────────────────────────────────────────────

    def channel_status(self, channel_id: str = "") -> Dict[str, Any]:
        """Describe the connected channel without mutating anything."""
        with self._lock:
            channel = self._resolve_channel_locked(channel_id)
            if channel is None:
                return {"connected": False}
            return {
                "connected": True,
                "channel_id": channel.channel_id,
                "tab_id": channel.tab_id,
                "window_id": channel.window_id,
                "url": channel.url,
                "title": channel.title,
            }

    def submit(
        self,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        channel_id: str = "",
        tab_id: Optional[int] = None,
        timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        """Queue a command for the pinned tab and block until it resolves.

        Raises rather than returning a soft failure, so a caller can never
        mistake "the extension is not connected" for "the click did nothing".
        """
        wait_for = min(max(1.0, float(timeout)), MAX_COMMAND_TIMEOUT_SECONDS)
        with self._lock:
            channel = self._resolve_channel_locked(channel_id)
            if channel is None:
                raise NoBrowserChannelError(
                    "No Hermes browser side panel is connected. Open the extension "
                    "side panel and pin a tab; do not fall back to the headless "
                    "browser tools for this page."
                )
            if channel.tab_id is None:
                raise NoBrowserChannelError(
                    "The browser side panel is connected but no tab is pinned. "
                    "Ask the user to pin the tab they want acted on."
                )
            pinned: int = channel.tab_id
            target = _clean_tab_id(tab_id) if tab_id is not None else pinned
            if target is None or target != pinned:
                raise TabMismatchError(
                    f"Tab {target} is not the pinned tab ({channel.tab_id}). "
                    "Commands only run in the tab the user pinned."
                )
            if len(channel.pending) >= MAX_PENDING_COMMANDS:
                raise BrowserControlError(
                    "Too many browser commands are already queued; the extension "
                    "is not draining them."
                )
            command = _Command(
                id=uuid.uuid4().hex,
                action=str(action),
                params=dict(params or {}),
                tab_id=pinned,
                created_at=monotonic(),
            )
            channel.pending.append(command)
            self._inflight[command.id] = command
            self._lock.notify_all()

        if not command.done.wait(wait_for):
            with self._lock:
                self._inflight.pop(command.id, None)
                for chan in self._channels.values():
                    if command in chan.pending:
                        chan.pending.remove(command)
                        break
            raise CommandTimeoutError(
                f"The browser extension did not complete '{action}' within "
                f"{wait_for:.0f}s. The tab may have navigated or the panel closed."
            )
        return dict(command.result or {})

    # ── internals ─────────────────────────────────────────────────────

    def _resolve_channel_locked(self, channel_id: str = "") -> Optional[_Channel]:
        now = monotonic()
        for stale in [cid for cid, chan in self._channels.items() if not chan.alive(now)]:
            self._channels.pop(stale, None)
        if channel_id:
            channel = self._channels.get(_clean_channel_id(channel_id))
            return channel if channel and channel.alive(now) else None
        live = [chan for chan in self._channels.values() if chan.alive(now)]
        if not live:
            return None
        # Most recently seen wins when several panels are open.
        return max(live, key=lambda chan: chan.last_seen)


def _clean_channel_id(value: Any) -> str:
    text = str(value or "default").strip()
    return text[:128] or "default"


def _clean_tab_id(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Process-wide broker. The gateway hosts both the API server routes and the
# agent that calls the tools, so a module-level instance is the shared seam.
BROKER = BrowserControlBroker()
