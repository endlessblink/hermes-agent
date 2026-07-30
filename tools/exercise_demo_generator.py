"""Generate an illustrated exercise demo GIF on demand, via the Codex CLI.

The bot draws its own demonstrations rather than shipping a fixed library. One
generation produces a three-panel pose strip; :mod:`tools.exercise_strip` slices
it into a centred looping GIF and writes it where ``exercise_demo`` already
looks, so the next request for the same exercise is served instantly from cache.

Why Codex rather than an image API: Codex's built-in ``image_generation`` tool
runs on the operator's ChatGPT login and uses gpt-image-2, so this needs no image
API key and no third-party provider. Codex is sandboxed to its working directory,
so generation happens in a temp dir and the result is copied out.

**One strip, not N images.** Generating each pose separately drifts in camera
distance and figure scale, so the subject visibly zooms between frames. Panels
inside a single image are drawn as one composition and match by construction —
and it is one generation instead of three.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from tools.exercise_library_tool import (
    _by_id,
    add_custom_exercise,
    demo_path,
    is_illustrated,
    mark_illustrated,
)
from tools.exercise_strip import build_demo_gif
from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# Codex generation runs an image model; it is slow. Well above the observed
# 60-90s so a slow render is not mistaken for a failure.
CODEX_TIMEOUT_SECONDS = 420

# Panels per strip. More panels read as motion; fewer keep each panel wide enough
# for the figure to stay legible once the strip is sliced.
PREFERRED_POSES = 5
MAX_POSES = 6

CAST = (
    {
        "file": "01_male_navy.png",
        "look": "a muscular male athlete with short dark hair, bare-chested, "
                "wearing navy blue athletic gym shorts and white training shoes "
                "with white socks",
    },
    {
        "file": "02_female_maroon.png",
        "look": "an athletic female with dark hair tied back in a ponytail, wearing a "
                "fitted maroon athletic tank top, black knee-length training leggings, "
                "and white training shoes with white socks",
    },
    {
        "file": "03_male_green.png",
        "look": "a lean Black male athlete with short cropped hair and a short beard, "
                "wearing a fitted grey athletic training t-shirt, forest green athletic "
                "gym shorts, and white training shoes with white socks",
    },
)


def _cast_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "exercise_demos" / "cast"


def _cast_for(exercise_id: str) -> dict[str, Any]:
    """Pick a cast member deterministically, so an exercise always shows the
    same person while the library as a whole stays varied."""
    index = sum(ord(c) for c in exercise_id) % len(CAST)
    return CAST[index]


def _codex_available() -> bool:
    return shutil.which("codex") is not None


def _build_prompt(exercise: dict, poses: list[str], camera: str, look: str) -> str:
    primary = ", ".join(exercise.get("primaryMuscles") or []) or "the working muscles"
    secondary = ", ".join(exercise.get("secondaryMuscles") or [])
    equipment = str(exercise.get("equipment") or "").strip()
    ground = ("the body on the same horizontal line" if camera == "lying"
              else "feet planted on the same ground line")

    equipment_clause = ""
    if equipment and equipment.lower() not in ("body only", "none", "other"):
        equipment_clause = (
            f"\nThe equipment must be drawn as {equipment} and nothing else — "
            f"never substitute a different implement.\n"
        )

    secondary_clause = (
        f" {secondary.upper()} in a lighter red as assisting muscles."
        if secondary else ""
    )

    n = len(poses)
    words = {3: "THREE", 4: "FOUR", 5: "FIVE", 6: "SIX", 7: "SEVEN", 8: "EIGHT"}
    count_word = words.get(n, str(n))
    # Positional hints only make sense at the ends; the middle panels are simply
    # numbered, which also keeps the prompt correct for any panel count.
    panel_lines = "\n".join(
        f"Panel {i + 1}"
        f"{' (leftmost)' if i == 0 else ' (rightmost)' if i == n - 1 else ''}: {pose}"
        for i, pose in enumerate(poses)
    )

    return f"""Using the attached reference image as the exact locked style and character: \
the SAME {look}. Same face, same build, same precise ink linework and neutral flesh \
tone, same plain flat white background, same anatomical fitness training-manual \
illustration style.

Draw a {count_word}-PANEL SEQUENCE STRIP on one continuous sheet, {count_word.lower()} \
equal side-by-side panels separated by thin vertical rules, showing one repetition of \
{exercise.get('name')} from a fixed {'side-on' if camera != 'front' else 'front-on'} camera:
{panel_lines}

