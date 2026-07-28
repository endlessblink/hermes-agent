"""Free exercise library: fuzzy lookup, animated demos, and workout building.

Data comes from the public-domain `free-exercise-db` dataset (873 exercises with
instructions, muscles, equipment, and two photos each: a start frame and an end
frame). There is no API key, no rate limit, and no cost.

The dataset ships still photos rather than filmed GIFs, so :func:`_build_gif`
crossfades the start frame into the end frame with Pillow to produce a looping
animation. Pillow is already a core dependency, so this adds no new packages.

GIFs are written under the image cache (``cache/images/exercises``) because that
root is already allowlisted for media delivery — the model can hand the returned
path straight to the ``MEDIA:<path>`` convention and the platform layer will
deliver it natively (``send_animation`` on Telegram).
"""

from __future__ import annotations

import difflib
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Iterable

from hermes_constants import get_hermes_dir
from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

DATASET_URL = (
    "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/dist/exercises.json"
)
IMAGE_BASE_URL = "https://cdn.jsdelivr.net/gh/yuhonas/free-exercise-db@main/exercises/"

# The dataset is effectively static; a month between refreshes is plenty.
DATASET_TTL_SECONDS = 30 * 24 * 60 * 60
_HTTP_TIMEOUT_SECONDS = 30.0

# GIF rendering. Telegram animations look best small and looping; 480px wide
# keeps a two-frame crossfade comfortably under a megabyte.
GIF_MAX_WIDTH = 480
_GIF_HOLD_MS = 420          # dwell on the start and end positions
_GIF_BLEND_MS = 70          # per crossfade step
_GIF_BLEND_STEPS = 6

MUSCLES = (
    "abdominals", "abductors", "adductors", "biceps", "calves", "chest",
    "forearms", "glutes", "hamstrings", "lats", "lower back", "middle back",
    "neck", "quadriceps", "shoulders", "traps", "triceps",
)
EQUIPMENT = (
    "bands", "barbell", "body only", "cable", "dumbbell", "e-z curl bar",
    "exercise ball", "foam roll", "kettlebells", "machine", "medicine ball",
    "other",
)
LEVELS = ("beginner", "intermediate", "expert")
CATEGORIES = (
    "strength", "stretching", "plyometrics", "powerlifting",
    "olympic weightlifting", "strongman", "cardio",
)

_LEVEL_ORDER = {"beginner": 0, "intermediate": 1, "expert": 2}

# Goal keywords the model can pass instead of naming individual muscles.
FOCUS_GROUPS: dict[str, tuple[str, ...]] = {
    "full body": ("quadriceps", "chest", "lats", "shoulders", "hamstrings", "abdominals"),
    "upper body": ("chest", "lats", "shoulders", "biceps", "triceps", "middle back"),
    "lower body": ("quadriceps", "hamstrings", "glutes", "calves"),
    "legs": ("quadriceps", "hamstrings", "glutes", "calves"),
    "push": ("chest", "shoulders", "triceps"),
    "pull": ("lats", "middle back", "biceps", "traps"),
    "core": ("abdominals", "lower back"),
    "abs": ("abdominals",),
    "arms": ("biceps", "triceps", "forearms"),
    "back": ("lats", "middle back", "lower back", "traps"),
    "chest": ("chest",),
    "shoulders": ("shoulders",),
    "glutes": ("glutes",),
}

