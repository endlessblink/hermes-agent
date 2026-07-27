"""Regression tests: computer_use reaches API-server (browser-extension) sessions.

Two independent gaps kept the browser extension from ever driving the real
desktop, and both are covered here:

1. Toolset assembly — the hand-written ``hermes-api-server`` composite listed
   every core tool EXCEPT ``computer_use``, so ``_get_platform_tools`` inferred
   the ``computer_use`` toolset as disabled for the api_server platform. The
   tool was installed, enabled and passing ``hermes computer-use doctor``, yet
   never appeared in an extension conversation's schema.

2. Input delivery — Hermes never forwarded cua-driver's ``delivery_mode``, so
   on platforms with no focus-free keyboard backend (Linux/X11) every
   ``type``/``key`` action failed with "no focus-free input backend" and there
   was no way for a caller to opt into the activating path.
"""
from unittest.mock import patch

import pytest


class TestApiServerToolsetExposesComputerUse:
    def test_composite_lists_computer_use(self):
        from toolsets import resolve_toolset
        assert "computer_use" in resolve_toolset("hermes-api-server")

    def test_platform_default_enables_computer_use_toolset(self):
        """The inference in _get_platform_tools compares a toolset's static
        membership against the platform composite, so the composite entry is
        what actually flips the toolset on for api_server."""
        from tools.registry import discover_builtin_tools
        from hermes_cli.tools_config import _get_platform_tools
        discover_builtin_tools()
        assert "computer_use" in _get_platform_tools({}, "api_server")

    def test_explicit_profile_config_still_wins(self):
        """Profile/toolset configuration is preserved: a profile that saved an
        explicit api_server toolset list without computer_use must NOT have it
        re-enabled by the composite."""
        from tools.registry import discover_builtin_tools
        from hermes_cli.tools_config import _get_platform_tools
        discover_builtin_tools()
        config = {"platform_toolsets": {"api_server": ["web", "file"]}}
        enabled = _get_platform_tools(config, "api_server")
        assert "computer_use" not in enabled
        assert "web" in enabled

    def test_default_off_toolsets_not_dragged_in(self):
        """Negative contract: adding computer_use must not newly enable any
        default-off or credential-gated toolset."""
        import os
        from tools.registry import discover_builtin_tools
        from hermes_cli.tools_config import _get_platform_tools
        discover_builtin_tools()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HASS_TOKEN", None)
            os.environ.pop("XAI_API_KEY", None)
            enabled = _get_platform_tools({}, "api_server")
        for off in ("homeassistant", "discord", "discord_admin", "x_search"):
            assert off not in enabled

    def test_computer_use_stays_gated_by_check_fn(self):
        """Enabling the toolset must not bypass the cua-driver requirement —
        the registry check_fn is still what decides schema registration."""
        from tools.registry import registry
        import tools.computer_use_tool  # noqa: F401  (registers the tool)
        entry = registry.get_entry("computer_use")
        assert entry is not None
        assert entry.check_fn is not None


class TestDeliveryModePassthrough:
    def test_schema_advertises_delivery_mode(self):
        from tools.computer_use.schema import COMPUTER_USE_SCHEMA
        prop = COMPUTER_USE_SCHEMA["parameters"]["properties"].get("delivery_mode")
        assert prop is not None, "delivery_mode missing from the computer_use schema"
        assert set(prop["enum"]) == {"background", "foreground"}

    def test_action_attaches_delivery_mode_to_input_tools_only(self):
        """_action forwards delivery_mode for input tools and never for
        introspection tools, which cua-driver would reject."""
        from tools.computer_use.cua_backend import CuaDriverBackend

        backend = CuaDriverBackend.__new__(CuaDriverBackend)
        backend._delivery_mode = "foreground"
        backend._session_id = "sess"
        backend._snapshot_tokens = {}
        sent = {}

        class _Session:
            def call_tool(self, name, args):
                sent[name] = dict(args)
                return {"isError": False, "data": {"message": "ok"}}

        backend._session = _Session()
        backend._action("type_text", {"pid": 1, "window_id": 2, "text": "x"})
        backend._action("list_windows", {})
        assert sent["type_text"]["delivery_mode"] == "foreground"
        assert "delivery_mode" not in sent["list_windows"]

    def test_default_is_background_free(self):
        """With no override, nothing is forwarded — cua-driver keeps its own
        focus-free default and existing behaviour is unchanged."""
        from tools.computer_use.cua_backend import CuaDriverBackend

        backend = CuaDriverBackend.__new__(CuaDriverBackend)
        backend._delivery_mode = None
        backend._session_id = "sess"
        backend._snapshot_tokens = {}
        sent = {}

        class _Session:
            def call_tool(self, name, args):
                sent[name] = dict(args)
                return {"isError": False, "data": {"message": "ok"}}

        backend._session = _Session()
        backend._action("type_text", {"pid": 1, "window_id": 2, "text": "x"})
        assert "delivery_mode" not in sent["type_text"]

    def test_override_is_scoped_and_restored(self):
        from tools.computer_use.cua_backend import CuaDriverBackend

        backend = CuaDriverBackend.__new__(CuaDriverBackend)
        backend._delivery_mode = None
        with backend.delivery_mode_override("foreground"):
            assert backend._delivery_mode == "foreground"
        assert backend._delivery_mode is None

    def test_override_ignores_unknown_mode(self):
        from tools.computer_use.cua_backend import CuaDriverBackend

        backend = CuaDriverBackend.__new__(CuaDriverBackend)
        backend._delivery_mode = None
        with backend.delivery_mode_override("sideways"):
            assert backend._delivery_mode is None


