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

# Captured before the autouse fixture stubs it, so the invocation tests below
# exercise the real subprocess call rather than the stub.
_REAL_RUN_CODEX = gen._run_codex


@pytest.fixture
def signed_in_codex_home(monkeypatch, tmp_path):
    """Point CODEX_HOME at a home that looks signed in.

    The generator now refuses to shell out when the ChatGPT login is missing, so
    tests that want to reach the subprocess have to say they are signed in
    rather than inheriting whatever the machine running the suite happens to
    have in ~/.codex.
    """
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "auth.json").write_text(
        json.dumps({"tokens": {"refresh_token": "rt", "account_id": "acct"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


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
    # Spread the crouch across however many panels there are, so the last one
    # still has a body rather than collapsing to zero height.
    step = 120 // max(1, panels - 1)
    for i in range(panels):
        top = 60 + i * step  # crouches lower each panel
        for x in range(i * pw + pw // 4, i * pw + pw // 4 + max(20, pw // 3)):
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

    # Patch at the source: demo_path / is_illustrated / mark_illustrated all
    # resolve the directory through exercise_library_tool at call time.
    from tools import exercise_library_tool as lib

    monkeypatch.setattr(lib, "_gif_dir", lambda: gif_dir)
    monkeypatch.setattr(gen, "_cast_dir", lambda: cast_dir)
    monkeypatch.setattr(gen, "_codex_available", lambda: True)
    monkeypatch.setattr(gen, "_by_id", lambda i: {"Pullups": PULLUPS,
                                                  "Barbell_Squat": SQUAT}.get(i))

    calls = []

    def fake_codex(prompt, reference, workdir):
        calls.append({"prompt": prompt, "reference": str(reference)})
        # Draw as many panels as the prompt asked for. A fixed three-panel stub
        # would make every wider strip fail slicing for a reason the real
        # pipeline never hits.
        panels = sum(
            1 for i in range(1, gen.MAX_POSES + 1) if f"Panel {i}" in prompt
        ) or 3
        return _strip_image(workdir / "strip.png", panels=panels), ""

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
    """A ready-to-copy tag, because the bot once messaged bare paths as text."""
    result = _payload(gen._handle_generate({"exerciseId": "Pullups", "poses": POSES}))
    assert result["mediaTag"] == f"MEDIA:{result['mediaPath']}"
    assert "mediaTag" in result["note"]


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


@pytest.mark.parametrize("poses", [[], ["only one"], ["a", "b"], ["p"] * (gen.MAX_POSES + 1)])
def test_rejects_pose_counts_outside_the_supported_range(poses):
    parsed = json.loads(gen._handle_generate({"exerciseId": "Pullups", "poses": poses}))
    assert "error" in parsed
    assert "3" in parsed["error"]


@pytest.mark.parametrize("count", range(3, gen.MAX_POSES + 1))
def test_more_poses_than_three_are_accepted(count):
    """Three poses is three pictures, which plays as a slideshow. Extra panels
    are the only way to buy smoother motion — interpolation cannot bridge poses
    this far apart, it just duplicates frames."""
    parsed = json.loads(
        gen._handle_generate({"exerciseId": "Pullups", "poses": ["pose"] * count})
    )
    assert "error" not in parsed, parsed


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
# how codex is invoked — both faults here were seen in production
# --------------------------------------------------------------------------

def test_the_prompt_is_never_passed_as_a_positional_argument(
    monkeypatch, tmp_path, signed_in_codex_home,
):
    """`-i/--image` takes a LIST of files, so a trailing positional prompt is
    parsed as another image path. Codex then reported "No prompt provided",
    exited 1, and drew nothing. The prompt must go on stdin."""
    seen = {}

    class _Proc:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["input"] = kwargs.get("input")
        (tmp_path / "strip.png").write_bytes(b"x" * 20_000)
        return _Proc()

    monkeypatch.setattr(gen.subprocess, "run", fake_run)
    _REAL_RUN_CODEX("DRAW THIS", tmp_path / "ref.png", tmp_path)

    assert "DRAW THIS" in (seen["input"] or ""), "the prompt must be fed on stdin"
    assert seen["argv"][-2] == "-i", "the image flag must be last, with nothing after its value"
    assert not any("DRAW THIS" in a for a in seen["argv"]), (
        "the prompt must not appear in argv, or -i swallows it as an image"
    )


def test_a_missing_login_is_reported_without_shelling_out(monkeypatch, tmp_path):
    """No login means no point starting codex — and the raw dump it produces
    when it fails names a temp-dir permission warning, not the real problem."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-home"))

    def _must_not_run(*a, **kw):
        raise AssertionError("codex must not be started without a login")

    monkeypatch.setattr(gen.subprocess, "run", _must_not_run)

    path, err = _REAL_RUN_CODEX("prompt", tmp_path / "ref.png", tmp_path)
    assert path is None
    assert "signed out" in err and "codex login" in err


def test_an_expired_access_token_alone_does_not_block_a_draw(
    monkeypatch, tmp_path, signed_in_codex_home,
):
    """Codex refreshes on use, so a stale access token is the normal state
    between refreshes — blocking on it would refuse perfectly good draws."""
    import base64

    expired = base64.urlsafe_b64encode(b'{"exp": 1}').decode().rstrip("=")
    (signed_in_codex_home / "auth.json").write_text(
        json.dumps({"tokens": {"refresh_token": "rt", "id_token": f"h.{expired}.s"}}),
        encoding="utf-8",
    )

    class _Proc:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kwargs):
        (tmp_path / "strip.png").write_bytes(b"x" * 20_000)
        return _Proc()

    monkeypatch.setattr(gen.subprocess, "run", fake_run)

    path, err = _REAL_RUN_CODEX("prompt", tmp_path / "ref.png", tmp_path)
    assert err == ""
    assert path is not None


@pytest.mark.parametrize("stderr", [
    "ERROR: Your access token could not be refreshed. Please log out and sign in again.",
    "failed to connect to websocket: HTTP error: 401 Unauthorized",
    "your refresh token was already used",
    # The account is shared across several Codex homes; whichever one refreshes
    # last revokes the rest, and that arrives worded like this.
    "unexpected status 401 Unauthorized: {\"error\": \"token_revoked\"}",
    "oauth token exchange failed: invalid_grant",
])
def test_an_expired_login_is_reported_as_something_an_operator_can_fix(
    monkeypatch, tmp_path, stderr, signed_in_codex_home,
):
    """The raw codex dump buries the one failure that needs human action."""
    class _Proc:
        returncode = 1
        stdout = ""

    _Proc.stderr = stderr
    monkeypatch.setattr(gen.subprocess, "run", lambda argv, **kw: _Proc())

    path, err = _REAL_RUN_CODEX("prompt", tmp_path / "ref.png", tmp_path)
    assert path is None
    assert "signed out" in err
    assert "codex login" in err


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


def test_the_anchor_is_measured_rather_than_guessed_from_the_camera(monkeypatch, _isolated):
    """Picking the anchor by camera angle was wrong more often than right.

    "feet" is the intuitive choice for a standing lift, and it was the losing
    choice on four of the five shakiest demos in the library — held equipment and
    a stance that widens through the movement drag the bottom-band correlation.
    The assembler already measures the residual drift, so it decides.
    """
    seen = {}
    real = gen.build_demo_gif

    def spy(strip, out, **kw):
        seen.update(kw)
        return real(strip, out, **kw)

    monkeypatch.setattr(gen, "build_demo_gif", spy)
    gen._handle_generate({"exerciseId": "Pullups", "poses": POSES, "camera": "lying"})
    assert seen.get("anchor") == "auto"


def test_an_explicit_anchor_is_still_honoured(monkeypatch, _isolated):
    seen = {}
    real = gen.build_demo_gif

    def spy(strip, out, **kw):
        seen.update(kw)
        return real(strip, out, **kw)

    monkeypatch.setattr(gen, "build_demo_gif", spy)
    gen._handle_generate({
        "exerciseId": "Pullups", "poses": POSES, "camera": "lying", "anchor": "full",
    })
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


def _offline_library(monkeypatch, tmp_path):
    """Point the library at a local dataset so no test touches the network."""
    from tools import exercise_library_tool as lib

    monkeypatch.setattr(lib, "_data_dir", lambda: tmp_path)
    (tmp_path / "exercises.json").write_text(json.dumps([PULLUPS]), encoding="utf-8")
    monkeypatch.setattr(lib, "_dataset_cache", None, raising=False)
    return lib

# --------------------------------------------------------------------------
# movements the dataset does not have
# --------------------------------------------------------------------------

def test_a_movement_missing_from_the_dataset_can_still_be_drawn(monkeypatch, tmp_path):
    """The dataset has 873 entries and no kettlebell halo. Refusing to draw one
    meant the bot could explain a movement perfectly and still have no way to
    show it — which is the thing the user actually asked for."""
    lib = _offline_library(monkeypatch, tmp_path)

    parsed = json.loads(gen._handle_generate({
        "exerciseId": "Kettlebell_Halo",
        "name": "Kettlebell Halo",
        "primaryMuscles": ["shoulders"],
        "equipment": "kettlebell",
        "poses": POSES,
    }))

    assert "error" not in parsed, parsed
    assert parsed["exerciseId"] == "Kettlebell_Halo"
    # And it is now a library member, so asking again finds it.
    assert lib._by_id("Kettlebell_Halo") is not None


def test_an_unknown_id_without_a_name_says_how_to_proceed(monkeypatch, tmp_path):
    """Silently inventing an exercise from a typo'd id would fill the library
    with junk, so the name has to be deliberate — but the refusal must say so."""
    _offline_library(monkeypatch, tmp_path)

    parsed = json.loads(gen._handle_generate({
        "exerciseId": "Kettlebell_Wibble", "poses": POSES,
    }))

    assert "error" in parsed
    assert "name" in parsed["error"]