# The exercise bot converses in Hebrew. The model normally translates before
# calling, but Hebrew tokens leak through often enough that mapping the common
# ones is cheaper than a failed lookup.
_HEBREW_TERMS: dict[str, str] = {
    "חזה": "chest", "גב": "back", "כתפיים": "shoulders", "כתף": "shoulders",
    "יד קדמית": "biceps", "יד אחורית": "triceps", "בייספס": "biceps",
    "טרייספס": "triceps", "ידיים": "arms", "רגליים": "legs", "רגל": "legs",
    "ארבע ראשי": "quadriceps", "ירך אחורית": "hamstrings", "תאומים": "calves",
    "ישבן": "glutes", "בטן": "abdominals", "ליבה": "core", "מותן": "lower back",
    "משקולת": "dumbbell", "משקולות": "dumbbell", "מוט": "barbell",
    "גומייה": "bands", "גומיות": "bands", "כבל": "cable", "מכונה": "machine",
    "קטלבל": "kettlebells", "משקל גוף": "body only", "בלי ציוד": "body only",
    "מתחיל": "beginner", "מתקדם": "expert", "בינוני": "intermediate",
    "מתיחה": "stretching", "מתיחות": "stretching", "כוח": "strength",
    "אירובי": "cardio", "סקוואט": "squat", "לחיצת חזה": "bench press",
    "מתח": "pull-up", "שכיבות סמיכה": "push-up",
}

_WORD_RE = re.compile(r"[a-z0-9]+")

# Tokens that carry no discriminating signal in a free-text description.
# "from", "over", and "under" are deliberately absent — they are load-bearing in
# exercise names ("Bent Over Row", "Clean From The Hang").
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "with", "your", "you", "on",
    "in", "for", "is", "it", "that", "this", "exercise", "move", "movement",
    "where", "when", "while", "one", "at", "as", "be", "do", "i", "me", "my",
    "we", "what", "how", "why", "which", "who", "does", "did", "can", "could",
    "should", "would", "about", "some", "any", "get", "got", "make", "like",
    "want", "need", "know", "call", "called", "name", "named", "there",
})

# Real matches score 10-25; an accidental one-word overlap with an instruction
# scores about 2. Anything under this floor is noise, and showing the user a
# confidently wrong exercise is worse than saying nothing.
MIN_MATCH_SCORE = 5.0
# Above this, the top hit is a solid match rather than a best-effort guess.
CONFIDENT_MATCH_SCORE = 12.0

_dataset_cache: list[dict[str, Any]] | None = None


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def _data_dir() -> Path:
    """Directory holding the dataset JSON and downloaded source frames."""
    path = get_hermes_dir("cache/exercises", "exercise_cache")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _gif_dir() -> Path:
    """Directory for rendered GIFs.

    Lives under the image cache so the path is already inside a media-delivery
    safe root (see ``MEDIA_DELIVERY_SAFE_ROOTS`` in ``gateway/platforms/base``).
    """
    path = get_hermes_dir("cache/images", "image_cache") / "exercises"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fetch_bytes(url: str) -> bytes:
    import httpx

    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.content


