"""Steer the agent to the user's pinned tab instead of the headless browser.

The bug this guards: the agent called browser_navigate on a page the user
already had open and signed in to, landed in a bot challenge, and reported that
as the page's fault. browser_* is a different browser with a different cookie
jar; when the user's own tab is pinned, that tab is the correct target.

The guard is intentionally narrow — same origin as the pinned tab only — so it
never blocks a legitimate use of the headless browser on some other site.
"""
import json

import pytest

from gateway.browser_control import BROKER
from tools.browser_tool import _pinned_tab_conflict


@pytest.fixture(autouse=True)
def clean_channel():
    """Every test starts with no panel connected."""
    BROKER.disconnect("test-panel")
    yield
    BROKER.disconnect("test-panel")


class TestPinnedTabGuard:
    def test_no_extension_connected_means_no_interference(self):
        assert _pinned_tab_conflict("https://example.com/apply") is None

    def test_same_origin_as_the_pinned_tab_is_refused(self):
        BROKER.announce("test-panel", tab_id=42, url="https://weworkremotely.com/jobs/1")
        blocked = _pinned_tab_conflict("https://weworkremotely.com/job-seekers/onboarding/step_1")
        assert blocked is not None
        payload = json.loads(blocked)
        assert payload["success"] is False
        assert payload["error"] == "pinned_tab_available"

    def test_the_refusal_names_the_tool_to_use_instead(self):
        """A bare refusal would just make the model retry the same way."""
        BROKER.announce("test-panel", tab_id=42, url="https://example.com/apply")
        payload = json.loads(_pinned_tab_conflict("https://example.com/apply"))
        assert "extension_browser_snapshot" in payload["hint"]
        assert "extension_browser_type" in payload["hint"]

    def test_the_refusal_explains_why_rather_than_just_saying_no(self):
        BROKER.announce("test-panel", tab_id=42, url="https://example.com/apply")
        payload = json.loads(_pinned_tab_conflict("https://example.com/other"))
        assert "does not share those cookies" in payload["message"]

    def test_a_different_site_passes_straight_through(self):
        BROKER.announce("test-panel", tab_id=42, url="https://example.com/apply")
        assert _pinned_tab_conflict("https://news.ycombinator.com") is None

    def test_matching_is_case_insensitive_on_the_host(self):
        BROKER.announce("test-panel", tab_id=42, url="https://Example.COM/apply")
        assert _pinned_tab_conflict("https://example.com/apply") is not None

    def test_a_subdomain_is_a_different_origin(self):
        """Narrow on purpose: app.example.com is not example.com."""
        BROKER.announce("test-panel", tab_id=42, url="https://example.com/apply")
        assert _pinned_tab_conflict("https://app.example.com/apply") is None

    def test_a_connected_panel_with_no_pinned_tab_does_not_block(self):
        BROKER.announce("test-panel", tab_id=None, url="")
        assert _pinned_tab_conflict("https://example.com") is None

    def test_a_pinned_tab_with_no_url_does_not_block(self):
        BROKER.announce("test-panel", tab_id=42, url="")
        assert _pinned_tab_conflict("https://example.com") is None

    def test_a_broken_check_never_blocks_navigation(self, monkeypatch):
        """Steering is a nicety; it must never take out the browser tool."""
        import tools.browser_tool as bt

        def boom(*_a, **_k):
            raise RuntimeError("broker exploded")

        monkeypatch.setattr(BROKER, "channel_status", boom)
        assert bt._pinned_tab_conflict("https://example.com") is None

    def test_garbage_url_does_not_raise(self):
        BROKER.announce("test-panel", tab_id=42, url="https://example.com/apply")
        assert _pinned_tab_conflict("not a url at all") is None


class TestToolDescriptions:
    def test_navigate_warns_that_it_is_a_different_browser(self):
        from tools.browser_tool import _BROWSER_SCHEMA_MAP
        description = _BROWSER_SCHEMA_MAP["browser_navigate"]["description"]
        assert "does NOT share" in description
        assert "extension_browser" in description

    def test_type_points_at_the_extension_for_the_user_s_own_tab(self):
        from tools.browser_tool import _BROWSER_SCHEMA_MAP
        description = _BROWSER_SCHEMA_MAP["browser_type"]["description"]
        assert "extension_browser_type" in description

    def test_the_extension_tools_point_back_the_other_way(self):
        """Both sides must agree, or the model ping-pongs between them."""
        import tools.browser_control_tool as bct
        from gateway.browser_control import NoBrowserChannelError
        payload = json.loads(bct._error(NoBrowserChannelError("no panel")))
        assert "browser_*" in payload["hint"]


class TestNavigateIntegration:
    def test_browser_navigate_refuses_before_touching_a_session(self, monkeypatch):
        """The guard runs early — no browser session is created on the refused path."""
        import tools.browser_tool as bt

        launched = []
        monkeypatch.setattr(bt, "_get_or_create_session",
                            lambda *a, **k: launched.append(a) or {}, raising=False)
        BROKER.announce("test-panel", tab_id=42, url="https://example.com/apply")
        result = json.loads(bt.browser_navigate("https://example.com/apply"))
        assert result["error"] == "pinned_tab_available"
        assert launched == [], "must not spin up a browser session just to refuse"
