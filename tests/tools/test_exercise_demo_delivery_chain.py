"""The exercise demo must survive the whole way to a delivered attachment.

Regression test for two failures seen in the live bot, neither of which any
single-layer test caught:

1. The bot messaged raw ``/opt/data/...`` paths to the user as chat text. The
   model had written the bare path instead of prefixing ``MEDIA:``, so the
   gateway treated it as prose and the user got a wall of paths.
2. A reply arrived with no image at all. The model reused a path from earlier in
   the conversation — topic 303 is one unbroken thread, so every path it has
   ever emitted stays in its history — and that file had since been moved. The
   gateway rejected the missing file and silently dropped it.

So this walks the real chain: tool result -> mediaTag -> the gateway's own
``extract_media`` -> delivered attachment, asserting nothing leaks into the text
and that a demo's path never moves.
"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from gateway.platforms.base import BasePlatformAdapter
from tools import exercise_library_tool as ex


PULLUPS = {
    "id": "Pullups",
    "name": "Pullups",
    "primaryMuscles": ["lats"],
    "secondaryMuscles": [],
    "equipment": "body only",
    "instructions": ["Hang.", "Pull up."],
    "category": "strength",
    "level": "beginner",
    "images": ["Pullups/0.jpg", "Pullups/1.jpg"],
}


def _jpeg(colour):
    import io

    buf = io.BytesIO()
    Image.new("RGB", (120, 90), colour).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    data_dir, gif_dir = tmp_path / "data", tmp_path / "gifs"
    data_dir.mkdir()
    gif_dir.mkdir()
    monkeypatch.setattr(ex, "_data_dir", lambda: data_dir)
    monkeypatch.setattr(ex, "_gif_dir", lambda: gif_dir)
    monkeypatch.setattr(ex, "_dataset_cache", [PULLUPS], raising=False)
    monkeypatch.setattr(
        ex, "_fetch_bytes",
        lambda url: json.dumps([PULLUPS]).encode() if url == ex.DATASET_URL
        else _jpeg((220, 40, 40) if url.endswith("0.jpg") else (40, 60, 220)),
    )
    yield gif_dir
    monkeypatch.setattr(ex, "_dataset_cache", None, raising=False)


def _demo_entry():
    payload = json.loads(ex._handle_demo({"exerciseIds": ["Pullups"]}))
    assert "error" not in payload, payload
    return payload["demos"][0]


def test_media_tag_survives_the_gateway_extraction():
    """The whole point: one tag in, one attachment out, no path in the text."""
    demo = _demo_entry()
    reply = f"הנה התרגיל\n{demo['mediaTag']}"

    media, cleaned = BasePlatformAdapter.extract_media(reply)

    assert len(media) == 1, "the tag must yield exactly one attachment"
    assert media[0][0] == demo["mediaPath"]
    assert "/" not in cleaned, f"a file path leaked into the message body: {cleaned!r}"
    assert cleaned.strip() == "הנה התרגיל"


def test_a_bare_path_leaks_into_the_message_body():
    """Why mediaTag exists. Without the prefix the path is delivered as text —
    this is what the user actually received."""
    demo = _demo_entry()
    media, cleaned = BasePlatformAdapter.extract_media(f"here\n{demo['mediaPath']}")

    assert media == [], "a bare path is not a media directive"
    assert demo["mediaPath"] in cleaned, "so it reaches the user as chat text"


def test_several_demos_each_deliver_separately():
    payload = json.loads(ex._handle_demo({"exerciseIds": ["Pullups", "Pullups"]}))
    tags = "\n".join(d["mediaTag"] for d in payload["demos"])
    media, cleaned = BasePlatformAdapter.extract_media(f"plan\n{tags}")

    assert len(media) == len(payload["demos"])
    assert "/" not in cleaned


def test_the_path_is_identical_before_and_after_being_drawn(_isolated):
    """A path already sitting in the chat history must keep resolving.

    Moving the file to reclassify it is what silently broke delivery: the
    gateway rejected the now-missing path and dropped the attachment.
    """
    before = _demo_entry()
    assert before["illustrated"] is False

    # ...an illustration is generated later, overwriting in place
    (_isolated / "Pullups.gif").write_bytes(b"GIF89a" + b"\0" * 5000)
    ex.mark_illustrated(PULLUPS)

    after = _demo_entry()
    assert after["illustrated"] is True
    assert after["mediaPath"] == before["mediaPath"], (
        "an old message's path must still resolve, and now return the drawing"
    )
    assert after["mediaTag"] == before["mediaTag"]
