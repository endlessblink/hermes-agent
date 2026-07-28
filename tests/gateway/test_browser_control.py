"""Broker + tool contract for agent control of the extension's pinned tab.

The failure this guards against is subtle: an agent that believes it clicked
something when nothing happened. Every path here asserts that a command either
demonstrably reached the pinned tab or raised a reason the model can act on —
never a soft "ok" that papers over a disconnected panel.
"""
import json
import threading
import time

import pytest

from gateway.browser_control import (
    BrowserControlBroker,
    BrowserControlError,
    CommandTimeoutError,
    NoBrowserChannelError,
    TabMismatchError,
)


@pytest.fixture()
def broker():
    return BrowserControlBroker()


def _extension(broker, channel="panel", result=None, timeout=5.0):
    """Run one poll→execute→resolve cycle on a background thread."""
    captured = {}

    def run():
        command = broker.take_command(channel, timeout=timeout)
        captured["command"] = command
        if command:
            broker.resolve(command["command_id"], result or {"ok": True})

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, captured


class TestChannelLifecycle:
    def test_no_channel_is_an_error_not_a_silent_noop(self, broker):
        with pytest.raises(NoBrowserChannelError):
            broker.submit("click", {"ref": "e1"})

    def test_connected_channel_without_pinned_tab_still_refuses(self, broker):
        broker.announce("panel", tab_id=None)
        with pytest.raises(NoBrowserChannelError) as excinfo:
            broker.submit("click", {"ref": "e1"})
        assert "no tab is pinned" in str(excinfo.value).lower()

    def test_status_reports_the_pinned_tab(self, broker):
        broker.announce("panel", tab_id=7, url="https://example.com", title="Example")
        status = broker.channel_status()
        assert status["connected"] is True
        assert status["tab_id"] == 7
        assert status["url"] == "https://example.com"

    def test_status_is_disconnected_before_any_announce(self, broker):
        assert broker.channel_status() == {"connected": False}

    def test_disconnect_fails_queued_commands_immediately(self, broker):
        broker.announce("panel", tab_id=7)
        results = {}

        def submit():
            try:
                results["value"] = broker.submit("click", {"ref": "e1"}, timeout=10)
            except BrowserControlError as exc:  # pragma: no cover - defensive
                results["error"] = exc

        thread = threading.Thread(target=submit, daemon=True)
        thread.start()
        time.sleep(0.2)
        broker.disconnect("panel")
        thread.join(timeout=5)
        assert results["value"]["ok"] is False
        assert results["value"]["error"] == "browser_channel_closed"

    def test_a_poll_from_an_unknown_channel_reregisters_it(self, broker):
        """Survives a gateway restart: the panel's next poll re-establishes it."""
        assert broker.take_command("panel", timeout=0.1) is None
        assert broker.channel_status("panel")["connected"] is True


class TestTabScoping:
    def test_command_for_a_different_tab_is_refused(self, broker):
        broker.announce("panel", tab_id=7)
        with pytest.raises(TabMismatchError):
            broker.submit("click", {"ref": "e1"}, tab_id=8)

    def test_delivered_command_carries_the_pinned_tab_id(self, broker):
        broker.announce("panel", tab_id=7)
        thread, captured = _extension(broker)
        broker.submit("click", {"ref": "e1"}, timeout=5)
        thread.join(timeout=5)
        assert captured["command"]["tab_id"] == 7

    def test_repinning_updates_the_target(self, broker):
        broker.announce("panel", tab_id=7)
        broker.announce("panel", tab_id=9)
        thread, captured = _extension(broker)
        broker.submit("click", {"ref": "e1"}, timeout=5)
        thread.join(timeout=5)
        assert captured["command"]["tab_id"] == 9


class TestCommandRoundTrip:
    def test_result_reaches_the_waiting_tool(self, broker):
        broker.announce("panel", tab_id=7)
        payload = {"ok": True, "verified": {"value": "Noam Naumovsky"}}
        thread, _ = _extension(broker, result=payload)
        result = broker.submit("type", {"ref": "e3", "text": "Noam Naumovsky"}, timeout=5)
        thread.join(timeout=5)
        assert result == payload

    def test_timeout_raises_rather_than_returning_ok(self, broker):
        broker.announce("panel", tab_id=7)
        with pytest.raises(CommandTimeoutError):
            broker.submit("click", {"ref": "e1"}, timeout=1)

    def test_a_command_is_delivered_once(self, broker):
        broker.announce("panel", tab_id=7)
        thread, captured = _extension(broker)
        broker.submit("click", {"ref": "e1"}, timeout=5)
        thread.join(timeout=5)
        # Same channel polls again: the command must not come back.
        assert broker.take_command("panel", timeout=0.2) is None

    def test_result_is_consumed_once(self, broker):
        broker.announce("panel", tab_id=7)
        thread, captured = _extension(broker)
        broker.submit("click", {"ref": "e1"}, timeout=5)
        thread.join(timeout=5)
        assert broker.resolve(captured["command"]["command_id"], {"ok": True}) is False

    def test_resolving_an_unknown_command_is_rejected(self, broker):
        assert broker.resolve("does-not-exist", {"ok": True}) is False

    def test_queue_is_bounded(self, broker):
        broker.announce("panel", tab_id=7)
        threads = []
        for _ in range(33):
            thread = threading.Thread(
                target=lambda: _swallow(broker.submit, "click", {"ref": "e1"}, timeout=2),
                daemon=True,
            )
            thread.start()
            threads.append(thread)
        time.sleep(0.5)
        with pytest.raises(BrowserControlError):
            broker.submit("click", {"ref": "e1"}, timeout=1)
        for thread in threads:
            thread.join(timeout=5)