def _load_dataset(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Return the exercise dataset, downloading it once and caching on disk.

    A stale cached copy is preferred over an error: the network is only needed
    on first use, and the data does not change.
    """
    global _dataset_cache
    if _dataset_cache is not None and not force_refresh:
        return _dataset_cache

    cache_path = _data_dir() / "exercises.json"
    fresh = (
        cache_path.is_file()
        and (time.time() - cache_path.stat().st_mtime) < DATASET_TTL_SECONDS
    )

    if fresh and not force_refresh:
        try:
            cached: list[dict[str, Any]] = json.loads(cache_path.read_text(encoding="utf-8"))
            _dataset_cache = cached
            return cached
        except (OSError, ValueError) as exc:
            logger.warning("Exercise dataset cache unreadable, refetching: %s", exc)

    try:
        raw = _fetch_bytes(DATASET_URL)
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, list) or not parsed:
            raise ValueError("dataset did not contain an exercise list")
        cache_path.write_text(
            json.dumps(parsed, ensure_ascii=False), encoding="utf-8"
        )
        _dataset_cache = parsed
        return parsed
    except Exception as exc:
        if cache_path.is_file():
            logger.warning("Exercise dataset refresh failed, using cache: %s", exc)
            stale: list[dict[str, Any]] = json.loads(cache_path.read_text(encoding="utf-8"))
            _dataset_cache = stale
            return stale
        raise


def _by_id(exercise_id: str) -> dict[str, Any] | None:
    wanted = str(exercise_id).strip().lower()
    for exercise in _load_dataset():
        if str(exercise.get("id", "")).lower() == wanted:
            return exercise
    # Fall back to an exact name match so the model can pass either form.
    for exercise in _load_dataset():
        if str(exercise.get("name", "")).lower() == wanted:
            return exercise
    return None


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------

def _translate_hebrew(text: str) -> str:
    """Replace known Hebrew fitness terms with their English equivalents."""
    for hebrew, english in _HEBREW_TERMS.items():
        if hebrew in text:
            text = text.replace(hebrew, f" {english} ")
    return text


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS]


def _haystacks(exercise: dict[str, Any]) -> tuple[str, str, str]:
    """Return (name, muscles+equipment, instructions) as lowercase text."""
    name = str(exercise.get("name", "")).lower()
    attrs = " ".join(
        [
            *(exercise.get("primaryMuscles") or []),
            *(exercise.get("secondaryMuscles") or []),
            str(exercise.get("equipment") or ""),
            str(exercise.get("category") or ""),
            str(exercise.get("force") or ""),
            str(exercise.get("mechanic") or ""),
        ]
    ).lower()
    steps = " ".join(exercise.get("instructions") or []).lower()
    return name, attrs, steps


def _score(exercise: dict[str, Any], query: str, query_tokens: Iterable[str]) -> float:
    """Rank an exercise against a free-text query.

    Weighted so that a remembered *name* beats a remembered *description*, but a
    description still finds the exercise when the user has no name for it — the
    "the one where you hang and pull yourself up" case.
    """
    name, attrs, steps = _haystacks(exercise)
    tokens = list(query_tokens)
    if not tokens:
        return 0.0

    name_tokens = set(_WORD_RE.findall(name))
    attr_tokens = set(_WORD_RE.findall(attrs))
    step_tokens = set(_WORD_RE.findall(steps))

    token_score = 0.0
    for token in tokens:
        if token in name_tokens:
            token_score += 6.0
        elif len(token) >= 4 and any(
            # Both sides must be substantial — otherwise a one-letter word in an
            # exercise name ("Curl Over A Bench") is a substring of every long
            # token in the query and scores pure noise as a match.
            len(word) >= 4 and (token in word or word in token)
            for word in name_tokens
        ):
            token_score += 3.0
        if token in attr_tokens:
            # An exact hit on the muscle/equipment/category vocabulary is strong
            # evidence on its own — it is how a translated one-word query
            # ("chest", from Hebrew) has to clear MIN_MATCH_SCORE. Off-topic
            # words never appear in this closed vocabulary.
            token_score += 5.0
        if token in step_tokens:
            token_score += 1.0

    # Whole-phrase similarity rescues near-miss spellings ("lat pull down").
    name_ratio = difflib.SequenceMatcher(None, query.lower(), name).ratio()

    # Similarity alone is not evidence — every string is ~30% similar to every
    # other string, so without it the bot would confidently show a random
    # exercise for a query that matched nothing. Require either a real token
    # hit or a genuinely close name.
    if token_score <= 0.0 and name_ratio < 0.6:
        return 0.0

    score = token_score + 5.0 * name_ratio
    if query.lower() in name:
        score += 6.0
    return score


def _summarize(exercise: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "exerciseId": exercise.get("id"),
        "name": exercise.get("name"),
        "primaryMuscles": exercise.get("primaryMuscles") or [],
        "equipment": exercise.get("equipment"),
        "level": exercise.get("level"),
        "category": exercise.get("category"),
    }
    instructions = exercise.get("instructions") or []
    if full:
        summary["secondaryMuscles"] = exercise.get("secondaryMuscles") or []
        summary["mechanic"] = exercise.get("mechanic")
        summary["instructions"] = instructions
    elif instructions:
        summary["firstStep"] = instructions[0]
    return summary


def _matches_filters(exercise: dict[str, Any], args: dict[str, Any]) -> bool:
    muscle = args.get("muscle")
    if muscle:
        muscles = {
            m.lower()
            for m in (exercise.get("primaryMuscles") or [])
            + (exercise.get("secondaryMuscles") or [])
        }
        if str(muscle).lower() not in muscles:
            return False

    equipment = args.get("equipment")
    if equipment:
        allowed = {str(e).lower() for e in _as_list(equipment)}
        if str(exercise.get("equipment") or "").lower() not in allowed:
            return False

    category = args.get("category")
    if category and str(exercise.get("category") or "").lower() != str(category).lower():
        return False

    level = args.get("level")
    if level:
        cap = _LEVEL_ORDER.get(str(level).lower())
        actual = _LEVEL_ORDER.get(str(exercise.get("level") or "beginner").lower(), 0)
        if cap is not None and actual > cap:
            return False
    return True


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    return [str(value)]


def _handle_find(args: dict, **_kwargs) -> str:
    try:
        query = str(args.get("query") or "").strip()
        limit = max(1, min(int(args.get("limit") or 5), 15))
        dataset = _load_dataset()

        candidates = [ex for ex in dataset if _matches_filters(ex, args)]
        if not candidates:
            return tool_result(
                {
                    "matches": [],
                    "note": "No exercise matched those filters. Try relaxing equipment or level.",
                }
            )

        if not query:
            # Pure filter browse — return a stable alphabetical slice.
            ordered = sorted(candidates, key=lambda ex: str(ex.get("name", "")))[:limit]
            return tool_result(
                {"matches": [_summarize(ex) for ex in ordered], "totalMatching": len(candidates)}
            )

        normalized = _translate_hebrew(query)
        query_tokens = _tokens(normalized)
        scored = [
            (_score(ex, normalized, query_tokens), ex) for ex in candidates
        ]
        scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("name", ""))))
        top = [ex for score, ex in scored[:limit] if score >= MIN_MATCH_SCORE]

        if not top:
            return tool_result(
                {
                    "matches": [],
                    "note": "Nothing matched that description. Ask the user which muscle it works "
                            "or what equipment it uses, then search again.",
                }
            )

        confident = scored[0][0] >= CONFIDENT_MATCH_SCORE
        note = "Call exercise_demo with an exerciseId to show the user an animated demo."
        if not confident:
            note = (
                "These are loose guesses, not confident matches — the library may not "
                "contain this movement. Show the closest one but say it is a guess, or "
                "ask the user for another detail. " + note
            )

        return tool_result(
            {
                "matches": [_summarize(ex, full=(len(top) == 1)) for ex in top],
                "totalMatching": len(candidates),
                "confident": confident,
                "note": note,
            }
        )
    except Exception as exc:
        logger.warning("exercise_find failed: %s", exc)
        return tool_error(f"exercise lookup failed: {exc}")


# --------------------------------------------------------------------------
# demo rendering
# --------------------------------------------------------------------------

def _download_frame(relative_path: str) -> Path:
    """Fetch one source photo, caching it on disk."""
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", relative_path)
    local = _data_dir() / "frames" / safe_name
    local.parent.mkdir(parents=True, exist_ok=True)
    if local.is_file() and local.stat().st_size > 0:
        return local
    local.write_bytes(_fetch_bytes(IMAGE_BASE_URL + relative_path))
    return local


def _safe_id(exercise: dict[str, Any]) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(exercise.get("id")))


def demo_path(exercise: dict[str, Any]) -> Path:
    """The one and only path a demo for this exercise ever lives at.

    Deliberately stable whichever kind of demo it holds. Chats here are a single
    unbroken thread, so any path the bot has ever emitted stays in its history
    forever and it will reuse one. Moving a file to mark it as a different kind
    silently broke delivery for every stale path — the gateway rejected the now
    missing file and the user got text with no picture.

    Provenance is therefore recorded beside the file, not in its location.
    """
    return _gif_dir() / f"{_safe_id(exercise)}.gif"


def _illustrated_marker(exercise: dict[str, Any]) -> Path:
    return _gif_dir() / f"{_safe_id(exercise)}.illustrated"


def is_illustrated(exercise: dict[str, Any]) -> bool:
    """True when the demo at ``demo_path`` is a real drawing, not a stock photo."""
    return _illustrated_marker(exercise).is_file()


def mark_illustrated(exercise: dict[str, Any]) -> None:
    """Record that the demo at ``demo_path`` is a drawing. Called after generating."""
    _illustrated_marker(exercise).write_text("generated\n", encoding="utf-8")


def _build_gif(exercise: dict[str, Any]) -> Path:
    """Render a looping GIF that crossfades the start frame into the end frame.

    This is the *fallback*: the dataset gives two stills per exercise, and
    blending between them costs nothing. It reads as a rough repetition but it is
    a photograph of a stranger, not the illustrated library. A generated drawing
    overwrites it in place and drops the marker beside it, so the path an old
    message points at keeps working and quietly improves.
    """
    from PIL import Image

    exercise_id = str(exercise.get("id"))

    out_path = demo_path(exercise)
    if out_path.is_file() and out_path.stat().st_size > 0:
        return out_path

    images = exercise.get("images") or []
    if not images:
        raise ValueError(f"{exercise_id} has no images")

    frames_src = [Image.open(_download_frame(rel)).convert("RGB") for rel in images[:2]]
    if len(frames_src) == 1:
        frames_src.append(frames_src[0].copy())

    width = min(GIF_MAX_WIDTH, frames_src[0].width)
    height = round(frames_src[0].height * width / frames_src[0].width)
    start, end = (img.resize((width, height), Image.Resampling.LANCZOS) for img in frames_src)

    frames: list[Image.Image] = [start]
    durations: list[int] = [_GIF_HOLD_MS]
    for step in range(1, _GIF_BLEND_STEPS):
        frames.append(Image.blend(start, end, step / _GIF_BLEND_STEPS))
        durations.append(_GIF_BLEND_MS)
    frames.append(end)
    durations.append(_GIF_HOLD_MS)
    for step in range(_GIF_BLEND_STEPS - 1, 0, -1):
        frames.append(Image.blend(start, end, step / _GIF_BLEND_STEPS))
        durations.append(_GIF_BLEND_MS)

    palette = [frame.convert("P", palette=Image.Palette.ADAPTIVE, colors=128) for frame in frames]
    palette[0].save(
        out_path,
        save_all=True,
        append_images=palette[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    return out_path


def _build_still(exercise: dict[str, Any]) -> Path:
    """Render a single side-by-side start/end image (fallback for `format=photo`)."""
    from PIL import Image

    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(exercise.get("id")))
    out_path = _gif_dir() / f"{safe_id}.jpg"
    if out_path.is_file() and out_path.stat().st_size > 0:
        return out_path

    images = exercise.get("images") or []
    if not images:
        raise ValueError(f"{exercise.get('id')} has no images")

    frames = [Image.open(_download_frame(rel)).convert("RGB") for rel in images[:2]]
    if len(frames) == 1:
        frames.append(frames[0].copy())
    width = min(GIF_MAX_WIDTH, frames[0].width)
    height = round(frames[0].height * width / frames[0].width)
    frames = [img.resize((width, height), Image.Resampling.LANCZOS) for img in frames]

    canvas = Image.new("RGB", (width * 2 + 8, height), (255, 255, 255))
    canvas.paste(frames[0], (0, 0))
    canvas.paste(frames[1], (width + 8, 0))
    canvas.save(out_path, "JPEG", quality=88)
    return out_path


def _handle_demo(args: dict, **_kwargs) -> str:
    try:
        ids = _as_list(args.get("exerciseIds") or args.get("exerciseId"))
        if not ids:
            return tool_error("exerciseIds is required — get one from exercise_find first")
        ids = ids[:5]
        want_gif = str(args.get("format") or "gif").lower() != "photo"

        demos: list[dict[str, Any]] = []
        missing: list[str] = []
        for exercise_id in ids:
            exercise = _by_id(exercise_id)
            if exercise is None:
                missing.append(exercise_id)
                continue
            try:
                path = _build_gif(exercise) if want_gif else _build_still(exercise)
            except Exception as exc:
                logger.warning("demo render failed for %s: %s", exercise_id, exc)
                missing.append(exercise_id)
                continue
            entry = _summarize(exercise, full=True)
            entry["mediaPath"] = str(path)
            # Pre-formatted so the model copies one line verbatim. Emitting the
            # bare path instead has happened in production: the paths were then
            # delivered to the user as chat messages full of /opt/... noise.
            entry["mediaTag"] = f"MEDIA:{path}"
            # The model cannot tell an illustration from a stock photo by looking
            # at a path, and the photo fallback never fails — so without this it
            # never learns a proper drawing is missing, and never asks for one.
            entry["illustrated"] = is_illustrated(exercise)
            demos.append(entry)

        if not demos:
            return tool_error(
                f"could not render a demo for: {', '.join(missing)}",
                unknownIds=missing,
            )

        note = (
            "Copy each demo's mediaTag onto its own line in your reply, exactly as "
            "given. Never write a bare file path — a path without the MEDIA: prefix "
            "is delivered to the user as chat text instead of a picture. Never reuse "
            "a path from earlier in the conversation; those go stale and silently "
            "fail to deliver, so always use the mediaTag from this result. Summarize "
            "the instructions in the user's language alongside it — do not paste the "
            "raw English steps verbatim."
        )
        undrawn = [d["exerciseId"] for d in demos if not d["illustrated"]]
        if undrawn:
            note += (
                " NOTE — these are stock photographs, not the illustrated library: "
                f"{', '.join(undrawn)}. Send them, and offer once to draw a proper "
                "illustration with exercise_generate_demo if the user wants a clearer "
                "one. Do not draw without being asked; it takes a minute or two each."
            )

        return tool_result(
            {
                "demos": demos,
                "unknownIds": missing,
                "illustratedCount": sum(1 for d in demos if d["illustrated"]),
                "note": note,
            }
        )
    except Exception as exc:
        logger.warning("exercise_demo failed: %s", exc)
        return tool_error(f"exercise demo failed: {exc}")


# --------------------------------------------------------------------------
# workout building
# --------------------------------------------------------------------------

def _prescription(exercise: dict[str, Any], level: str) -> dict[str, Any]:
    """Sets/reps guidance derived from the exercise category and user level."""
    category = str(exercise.get("category") or "strength").lower()
    if category == "stretching":
        return {"sets": 2, "prescription": "hold 30 seconds per side"}
    if category == "cardio":
        return {"sets": 1, "prescription": "8-12 minutes at a steady effort"}
    if category == "plyometrics":
        return {"sets": 3, "prescription": "8 explosive reps"}
    if category in {"powerlifting", "olympic weightlifting", "strongman"}:
        return {"sets": 5, "prescription": "3-5 reps, heavy, long rest"}

    if level == "expert":
        return {"sets": 4, "prescription": "6-10 reps"}
    if level == "intermediate":
        return {"sets": 4, "prescription": "8-12 reps"}
    return {"sets": 3, "prescription": "10-12 reps"}


def _resolve_focus(focus: Any) -> list[str]:
    """Turn a goal keyword or muscle list into concrete target muscles."""
    requested = _as_list(focus)
    if not requested:
        return list(FOCUS_GROUPS["full body"])

    targets: list[str] = []
    for item in requested:
        key = _translate_hebrew(str(item).strip().lower()).strip()
        if key in FOCUS_GROUPS:
            targets.extend(FOCUS_GROUPS[key])
        elif key in MUSCLES:
            targets.append(key)
        else:
            # Unknown word — try the closest known muscle or group.
            pool = list(MUSCLES) + list(FOCUS_GROUPS)
            close = difflib.get_close_matches(key, pool, n=1, cutoff=0.75)
            if close:
                match = close[0]
                targets.extend(FOCUS_GROUPS.get(match, (match,)))

    if not targets:
        return list(FOCUS_GROUPS["full body"])
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(targets))


def _handle_build_workout(args: dict, **_kwargs) -> str:
    try:
        level = str(args.get("level") or "beginner").lower()
        if level not in _LEVEL_ORDER:
            level = "beginner"
        count = max(3, min(int(args.get("exerciseCount") or 6), 12))
        targets = _resolve_focus(args.get("focus"))
        equipment = [str(e).lower() for e in _as_list(args.get("equipment"))]
        avoid = {str(a).strip().lower() for a in _as_list(args.get("avoid"))}

        dataset = _load_dataset()
        level_cap = _LEVEL_ORDER[level]

        def eligible(exercise: dict[str, Any], muscle: str) -> bool:
            if str(exercise.get("id", "")).lower() in avoid:
                return False
            if str(exercise.get("name", "")).lower() in avoid:
                return False
            if muscle not in (exercise.get("primaryMuscles") or []):
                return False
            if _LEVEL_ORDER.get(str(exercise.get("level") or "beginner"), 0) > level_cap:
                return False
            if equipment and str(exercise.get("equipment") or "").lower() not in equipment:
                return False
            if str(exercise.get("category") or "") in {"strongman", "olympic weightlifting"}:
                return False
            return True

        # Same plan within a day, different plan the next — stable enough for the
        # user to re-ask without churn, varied enough not to be the same six
        # exercises forever. Callers can pin it explicitly with `seed`.
        seed = args.get("seed")
        if seed is None:
            seed = time.strftime("%Y-%m-%d")
        rng = random.Random(f"{seed}|{level}|{','.join(targets)}|{','.join(equipment)}")

        picked: list[dict[str, Any]] = []
        used_ids: set[str] = set()
        # Round-robin across target muscles so the session stays balanced.
        for round_index in range(count):
            muscle = targets[round_index % len(targets)]
            pool = [
                ex for ex in dataset
                if eligible(ex, muscle) and str(ex.get("id")) not in used_ids
            ]
            if not pool:
                continue

            # A workout should be resistance work. Stretches and plyometrics are
            # in the same dataset and will otherwise land in the middle of a
            # strength session ("hold 30 seconds" as exercise 3 of 6). Keep them
            # only when nothing else fits the muscle and equipment.
            strength = [
                ex for ex in pool
                if str(ex.get("category") or "") in {"strength", "powerlifting"}
            ]
            if strength:
                pool = strength

            # Compounds first — they carry the session; isolation fills the tail.
            prefer_compound = round_index < max(2, count // 2)
            compounds = [ex for ex in pool if ex.get("mechanic") == "compound"]
            if prefer_compound and compounds:
                pool = compounds
            choice = rng.choice(pool)
            used_ids.add(str(choice.get("id")))
            entry = _summarize(choice, full=True)
            entry.update(_prescription(choice, level))
            entry["targets"] = muscle
            picked.append(entry)

        if not picked:
            return tool_result(
                {
                    "workout": [],
                    "note": "No exercises fit that combination of equipment and level. "
                            "Ask the user what equipment they actually have.",
                }
            )

        unmet = [m for m in targets if m not in {e["targets"] for e in picked}]
        return tool_result(
            {
                "focus": targets,
                "level": level,
                "equipment": equipment or ["any"],
                "workout": picked,
                "musclesNotCovered": unmet,
                "note": (
                    "Present this as a numbered plan in the user's language with sets and reps. "
                    "Call exercise_demo with the exerciseIds if they want to see the movements. "
                    "Log it with health_log_event as status=planned; only mark it completed "
                    "after the user reports finishing."
                ),
            }
        )
    except Exception as exc:
        logger.warning("exercise_build_workout failed: %s", exc)
        return tool_error(f"workout build failed: {exc}")


# --------------------------------------------------------------------------
# schemas + registration
# --------------------------------------------------------------------------

FIND_SCHEMA = {
    "name": "exercise_find",
    "description": (
        "Look up exercises in a free 873-exercise library by name, by muscle, by "
        "equipment, or by a plain description when the user does not know the name "
        "(e.g. 'the one where you hang from a bar and pull yourself up'). Translate "
        "the user's description to English before calling. Returns exercise IDs to "
        "pass to exercise_demo."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Exercise name or a plain-English description of the movement.",
            },
            "muscle": {
                "type": "string",
                "enum": list(MUSCLES),
                "description": "Restrict to exercises working this muscle.",
            },
            "equipment": {
                "type": "array",
                "items": {"type": "string", "enum": list(EQUIPMENT)},
                "description": "Restrict to these equipment types.",
            },
            "category": {"type": "string", "enum": list(CATEGORIES)},
            "level": {
                "type": "string",
                "enum": list(LEVELS),
                "description": "Maximum difficulty to return.",
            },
            "limit": {"type": "integer", "description": "Max results, 1-15. Default 5."},
        },
        "additionalProperties": False,
    },
}

DEMO_SCHEMA = {
    "name": "exercise_demo",
    "description": (
        "Render an animated looping GIF showing how an exercise is performed and "
        "return its local path plus full instructions. Deliver it to the user by "
        "writing MEDIA:<mediaPath> in your reply. Use after exercise_find, or "
        "directly with IDs from exercise_build_workout."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "exerciseIds": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exercise IDs from exercise_find. Max 5 per call.",
            },
            "format": {
                "type": "string",
                "enum": ["gif", "photo"],
                "description": "gif (default) is an animated loop; photo is a static "
                               "side-by-side of the start and end positions.",
            },
        },
        "required": ["exerciseIds"],
        "additionalProperties": False,
    },
}

BUILD_WORKOUT_SCHEMA = {
    "name": "exercise_build_workout",
    "description": (
        "Build a balanced workout set from the free exercise library, with sets and "
        "reps chosen for the user's level and available equipment. Returns exercise "
        "IDs that can be shown with exercise_demo and logged with health_log_event."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "focus": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Goal keywords (full body, upper body, lower body, push, pull, "
                    "core, arms, back, legs) or specific muscle names. "
                    "Defaults to a full-body session."
                ),
            },
            "equipment": {
                "type": "array",
                "items": {"type": "string", "enum": list(EQUIPMENT)},
                "description": "What the user actually has. Omit to allow anything. "
                               "Use ['body only'] for a no-equipment session.",
            },
            "level": {"type": "string", "enum": list(LEVELS), "description": "Default beginner."},
            "exerciseCount": {"type": "integer", "description": "3-12 exercises. Default 6."},
            "avoid": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exercise IDs or names to exclude (injuries, dislikes).",
            },
            "seed": {
                "type": "string",
                "description": "Optional. Pin the selection so the same plan is "
                               "regenerated; otherwise it varies by day.",
            },
        },
        "additionalProperties": False,
    },
}


for _name, _schema, _handler in (
    ("exercise_find", FIND_SCHEMA, _handle_find),
    ("exercise_demo", DEMO_SCHEMA, _handle_demo),
    ("exercise_build_workout", BUILD_WORKOUT_SCHEMA, _handle_build_workout),
):
    registry.register(
        name=_name,
        toolset="exercise",
        schema=_schema,
        handler=_handler,
        emoji="🏋️",
    )
