"""End-to-end: agent tool → HTTP routes → extension executor → DOM read-back.

This runs the real aiohttp routes against the real broker, with a stand-in for
the extension that speaks the exact wire protocol the side panel uses and
mutates a small in-memory "DOM". It is the test that would have caught the
original bug: the agent could see the form but nothing it did reached the page.
"""
import asyncio
import json
import threading

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402

from gateway.browser_control import BrowserControlBroker  # noqa: E402


class _FakePage:
    """Minimum of a form: fields that remember what was typed into them."""

    def __init__(self):
        self.fields = {"e1": {"label": "Full name", "value": ""},
                       "e2": {"label": "Target job title", "value": ""}}
        self.submitted = False

    def apply(self, command):
        action = command["action"]
        params = command["params"]
        if action == "snapshot":
            return {"ok": True, "elements": [
                {"ref": ref, "label": field["label"], "value": field["value"]}
                for ref, field in self.fields.items()
            ]}
        ref = params.get("ref")
        if ref not in self.fields:
            return {"ok": False, "error": "stale_ref", "message": f"no element {ref}"}
        if action == "type":
            self.fields[ref]["value"] = params.get("text", "")
            # Read-back is what the tool reports — never a bare "accepted".
            return {"ok": True, "verified": {"value": self.fields[ref]["value"]}}
        if action == "click":
            return {"ok": False, "error": "submit_blocked", "message": "filling only"}
        return {"ok": False, "error": "unknown_action"}


@pytest.fixture()
def live_gateway():
    """Start the two browser-control routes on a real loopback port."""
    broker = BrowserControlBroker()
    app = web.Application()

    async def announce(request):
        body = await request.json()
        return web.json_response(broker.announce(
            body.get("channel_id", "default"),
            tab_id=body.get("tab_id"),
            url=body.get("url", ""),
        ))

    async def next_command(request):
        channel = request.query.get("channel_id", "default")
        wait = float(request.query.get("wait", "5"))
        loop = asyncio.get_running_loop()
        command = await loop.run_in_executor(None, broker.take_command, channel, wait)
        return web.json_response({"command": command})

    async def result(request):
        body = await request.json()
        ok = broker.resolve(request.match_info["command_id"], body)
        return web.json_response({"accepted": ok}, status=200 if ok else 404)

    app.router.add_post("/v1/browser/channel", announce)
    app.router.add_get("/v1/browser/commands/next", next_command)
    app.router.add_post("/v1/browser/commands/{command_id}/result", result)

    state = {}

    def serve():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        runner = web.AppRunner(app)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 0)
        loop.run_until_complete(site.start())
        state["port"] = site._server.sockets[0].getsockname()[1]
        state["loop"] = loop
        state["ready"].set()
        loop.run_forever()

    state["ready"] = threading.Event()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert state["ready"].wait(10), "gateway did not start"
    yield broker, state["port"]
    state["loop"].call_soon_threadsafe(state["loop"].stop)


def _extension(port, page, channel="panel", tab_id=42, cycles=1):
    """Stand-in side panel: announce, then poll → apply → post result."""
    import urllib.request

    def post(path, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def run():
        post("/v1/browser/channel", {"channel_id": channel, "tab_id": tab_id,
                                     "url": "https://example.com/apply"})
        for _ in range(cycles):
            url = f"http://127.0.0.1:{port}/v1/browser/commands/next?channel_id={channel}&wait=5"
            with urllib.request.urlopen(url, timeout=10) as response:
                command = json.loads(response.read()).get("command")
            if not command:
                continue
            post(f"/v1/browser/commands/{command['command_id']}/result", page.apply(command))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_typing_reaches_the_page_and_is_verified_by_read_back(live_gateway):
    broker, port = live_gateway
    page = _FakePage()
    thread = _extension(port, page)

    # Wait for the panel to register before the agent acts.
    for _ in range(100):
        if broker.channel_status().get("connected"):
            break
        threading.Event().wait(0.05)

    result = broker.submit("type", {"ref": "e1", "text": "Noam Naumovsky"},
                           channel_id="panel", timeout=10)
    thread.join(timeout=10)

    assert result["ok"] is True
    assert result["verified"]["value"] == "Noam Naumovsky"
    # The real proof: the page itself changed, not just the response.
    assert page.fields["e1"]["value"] == "Noam Naumovsky"


def test_snapshot_then_fill_uses_refs_from_the_snapshot(live_gateway):
    broker, port = live_gateway
    page = _FakePage()
    thread = _extension(port, page, cycles=2)
    for _ in range(100):
        if broker.channel_status().get("connected"):
            break
        threading.Event().wait(0.05)

    snapshot = broker.submit("snapshot", {}, channel_id="panel", timeout=10)
    ref = next(element["ref"] for element in snapshot["elements"]
               if element["label"] == "Target job title")
    result = broker.submit("type", {"ref": ref, "text": "AI Workflow Specialist"},
                           channel_id="panel", timeout=10)
    thread.join(timeout=10)

    assert result["verified"]["value"] == "AI Workflow Specialist"
    assert page.fields["e2"]["value"] == "AI Workflow Specialist"


def test_a_stale_ref_surfaces_as_a_failure_not_a_silent_success(live_gateway):
    broker, port = live_gateway
    page = _FakePage()
    thread = _extension(port, page)
    for _ in range(100):
        if broker.channel_status().get("connected"):
            break
        threading.Event().wait(0.05)

    result = broker.submit("type", {"ref": "gone", "text": "x"}, channel_id="panel", timeout=10)
    thread.join(timeout=10)
    assert result["ok"] is False
    assert result["error"] == "stale_ref"
    assert page.fields["e1"]["value"] == ""


def test_the_form_is_never_submitted(live_gateway):
    broker, port = live_gateway
    page = _FakePage()
    thread = _extension(port, page)
    for _ in range(100):
        if broker.channel_status().get("connected"):
            break
        threading.Event().wait(0.05)

    result = broker.submit("click", {"ref": "e1"}, channel_id="panel", timeout=10)
    thread.join(timeout=10)
    assert result["error"] == "submit_blocked"
    assert page.submitted is False