def _swallow(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Exception:
        pass


class TestToolSurface:
    def test_tools_are_hidden_when_no_panel_is_connected(self, monkeypatch):
        import tools.browser_control_tool as bct
        monkeypatch.setattr(bct.BROKER, "channel_status", lambda *a, **k: {"connected": False})
        assert bct.channel_available() is False

    def test_tools_appear_once_a_panel_connects(self, monkeypatch):
        import tools.browser_control_tool as bct
        monkeypatch.setattr(bct.BROKER, "channel_status", lambda *a, **k: {"connected": True, "tab_id": 7})
        assert bct.channel_available() is True

    def test_registered_in_the_browser_control_toolset(self):
        from tools.registry import discover_builtin_tools, registry
        discover_builtin_tools()
        entry = registry.get_entry("extension_browser_type")
        assert entry is not None
        assert entry.toolset == "browser_control"

    def test_api_server_composite_carries_the_control_tools(self):
        from toolsets import resolve_toolset
        tools = resolve_toolset("hermes-api-server")
        for name in ("extension_browser_snapshot", "extension_browser_click",
                     "extension_browser_type", "extension_browser_upload"):
            assert name in tools

    def test_failure_tells_the_model_not_to_use_the_headless_browser(self, monkeypatch):
        """The whole point: a disconnected panel must not read as 'try browser_*'."""
        import tools.browser_control_tool as bct

        def boom(*_a, **_k):
            raise NoBrowserChannelError("No Hermes browser side panel is connected.")

        monkeypatch.setattr(bct.BROKER, "submit", boom)
        payload = json.loads(bct.extension_browser_click({"ref": "e1"}))
        assert payload["ok"] is False
        assert payload["error"] == "no_browser_channel"
        assert "browser_*" in payload["hint"]

    def test_click_without_a_ref_never_reaches_the_browser(self, monkeypatch):
        import tools.browser_control_tool as bct
        called = []
        monkeypatch.setattr(bct.BROKER, "submit", lambda *a, **k: called.append(a))
        payload = json.loads(bct.extension_browser_click({}))
        assert payload["error"] == "missing_ref"
        assert called == []

    def test_timeout_maps_to_its_own_error_code(self, monkeypatch):
        import tools.browser_control_tool as bct

        def boom(*_a, **_k):
            raise CommandTimeoutError("too slow")

        monkeypatch.setattr(bct.BROKER, "submit", boom)
        payload = json.loads(bct.extension_browser_type({"ref": "e1", "text": "x"}))
        assert payload["error"] == "browser_command_timeout"


class TestUploadGuards:
    def test_relative_path_is_refused(self):
        import tools.browser_control_tool as bct
        payload = json.loads(bct.extension_browser_upload({"ref": "e1", "path": "resume.pdf"}))
        assert payload["error"] == "path_not_absolute"

    def test_missing_file_is_refused(self):
        import tools.browser_control_tool as bct
        payload = json.loads(bct.extension_browser_upload({"ref": "e1", "path": "/nope/missing.pdf"}))
        assert payload["error"] == "file_not_found"

    def test_disallowed_extension_is_refused(self, tmp_path):
        import tools.browser_control_tool as bct
        target = tmp_path / "payload.sh"
        target.write_text("#!/bin/sh\n")
        payload = json.loads(bct.extension_browser_upload({"ref": "e1", "path": str(target)}))
        assert payload["error"] == "file_type_not_allowed"

    def test_oversized_file_is_refused(self, tmp_path, monkeypatch):
        import tools.browser_control_tool as bct
        monkeypatch.setattr(bct, "_UPLOAD_MAX_BYTES", 16)
        target = tmp_path / "resume.pdf"
        target.write_bytes(b"x" * 64)
        payload = json.loads(bct.extension_browser_upload({"ref": "e1", "path": str(target)}))
        assert payload["error"] == "file_too_large"

    def test_upload_requires_an_explicit_path(self):
        import tools.browser_control_tool as bct
        payload = json.loads(bct.extension_browser_upload({"ref": "e1"}))
        assert payload["error"] == "missing_path"

    def test_allowed_file_is_sent_as_bytes_not_a_host_path(self, tmp_path, monkeypatch):
        """The extension never reads the disk; the validated bytes travel inline."""
        import tools.browser_control_tool as bct
        target = tmp_path / "resume.pdf"
        target.write_bytes(b"%PDF-1.4 fake")
        sent = {}

        def capture(action, params, **kwargs):
            sent["action"] = action
            sent["params"] = params
            return {"ok": True}

        monkeypatch.setattr(bct.BROKER, "submit", capture)
        json.loads(bct.extension_browser_upload({"ref": "e1", "path": str(target)}))
        assert sent["action"] == "upload"
        assert sent["params"]["name"] == "resume.pdf"
        assert sent["params"]["content_base64"]
        assert "path" not in sent["params"]
