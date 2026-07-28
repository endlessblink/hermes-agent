"""A demo must never claim to be an illustration when it is a stock photo.

This is the regression test for a shipped bug. `exercise_demo` silently fell
back to crossfading the dataset's two photographs, wrote that under the same
filename an illustration would use, and returned it with no indication of what
it was. Three consequences, all observed in production:

1. The model was never told a drawing was missing, so `exercise_generate_demo`
   was never called — the on-demand feature was dead on arrival.
2. The fallback cached itself at the illustration's path, so that exercise could
   never be upgraded to a real drawing.
3. The user asked for a workout and got photographs of a stranger in a gym.
"""

from __future__ import annotations

import json

import pytest
from PIL import Image

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


def _png_bytes(colour):
    import io

    buf = io.BytesIO()
    Image.new("RGB", (120, 90), colour).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    gif_dir = tmp_path / "gifs"
    data_dir.mkdir()
    gif_dir.mkdir()
    monkeypatch.setattr(ex, "_data_dir", lambda: data_dir)
    monkeypatch.setattr(ex, "_gif_dir", lambda: gif_dir)
    monkeypatch.setattr(ex, "_dataset_cache", [PULLUPS], raising=False)
    monkeypatch.setattr(
        ex, "_fetch_bytes",
        lambda url: json.dumps([PULLUPS]).encode() if url == ex.DATASET_URL
        else _png_bytes((220, 40, 40) if url.endswith("0.jpg") else (40, 60, 220)),
    )
    yield gif_dir
    monkeypatch.setattr(ex, "_dataset_cache", None, raising=False)


def _demo(**kw):
    parsed = json.loads(ex._handle_demo({"exerciseIds": ["Pullups"], **kw}))
    assert "error" not in parsed, parsed
    return parsed


def test_photo_fallback_is_reported_as_not_illustrated():
    result = _demo()
    assert result["demos"][0]["illustrated"] is False
    assert result["illustratedCount"] == 0


def test_photo_fallback_tells_the_model_it_is_a_photograph():
    """Without this the model cannot know to offer a real drawing."""
    note = _demo()["note"]
    assert "photograph" in note.lower()
    assert "exercise_generate_demo" in note


def test_the_demo_path_never_changes(_isolated):
    """The chat is one unbroken thread, so every path the bot ever emitted stays
    in its history and gets reused. Moving a file to reclassify it broke delivery
    for every stale path — the gateway rejected the now-missing file silently."""
    photo_path = _demo()["demos"][0]["mediaPath"]

    ex.mark_illustrated(PULLUPS)
    (_isolated / "Pullups.gif").write_bytes(b"GIF89a" + b"\0" * 5000)
    drawn_path = _demo()["demos"][0]["mediaPath"]

    assert photo_path == drawn_path, "upgrading to a drawing must not move the file"


def test_a_real_illustration_is_reported_as_illustrated(_isolated):
    (_isolated / "Pullups.gif").write_bytes(b"GIF89a" + b"\0" * 5000)
    ex.mark_illustrated(PULLUPS)
    result = _demo()
    demo = result["demos"][0]
    assert demo["illustrated"] is True
    assert demo["mediaPath"].endswith("Pullups.gif")
    assert result["illustratedCount"] == 1


def test_an_unmarked_file_is_treated_as_a_photo(_isolated):
    """Provenance comes from the marker, not from the file being present."""
    (_isolated / "Pullups.gif").write_bytes(b"GIF89a" + b"\0" * 5000)
    assert _demo()["demos"][0]["illustrated"] is False


def test_a_failed_render_never_marks_the_exercise_illustrated():
    assert ex.is_illustrated(PULLUPS) is False
    _demo()  # produces only a photo fallback
    assert ex.is_illustrated(PULLUPS) is False


def test_note_is_clean_when_everything_is_illustrated(_isolated):
    (_isolated / "Pullups.gif").write_bytes(b"GIF89a" + b"\0" * 5000)
    ex.mark_illustrated(PULLUPS)
    note = _demo()["note"]
    assert "photograph" not in note.lower()


# --------------------------------------------------------------------------
# path leakage — the bot once messaged raw /opt/... paths to the user
# --------------------------------------------------------------------------

def test_every_demo_carries_a_ready_to_copy_media_tag():
    """The model emitted bare paths, which Telegram showed as chat text."""
    demo = _demo()["demos"][0]
    assert demo["mediaTag"] == f"MEDIA:{demo['mediaPath']}"
    assert demo["mediaTag"].startswith("MEDIA:/")


def test_note_forbids_bare_paths_and_stale_paths():
    """Both observed failure modes must be named in the instruction."""
    note = _demo()["note"]
    assert "bare file path" in note
    assert "reuse a path from earlier" in note
