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
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from tools.exercise_library_tool import (
    _by_id,
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

    return f"""Using the attached reference image as the exact locked style and character: \
the SAME {look}. Same face, same build, same precise ink linework and neutral flesh \
tone, same plain flat white background, same anatomical fitness training-manual \
illustration style.

Draw a THREE-PANEL SEQUENCE STRIP on one continuous sheet, three equal side-by-side \
panels separated by thin vertical rules, showing one repetition of \
{exercise.get('name')} from a fixed {'side-on' if camera != 'front' else 'front-on'} camera:
Panel 1 (left): {poses[0]}
Panel 2 (centre): {poses[1]}
Panel 3 (right): {poses[2]}

The change between consecutive panels must be SMALL and EVEN — three equal steps of \
one continuous movement, not three unrelated poses.

Shade the working muscles anatomically: {primary.upper()} in deep anatomical red as \
the prime mover.{secondary_clause} All other muscles stay neutral flesh tone. The same \
muscles must be shaded identically in all three panels.
{equipment_clause}
CRITICAL: identical figure scale and identical distance from the camera in all three \
panels, {ground} at the same height in every panel, the whole figure fully visible and \
identically framed in each panel, as if a fixed camera photographed three moments. \
Same lighting and same white background in every panel.

No text, no numbers, no labels, no arrows, no logos, no watermark. \
Use a wide 21:9 landscape aspect ratio."""


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


def _run_codex(prompt: str, reference: Path, workdir: Path) -> tuple[Path | None, str]:
    """Ask Codex to generate the strip. Returns (png path, error message)."""
    started = time.time() - 1
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))

    instruction = (
        f"{prompt}\n\n"
        f"Generate this image with your image_generation tool, then save the "
        f"resulting PNG as exactly 'strip.png' in your current working directory. "
        f"Do not write any other files, do not explain, do not ask questions."
    )
    argv = [
        "codex", "exec",
        "--cd", str(workdir),
        "--skip-git-repo-check",
        "--sandbox", "workspace-write",
        "-i", str(reference),
        instruction,
    ]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True,
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

    tail = (proc.stderr or proc.stdout or "").strip()[-400:]
    return None, f"codex produced no image (exit {proc.returncode}): {tail}"


def _handle_generate(args: dict, **_kwargs) -> str:
    try:
        exercise_id = str(args.get("exerciseId") or "").strip()
        if not exercise_id:
            return tool_error("exerciseId is required")
        exercise = _by_id(exercise_id)
        if exercise is None:
            return tool_error(f"no exercise with id {exercise_id!r} in the library")

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
        if len(poses) != 3:
            return tool_error(
                "poses must be exactly 3 short descriptions — the start, the midpoint "
                "and the end of one repetition, taken from the exercise's instructions"
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
        anchor = str(args.get("anchor") or ("full" if camera == "lying" else "feet")).lower()
        if anchor not in ("feet", "full"):
            anchor = "feet"

        prompt = _build_prompt(exercise, poses, camera, member["look"])

        with tempfile.TemporaryDirectory(prefix="exdemo-") as tmp:
            workdir = Path(tmp)
            strip, err = _run_codex(prompt, reference, workdir)
            if strip is None:
                logger.warning("exercise_generate_demo: %s", err)
                return tool_error(f"could not draw this exercise: {err}")

            try:
                result = build_demo_gif(strip, out_path, anchor=anchor)
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
        "not needed. You must supply the three pose stages yourself, taken from the "
        "exercise's own instructions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "exerciseId": {
                "type": "string",
                "description": "Exercise id from exercise_find.",
            },
            "poses": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": 3,
                "description": (
                    "Exactly three short English descriptions of body position: the "
                    "start, a true midpoint, and the end of one repetition. Derive "
                    "them from the exercise's instructions, and make the step between "
                    "each one roughly equal."
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