class TestDeliveryScope:
    """The tool-level scope that turns a caller's delivery_mode into a
    window activation plus a scoped backend override."""

    class _Backend:
        def __init__(self):
            self._active_pid = 42
            self._active_window_id = 7
            self.mode = None
            self.brought_to_front = []

        def bring_to_front(self, *, pid, window_id=None):
            self.brought_to_front.append((pid, window_id))

        def delivery_mode_override(self, mode):
            import contextlib

            @contextlib.contextmanager
            def _cm():
                previous = self.mode
                self.mode = mode
                try:
                    yield
                finally:
                    self.mode = previous
            return _cm()

    def test_foreground_activates_window_and_sets_mode(self):
        from tools.computer_use.tool import _delivery_scope
        backend = self._Backend()
        with _delivery_scope(backend, "type", {"delivery_mode": "foreground"}):
            assert backend.mode == "foreground"
        assert backend.brought_to_front == [(42, 7)]
        assert backend.mode is None

    def test_background_does_not_activate_window(self):
        from tools.computer_use.tool import _delivery_scope
        backend = self._Backend()
        with _delivery_scope(backend, "type", {"delivery_mode": "background"}):
            assert backend.mode == "background"
        assert backend.brought_to_front == []

    def test_absent_delivery_mode_is_a_noop(self):
        from tools.computer_use.tool import _delivery_scope
        backend = self._Backend()
        with _delivery_scope(backend, "type", {}):
            assert backend.mode is None
        assert backend.brought_to_front == []

    def test_read_only_actions_never_activate(self):
        """capture/wait/list_apps must not steal focus even if a caller passes
        delivery_mode."""
        from tools.computer_use.tool import _delivery_scope
        backend = self._Backend()
        with _delivery_scope(backend, "capture", {"delivery_mode": "foreground"}):
            assert backend.mode is None
        assert backend.brought_to_front == []

    def test_backend_without_passthrough_is_tolerated(self):
        """An older/alternate backend lacking delivery_mode_override must not
        break dispatch."""
        from tools.computer_use.tool import _delivery_scope

        class _Old:
            pass

        with _delivery_scope(_Old(), "type", {"delivery_mode": "foreground"}):
            pass


class TestApprovalHandlingUnchanged:
    def test_computer_use_input_actions_still_go_through_approval(self):
        from tools.computer_use.tool import _DESTRUCTIVE_ACTIONS
        for action in ("click", "type", "key", "drag", "scroll", "set_value"):
            assert action in _DESTRUCTIVE_ACTIONS

    def test_delivery_mode_does_not_bypass_blocked_type_patterns(self):
        """Safety validation runs before dispatch, so a foreground request is
        still refused for blocked content."""
        import json
        from tools.computer_use.tool import handle_computer_use, _is_blocked_type
        blocked = None
        for candidate in ("rm -rf /", "sudo rm -rf /"):
            if _is_blocked_type(candidate):
                blocked = candidate
                break
        if blocked is None:
            pytest.skip("no blocked type pattern configured in this build")
        out = json.loads(handle_computer_use({
            "action": "type", "text": blocked, "delivery_mode": "foreground",
        }))
        assert "error" in out
