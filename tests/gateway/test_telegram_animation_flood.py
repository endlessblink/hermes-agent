"""A rate-limited animation must not decay into a dropped message.

Sending a library of exercise demos in one burst reliably trips Telegram flood
control. The old code treated ``RetryAfter`` like any other send failure and
fell back to ``send_image`` — which, for a local path, runs the URL SSRF check,
rejects the bare path for having no scheme, and hands it to the base adapter,
which posts the *filesystem path itself* as a text message. Live logs showed 42
flood errors paired with 43 blocked-URL warnings inside five minutes: the user
received paths instead of pictures.

Flood control means wait, and a local file belongs on the local-file sender.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

# Reuse the fake telegram module tree and the bare-adapter builder.
from tests.gateway.test_telegram_thread_fallback import (  # noqa: F401
    FakeRetryAfter,
    _inject_fake_telegram,
    _make_adapter,
)


class _Msg:
    message_id = 77


def _adapter_with_bot(send_animation):
    adapter = _make_adapter()
    adapter._bot = type("Bot", (), {"send_animation": staticmethod(send_animation)})()
    adapter.send_image_file = AsyncMock(return_value="SENT_AS_FILE")
    adapter.send_image = AsyncMock(return_value="SENT_AS_URL")
    return adapter


@pytest.fixture
def _no_real_sleep(monkeypatch):
    slept: list[float] = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("asyncio.sleep", _fake_sleep)
    return slept


@pytest.mark.asyncio
async def test_flood_control_waits_and_retries_the_animation(tmp_path, _no_real_sleep):
    gif = tmp_path / "swing.gif"
    gif.write_bytes(b"GIF89a")
    calls = []

    async def _send_animation(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise FakeRetryAfter(38)
        return _Msg()

    adapter = _adapter_with_bot(_send_animation)

    result = await adapter.send_animation("chat", str(gif))

    assert result.success, "the animation should survive one flood-control wait"
    assert len(calls) == 2, "the send must be retried, not degraded"
    assert _no_real_sleep and _no_real_sleep[0] >= 38, "must honour the server's interval"
    adapter.send_image.assert_not_awaited()
    adapter.send_image_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_animation_falls_back_to_the_file_sender(tmp_path, _no_real_sleep):
    """A local path must never reach send_image — that path posts it as text."""
    gif = tmp_path / "clean.gif"
    gif.write_bytes(b"GIF89a")

    async def _always_fails(**kwargs):
        raise RuntimeError("Telegram said no")

    adapter = _adapter_with_bot(_always_fails)

    result = await adapter.send_animation("chat", str(gif))

    assert result == "SENT_AS_FILE"
    adapter.send_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_animation_still_falls_back_to_send_image(_no_real_sleep):
    async def _always_fails(**kwargs):
        raise RuntimeError("Telegram said no")

    adapter = _adapter_with_bot(_always_fails)

    result = await adapter.send_animation("chat", "https://example.com/a.gif")

    assert result == "SENT_AS_URL"
    adapter.send_image_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_image_routes_an_existing_local_path_to_the_file_sender(tmp_path):
    png = tmp_path / "pose.png"
    png.write_bytes(b"\x89PNG")

    adapter = _make_adapter()
    adapter._bot = object()
    adapter.send_image_file = AsyncMock(return_value="SENT_AS_FILE")

    result = await adapter.send_image("chat", str(png))

    assert result == "SENT_AS_FILE"


@pytest.mark.asyncio
async def test_long_batches_are_paced_short_ones_are_not(monkeypatch):
    """Spacing a long batch is cheaper than waiting out the rejections it earns."""
    from gateway.platforms.base import BasePlatformAdapter, SendResult
    from gateway.config import Platform, PlatformConfig

    class _Batch(BasePlatformAdapter):
        def __init__(self):
            super().__init__(
                config=PlatformConfig(enabled=True, token="t"), platform=Platform.TELEGRAM,
            )

        async def connect(self, *, is_reconnect: bool = False):
            return True

        async def disconnect(self):
            pass

        async def send(self, *a, **kw):
            return SendResult(success=True, message_id="1")

        async def get_chat_info(self, *a):
            return {}

        async def send_image(self, chat_id, image_url, caption=None, reply_to=None, metadata=None):
            return SendResult(success=True, message_id="1")

    slept: list[float] = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("asyncio.sleep", _fake_sleep)

    await _Batch().send_multiple_images(
        "chat", [(f"https://example.com/{i}.png", "") for i in range(3)],
    )
    assert slept == [], "a short batch should still go out back to back"

    await _Batch().send_multiple_images(
        "chat", [(f"https://example.com/{i}.png", "") for i in range(20)],
    )
    assert len(slept) == 20 and all(s >= 3.0 for s in slept)


@pytest.mark.asyncio
async def test_send_image_leaves_a_non_path_string_alone():
    """A schemeless string that is not a real file keeps its existing route.

    Telegram file_ids are schemeless too. They have never worked through
    ``send_image`` — the SSRF check rejects them for having no scheme — and the
    local-path shortcut must not change that by handing a non-existent path to
    the file sender, which would only produce a confusing missing-file error.
    """
    adapter = _make_adapter()
    adapter.send_image_file = AsyncMock(return_value="SENT_AS_FILE")
    adapter._bot = object()

    await adapter.send_image("chat", "AgACAgQAAxkBAAI_file_id")

    adapter.send_image_file.assert_not_awaited()
