"""Tests for on-demand exercise demo generation.

The Codex call is stubbed throughout — nothing here spends a generation or
touches the network. What is exercised is the contract around it: caching,
validation, cast assignment, prompt content, and failure handling.
"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from tools import exercise_demo_generator as gen


PULLUPS = {
    "id": "Pullups",
    "name": "Pullups",
    "primaryMuscles": ["lats"],
    "secondaryMuscles": ["biceps"],
    "equipment": "body only",
    "instructions": ["Hang from the bar.", "Pull up until your chin passes it."],
}

SQUAT = {
    "id": "Barbell_Squat",
    "name": "Barbell Squat",
    "primaryMuscles": ["quadriceps"],
    "secondaryMuscles": [],
    "equipment": "barbell",
    "instructions": ["Rack the bar.", "Squat down."],
}

POSES = ["standing tall", "halfway down", "bottom position"]


def _strip_image(path, panels=3):
    """A synthetic strip: a dark figure-ish blob per panel on white.

    Each panel's blob is a different height — identical panels would collapse
    into a single GIF frame and the animation assertions would pass vacuously.
    """
    w, h = 900, 300
    im = Image.new("RGB", (w, h), (255, 255, 255))
    pw = w // panels
    for i in range(panels):
        top = 60 + i * 40  # crouches lower each panel
        for x in range(i * pw + 60, i * pw + 60 + 80):
            for y in range(top, 250):
                im.putpixel((x, y), (20, 20, 20))
    im.save(path)
    return path


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    gif_dir = tmp_path / "gifs"
    cast_dir = tmp_path / "cast"
    gif_dir.mkdir()
    cast_dir.mkdir()
    for member in gen.CAST:
        Image.new("RGB", (64, 96), (200, 200, 200)).save(cast_dir / member["file"])

    monkeypatch.setattr(gen, "_gif_dir", lambda: gif_dir)
    monkeypatch.setattr(gen, "_cast_dir", lambda: cast_dir)
    monkeypatch.setattr(gen, "_codex_available", lambda: True)
    monkeypatch.setattr(gen, "_by_id", lambda i: {"Pullups": PULLUPS,
                                                  "Barbell_Squat": SQUAT}.get(i))

    calls = []

    def fake_codex(prompt, reference, workdir):
        calls.append({"prompt": prompt, "reference": str(reference)})
        return _strip_image(workdir / "strip.png"), ""

    monkeypatch.setattr(gen, "_run_codex", fake_codex)
    yield calls


def _payload(raw):
    parsed = json.loads(raw)
    assert "error" not in parsed, parsed
    return parsed


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------

def test_generates_a_gif_and_reports_the_path():
    result = _payload(gen._handle_generate({"exerciseId": "Pullups", "poses": POSES}))
    assert result["generated"] is True
    assert result["mediaPath"].endswith("Pullups.gif")
    with Image.open(result["mediaPath"]) as im:
        assert im.format == "GIF"
        assert im.is_animated


def test_tells_the_model_how_to_deliver_it():
    result = _payload(gen._handle_generate({"exerciseId": "Pullups", "poses": POSES}))
    assert "MEDIA:" in result["note"]


def test_keeps_the_strip_for_free_rebuilds(tmp_path):
    """Re-timing later must not cost another generation."""
    result = _payload(gen._handle_generate({"exerciseId": "Pullups", "poses": POSES}))
    from pathlib import Path
    assert Path(result["mediaPath"]).with_name("Pullups_strip.png").is_file()


# --------------------------------------------------------------------------
# caching — the thing that stops repeat requests costing money
# --------------------------------------------------------------------------

def test_second_request_is_served_from_cache_without_generating(_isolated):
    first = _payload(gen._handle_generate({"exerciseId": "Pullups", "poses": POSES}))
    assert first["generated"] is True
    assert len(_isolated) == 1

    second = _payload(gen._handle_generate({"exerciseId": "Pullups", "poses": POSES}))
    assert second["generated"] is False
    assert len(_isolated) == 1, "a cached demo must not trigger a second generation"


def test_force_regenerates(_isolated):
    gen._handle_generate({"exerciseId": "Pullups", "poses": POSES})
    result = _payload(gen._handle_generate({"exerciseId": "Pullups", "poses": POSES,
                                            "force": True}))
    assert result["generated"] is True
    assert len(_isolated) == 2


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def test_rejects_an_unknown_exercise():
    parsed = json.loads(gen._handle_generate({"exerciseId": "Nope", "poses": POSES}))
    assert "error" in parsed


@pytest.mark.parametrize("poses", [[], ["only one"], ["a", "b"], ["a", "b", "c", "d"]])
def test_requires_exactly_three_poses(poses):
    parsed = json.loads(gen._handle_generate({"exerciseId": "Pullups", "poses": poses}))
    assert "error" in parsed
    assert "3" in parsed["error"]


def test_reports_clearly_when_codex_is_missing(monkeypatch):
    monkeypatch.setattr(gen, "_codex_available", lambda: False)
    parsed = json.loads(gen._handle_generate({"exerciseId": "Pullups", "poses": POSES}))
    assert "error" in parsed
    assert "codex" in parsed["error"].lower()


def test_surfaces_a_codex_failure_without_crashing(monkeypatch):
    monkeypatch.setattr(gen, "_run_codex", lambda p, r, w: (None, "codex timed out"))
    parsed = json.loads(gen._handle_generate({"exerciseId": "Pullups", "poses": POSES}))
    assert "error" in parsed
    assert "timed out" in parsed["error"]


# --------------------------------------------------------------------------
# cast + prompt
# --------------------------------------------------------------------------

def test_cast_choice_is_stable_per_exercise():
    assert gen._cast_for("Pullups") == gen._cast_for("Pullups")


def test_different_exercises_can_draw_different_people():
    picks = {gen._cast_for(f"Exercise_{i}")["file"] for i in range(40)}
    assert len(picks) > 1, "every exercise would show the same person"


def test_prompt_names_the_dataset_muscles(_isolated):
    gen._handle_generate({"exerciseId": "Pullups", "poses": POSES})
    prompt = _isolated[0]["prompt"]
    assert "LATS" in prompt, "the prime mover must come from the dataset, not guesswork"
    assert "BICEPS" in prompt


def test_prompt_pins_the_equipment(_isolated):
    """A kettlebell drawn as a dumbbell is wrong information."""
    gen._handle_generate({"exerciseId": "Barbell_Squat", "poses": POSES})
    assert "barbell" in _isolated[0]["prompt"]


def test_bodyweight_exercises_get_no_equipment_clause(_isolated):
    gen._handle_generate({"exerciseId": "Pullups", "poses": POSES})
    assert "must be drawn as body only" not in _isolated[0]["prompt"]


def test_prompt_carries_the_fixed_camera_clause(_isolated):
    gen._handle_generate({"exerciseId": "Pullups", "poses": POSES})
    prompt = _isolated[0]["prompt"]
    assert "IDENTICAL scale" in prompt or "identical" in prompt.lower()
    assert "three-panel" in prompt.lower() or "THREE-PANEL" in prompt


def test_prompt_includes_the_given_poses(_isolated):
    gen._handle_generate({"exerciseId": "Pullups", "poses": POSES})
    for pose in POSES:
        assert pose in _isolated[0]["prompt"]


def test_lying_camera_defaults_to_the_full_anchor(monkeypatch, _isolated):
    seen = {}
    real = gen.build_demo_gif

    def spy(strip, out, **kw):
        seen.update(kw)
        return real(strip, out, **kw)

    monkeypatch.setattr(gen, "build_demo_gif", spy)
    gen._handle_generate({"exerciseId": "Pullups", "poses": POSES, "camera": "lying"})
    assert seen.get("anchor") == "full"


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------

def test_registered_in_the_exercise_toolset():
    from tools.registry import registry

    entry = registry.get_entry("exercise_generate_demo")
    assert entry is not None
    assert entry.toolset == "exercise"


def test_declared_in_the_toolset_list():
    from toolsets import TOOLSETS

    assert "exercise_generate_demo" in TOOLSETS["exercise"]["tools"]


def test_cast_assets_ship_with_the_code():
    """The references must be in the repo, or the container cannot draw."""
    real_cast = gen.__file__.replace("tools/exercise_demo_generator.py",
                                     "assets/exercise_demos/cast")
    from pathlib import Path
    for member in gen.CAST:
        assert (Path(real_cast) / member["file"]).is_file(), member["file"]
