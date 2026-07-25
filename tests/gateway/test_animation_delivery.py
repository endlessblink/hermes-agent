"""Local animated GIFs must be delivered as animations, not flattened photos.

Telegram (and every other platform with a distinct animation API) turns a GIF
posted through the photo endpoint into a single still frame. The routing used to
check ``file://`` before checking for an animation, so any locally generated GIF
silently arrived as a static image.
"""

from __future__ import annotations

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult


class _RecordingAdapter(BasePlatformAdapter):
    """Minimal concrete adapter that records which send path each item took."""

    def __init__(self):
        super().__init__(config=PlatformConfig(enabled=True, token="test"), platform=Platform.TELEGRAM)
        self.animations: list[str] = []
        self.image_files: list[str] = []
        self.image_urls: list[str] = []

    async def connect(self, *, is_reconnect: bool = False):
        return True

    async def disconnect(self):
        pass

    async def send(self, *a, **kw):
        return SendResult(success=True, message_id="4")

    async def get_chat_info(self, *a):
        return {}

    async def send_animation(self, chat_id, animation_url, caption=None, reply_to=None, metadata=None):
        self.animations.append(animation_url)
        return SendResult(success=True, message_id="1")

    async def send_image_file(self, chat_id, image_path, caption=None, reply_to=None, metadata=None, **kwargs):
        self.image_files.append(image_path)
        return SendResult(success=True, message_id="2")

    async def send_image(self, chat_id, image_url, caption=None, reply_to=None, metadata=None):
        self.image_urls.append(image_url)
        return SendResult(success=True, message_id="3")


@pytest.mark.asyncio
async def test_local_gif_is_sent_as_an_animation():
    adapter = _RecordingAdapter()
    await adapter.send_multiple_images("chat", [("file:///tmp/pullups.gif", "")])
    assert adapter.animations == ["/tmp/pullups.gif"], "a local GIF must not go through the photo path"
    assert adapter.image_files == []


@pytest.mark.asyncio
async def test_local_gif_path_is_percent_decoded():
    adapter = _RecordingAdapter()
    await adapter.send_multiple_images("chat", [("file:///tmp/V-Bar%20Pullup.gif", "")])
    assert adapter.animations == ["/tmp/V-Bar Pullup.gif"]


@pytest.mark.asyncio
async def test_remote_gif_still_routes_to_animation():
    adapter = _RecordingAdapter()
    await adapter.send_multiple_images("chat", [("https://example.com/a.gif", "")])
    assert adapter.animations == ["https://example.com/a.gif"]


@pytest.mark.asyncio
async def test_local_png_still_routes_to_the_photo_path():
    adapter = _RecordingAdapter()
    await adapter.send_multiple_images("chat", [("file:///tmp/chart.png", "")])
    assert adapter.image_files == ["/tmp/chart.png"]
    assert adapter.animations == []


@pytest.mark.asyncio
async def test_remote_png_still_routes_to_send_image():
    adapter = _RecordingAdapter()
    await adapter.send_multiple_images("chat", [("https://example.com/a.png", "")])
    assert adapter.image_urls == ["https://example.com/a.png"]


@pytest.mark.asyncio
async def test_mixed_batch_splits_correctly():
    adapter = _RecordingAdapter()
    await adapter.send_multiple_images(
        "chat",
        [
            ("file:///tmp/a.gif", ""),
            ("file:///tmp/b.png", ""),
            ("https://example.com/c.gif", ""),
        ],
    )
    assert adapter.animations == ["/tmp/a.gif", "https://example.com/c.gif"]
    assert adapter.image_files == ["/tmp/b.png"]


@pytest.mark.asyncio
async def test_default_send_animation_uploads_a_local_path_as_a_file():
    """The base fallback must not hand a filesystem path to send_image."""

    class _NoOverride(_RecordingAdapter):
        send_animation = BasePlatformAdapter.send_animation

    adapter = _NoOverride()
    await adapter.send_animation("chat", "/tmp/local.gif")
    assert adapter.image_files == ["/tmp/local.gif"]
    assert adapter.image_urls == []


@pytest.mark.asyncio
async def test_default_send_animation_passes_a_url_through():
    class _NoOverride(_RecordingAdapter):
        send_animation = BasePlatformAdapter.send_animation

    adapter = _NoOverride()
    await adapter.send_animation("chat", "https://example.com/a.gif")
    assert adapter.image_urls == ["https://example.com/a.gif"]