The change between consecutive panels must be SMALL and EVEN — {count_word.lower()} equal \
steps of one continuous movement, not {count_word.lower()} unrelated poses. Consecutive \
panels should look almost the same, so that flipping through them reads as smooth motion.

Shade the working muscles anatomically: {primary.upper()} in deep anatomical red as \
the prime mover.{secondary_clause} All other muscles stay neutral flesh tone. The same \
muscles must be shaded identically in every panel.
{equipment_clause}
CRITICAL: identical figure scale and identical distance from the camera in all \
{count_word.lower()} panels, {ground} at the same height in every panel, and the athlete \
standing in the SAME horizontal position within every panel — do not let the figure move \
sideways from panel to panel. The whole figure must be fully visible and identically \
framed in each panel, as if a fixed camera photographed {count_word.lower()} moments. \
Same lighting and same white background in every panel.

No text, no numbers, no labels, no arrows, no logos, no watermark. \
Use the widest landscape aspect ratio available, roughly {n * 2}:3, so that all \
{count_word.lower()} panels sit side by side on one row and none is cropped."""


def _newest_png(*dirs: Path, newer_than: float) -> Path | None:
    best, best_mtime = None, newer_than
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.rglob("*.png"):
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if m > best_mtime and p.stat().st_size > 10_000:
                best, best_mtime = p, m
    return best


SIGNED_OUT_MESSAGE = (
    "the drawing service is signed out — an operator needs to run "
    "`codex login` on the server to sign in again"
)


def _login_problem(codex_home: Path) -> str:
    """Return an operator-facing message if the ChatGPT login is unusable.

    Worth checking up front because the alternative is what production actually
    produced: a raw ``codex produced no image (exit 1)`` dump quoting an unrelated
    temp-dir permission warning, which tells nobody what to do. An empty string
    means the login looks usable.

    Note that a past-expiry access token is *not* a failure — Codex refreshes it
    on use. It only becomes one when the refresh itself is rejected, which shows
    up in the process output and is matched after the run.
    """
    auth = codex_home / "auth.json"
    if not auth.is_file():
        return SIGNED_OUT_MESSAGE
    try:
        import json

        tokens = (json.loads(auth.read_text(encoding="utf-8")) or {}).get("tokens") or {}
    except Exception as exc:
        logger.warning("codex auth.json at %s is unreadable: %s", auth, exc)
        return SIGNED_OUT_MESSAGE
    if not tokens.get("refresh_token"):
        return SIGNED_OUT_MESSAGE

    expiry = _token_expiry(tokens.get("id_token"))
    if expiry is not None and expiry < time.time():
        # Only a warning: this is the normal state between refreshes, but when a
        # generation does fail it is the first thing an operator wants to see.
        logger.warning(
            "codex access token at %s expired %.0f minutes ago; a refresh is due",
            auth, (time.time() - expiry) / 60.0,
        )
    return ""


def _token_expiry(id_token: Any) -> float | None:
    """Seconds-since-epoch expiry encoded in a JWT, or None if unreadable."""
    if not isinstance(id_token, str) or id_token.count(".") != 2:
        return None
    try:
        import base64
        import json

        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return float(exp) if exp is not None else None
    except Exception:
        return None


def _run_codex(prompt: str, reference: Path, workdir: Path) -> tuple[Path | None, str]:
    """Ask Codex to generate the strip. Returns (png path, error message)."""
    started = time.time() - 1
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))

    problem = _login_problem(codex_home)
    if problem:
        return None, problem

    instruction = (
        f"{prompt}\n\n"
        f"Generate this image with your image_generation tool, then save the "
        f"resulting PNG as exactly 'strip.png' in your current working directory. "
        f"Do not write any other files, do not explain, do not ask questions."
    )
    # The prompt goes on stdin, never as a positional argument. `-i/--image`
    # takes a *list* of files, so a trailing positional is parsed as another
    # image path: codex then reported "No prompt provided" and exited 1 with no
    # image. Codex reads instructions from stdin when no positional is given.
    argv = [
        "codex", "exec",
        "--cd", str(workdir),
        "--skip-git-repo-check",
        "--sandbox", "workspace-write",
        "-i", str(reference),
    ]
    try:
        proc = subprocess.run(
            argv, input=instruction, capture_output=True, text=True,
            timeout=CODEX_TIMEOUT_SECONDS, cwd=str(workdir),
        )
    except subprocess.TimeoutExpired:
        return None, f"codex timed out after {CODEX_TIMEOUT_SECONDS}s"
    except FileNotFoundError:
        return None, "the codex CLI is not installed in this environment"

    # Preferred location first, then anywhere Codex may have parked it.
    candidate = workdir / "strip.png"
    if candidate.is_file() and candidate.stat().st_size > 10_000:
        return candidate, ""
    found = _newest_png(workdir, codex_home / "generated_images", newer_than=started)
    if found:
        return found, ""

    combined = f"{proc.stderr or ''}\n{proc.stdout or ''}"
    # A dead ChatGPT login is the one failure an operator must act on, and the
    # raw codex dump buries it. Surface it as something actionable instead.
    if re.search(
        r"refresh token|could not be refreshed|log out and sign in|401 Unauthorized"
        # The account is shared across several Codex homes, and each one that
        # refreshes rotates the chain and revokes the others. That arrives as
        # token_revoked / invalid_grant rather than a plain 401.
        r"|token_revoked|invalid_grant|not logged in|please (?:re-?)?login",
        combined,
        re.I,
    ):
        return None, SIGNED_OUT_MESSAGE
    tail = combined.strip()[-400:]
    return None, f"codex produced no image (exit {proc.returncode}): {tail}"


def _handle_generate(args: dict, **_kwargs) -> str:
    try:
        exercise_id = str(args.get("exerciseId") or "").strip()
        if not exercise_id:
            return tool_error("exerciseId is required")
        exercise = _by_id(exercise_id)
        if exercise is None:
            # The dataset is large but not complete — a kettlebell halo is not
            # among its 873 entries. Refusing here meant the bot could explain a
            # movement perfectly and still be unable to draw it, which is what a
            # user actually asks for. Supply a name and the movement joins the
            # library, so the drawing has somewhere to live and every later
            # request finds it.
            name = str(args.get("name") or "").strip()
            if not name:
                return tool_error(
                    f"no exercise with id {exercise_id!r} in the library. If this "
                    f"movement is genuinely missing, call again with 'name' set "
                    f"to its English name and it will be added and drawn.",
                    unknownId=exercise_id,
                )
            exercise = add_custom_exercise({
                "id": exercise_id,
                "name": name,
                "primaryMuscles": [
                    str(m).strip().lower()
                    for m in (args.get("primaryMuscles") or []) if str(m).strip()
                ],
                "secondaryMuscles": [],
                "equipment": str(args.get("equipment") or "body only"),
                "level": "intermediate",
                "category": "strength",
                "instructions": [],
                "custom": True,
            })

        out_path = demo_path(exercise)
        # Only a real drawing counts as "already done" — a stock-photo fallback
        # sitting at this path is exactly what the user wants replaced.
        if is_illustrated(exercise) and out_path.is_file() and not args.get("force"):
            return tool_result({
                "exerciseId": exercise["id"],
                "mediaPath": str(out_path),
                "mediaTag": f"MEDIA:{out_path}",
                "generated": False,
                "note": "Already drawn. Copy mediaTag onto its own line to send it.",
            })

        poses = [str(p).strip() for p in (args.get("poses") or []) if str(p).strip()]
        # Three poses produce three distinct pictures, which plays as a slideshow
        # rather than a movement — the first version of this library held each of
        # them for over a second and users read it as a still image that kept
        # resetting. Five is the working compromise: enough steps to read as
        # motion, few enough that each panel is still drawn wide enough to see.
        if not 3 <= len(poses) <= MAX_POSES:
            return tool_error(
                f"poses must be between 3 and {MAX_POSES} short descriptions — evenly "
                f"spaced moments through one repetition, taken from the exercise's "
                f"instructions. {PREFERRED_POSES} reads as smooth motion; 3 looks like "
                f"a slideshow."
            )

        if not _codex_available():
            return tool_error(
                "image generation is unavailable here (the codex CLI is not installed)"
            )

        member = _cast_for(str(exercise["id"]))
        reference = _cast_dir() / member["file"]
        if not reference.is_file():
            return tool_error(f"cast reference missing: {reference.name}")

        camera = str(args.get("camera") or "side").lower()
        # Default to letting the assembler measure both anchors and keep the
        # steadier one. Hand-picking by camera angle was wrong more often than it
        # was right — "feet" is the intuitive choice for a standing lift, and it
        # was the losing choice on four of the five shakiest demos in the library.
        anchor = str(args.get("anchor") or "auto").lower()
        if anchor not in ("feet", "full", "auto"):
            anchor = "auto"

        prompt = _build_prompt(exercise, poses, camera, member["look"])

        with tempfile.TemporaryDirectory(prefix="exdemo-") as tmp:
            workdir = Path(tmp)
            strip, err = _run_codex(prompt, reference, workdir)
            if strip is None:
                logger.warning("exercise_generate_demo: %s", err)
                return tool_error(f"could not draw this exercise: {err}")

            try:
                result = build_demo_gif(
                    strip, out_path, panels=len(poses), anchor=anchor,
                    label=str(exercise.get("name") or exercise_id),
                )
            except Exception as exc:
                logger.warning("strip assembly failed for %s: %s", exercise_id, exc)
                return tool_error(f"the drawing came out unusable: {exc}")

            # Keep the strip next to the GIF: re-timing or re-anchoring it later
            # is free, whereas regenerating costs another image generation.
            try:
                shutil.copy2(strip, out_path.with_name(out_path.stem + "_strip.png"))
            except OSError:
                pass

        # Record that this path now holds a drawing rather than a stock photo.
        # Written only after the GIF exists, so a failed render never leaves the
        # exercise wrongly marked as illustrated.
        mark_illustrated(exercise)

        payload = {
            "exerciseId": exercise["id"],
            "name": exercise.get("name"),
            "mediaPath": result["path"],
            "mediaTag": f"MEDIA:{result['path']}",
            "generated": True,
            "driftPx": result["driftPx"],
            "note": (
                "Copy mediaTag onto its own line in your reply, exactly as given, to "
                "send it. It is cached now, so showing this exercise again is instant."
            ),
        }
        if result["drifts"]:
            payload["warning"] = (
                "The figure shifts position between frames, so the animation will look "
                "unsteady. Send it anyway, but do not regenerate more than once."
            )
        return tool_result(payload)
    except Exception as exc:
        logger.warning("exercise_generate_demo failed: %s", exc)
        return tool_error(f"demo generation failed: {exc}")


GENERATE_SCHEMA = {
    "name": "exercise_generate_demo",
    "description": (
        "Draw an animated demonstration for an exercise that does not have one yet, "
        "and cache it. Takes 1-2 minutes and costs a generation, so tell the user it "
        "is being drawn before calling, and only call it when they actually want to "
        "see the movement. Call exercise_demo first — if that returns a demo, this is "
        "not needed. You must supply the pose stages yourself, taken from the "
        "exercise's own instructions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "exerciseId": {
                "type": "string",
                "description": (
                    "Exercise id from exercise_find. For a movement the library "
                    "does not have, invent a stable id in the same style "
                    "(Kettlebell_Halo) and also pass 'name'."
                ),
            },
            "name": {
                "type": "string",
                "description": (
                    "Only for a movement exercise_find could not find. Its English "
                    "name. Supplying it adds the movement to the library and draws "
                    "it, instead of refusing. Do not pass this for an exercise that "
                    "already exists — you would create a duplicate."
                ),
            },
            "primaryMuscles": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Only with 'name'. The muscles the movement works, in English, "
                    "so the drawing can shade them."
                ),
            },
            "equipment": {
                "type": "string",
                "description": (
                    "Only with 'name'. What the movement is performed with, e.g. "
                    "kettlebell. Defaults to body only."
                ),
            },
            "poses": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": MAX_POSES,
                "description": (
                    f"Short English descriptions of body position at evenly spaced "
                    f"moments through one repetition, from the start to the end. "
                    f"Supply {PREFERRED_POSES} unless the movement is very simple — "
                    f"consecutive poses should differ only slightly, because that is "
                    f"what makes the finished demo look like motion instead of a "
                    f"slideshow. Derive them from the exercise's instructions."
                ),
            },
            "camera": {
                "type": "string",
                "enum": ["side", "front", "lying"],
                "description": (
                    "side for most standing exercises; front for presses and pulls "
                    "where the working side faces the viewer; lying for anything "
                    "performed on a bench or the floor."
                ),
            },
            "anchor": {
                "type": "string",
                "enum": ["feet", "full"],
                "description": (
                    "How frames are lined up. feet for standing exercises; full for "
                    "lying, seated or hanging ones. Defaults from camera."
                ),
            },
            "force": {
                "type": "boolean",
                "description": "Redraw even if a demo already exists. Costs a generation.",
            },
        },
        "required": ["exerciseId", "poses"],
        "additionalProperties": False,
    },
}


registry.register(
    name="exercise_generate_demo",
    toolset="exercise",
    schema=GENERATE_SCHEMA,
    handler=_handle_generate,
    check_fn=_codex_available,
    emoji="🎨",
)
