"""Turn a generated multi-panel exercise strip into a centred looping demo GIF.

The image model draws one strip containing N pose panels of a single exercise;
this slices it and assembles the animation. Everything here is deterministic —
no model, no network — so a bad result is a prompt problem, not a pipeline one.

Pillow only, deliberately: the runtime image ships Pillow but not numpy, and the
project pins dependencies exactly, so adding one for a single tool is not worth
it. Column profiles are extracted with ``resize`` (a C-level reduction) and the
one genuinely iterative step, the alignment search, runs over a few hundred
integers.

Each step exists because of an observed failure:

* **Shared background.** Panels differ slightly in tone and carry drawn rules at
  their edges, which flickers between frames. The figure is matted out and
  composited onto one canvas.
* **Hole-filled matte.** A plain "differs from background" mask punches holes in
  white shoes and highlights when the art is on white. The background is instead
  flood-filled inward from the border, so interior light areas stay part of the
  figure.
* **Solid-ink threshold for anchoring.** The soft ground shadow reads as ink at a
  low threshold and dragged the anchor 50px off. Anchoring uses a high threshold
  the shadow cannot reach.
* **Correlation, not midpoints.** Midpoint anchors drift because held equipment
  swings with the pose. Matching the anchor band against the first frame by 1-D
  cross-correlation cut drift from ~50px to ~5px.
* **One shared crop.** Each pose fills a different amount of its panel, so
  per-frame cropping makes the framing jump.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter

# Summed per-channel distance from the background colour. SOFT finds the figure
# at all; SOLID is high enough that the soft ground shadow never reaches it.
SOFT_INK = 55
SOLID_INK = 150

PANEL_INSET = 14      # px trimmed per panel to drop the drawn rules
CROP_PAD = 26         # px of breathing room around the union figure box
ANCHOR_BAND = 0.13    # fraction of panel height used as the "feet" anchor band
GIF_COLORS = 110
DEFAULT_WIDTH = 460
DEFAULT_HOLD_END = 1100
DEFAULT_HOLD_MID = 520

# Drift above this means the figure visibly slides during playback.
DRIFT_WARN_PX = 20.0


def _median(values: Sequence[int]) -> int:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _background_colour(img: Image.Image) -> Tuple[int, int, int]:
    """Median colour of the panel border — the paper the figure sits on."""
    w, h = img.size
    edge = 6
    strips = [
        img.crop((0, 0, w, edge)), img.crop((0, h - edge, w, h)),
        img.crop((0, 0, edge, h)), img.crop((w - edge, 0, w, h)),
    ]
    channels: List[List[int]] = [[], [], []]
    for s in strips:
        raw = s.tobytes()  # RGB, 3 bytes per pixel
        for c in range(3):
            channels[c].extend(raw[c::3])
    return (_median(channels[0]), _median(channels[1]), _median(channels[2]))


def _distance_map(img: Image.Image, bg: Tuple[int, int, int]) -> Image.Image:
    """Per-pixel summed |channel - background|, as an 8-bit image.

    Channel addition saturates at 255, which is harmless: both thresholds are
    well below that, so the thresholded result is unaffected.
    """
    diff = ImageChops.difference(img, Image.new("RGB", img.size, bg))
    r, g, b = diff.split()
    return ImageChops.add(ImageChops.add(r, g), b)


def _threshold(dist: Image.Image, level: int) -> Image.Image:
    """Binary mask (mode L, 0/255) of pixels further than ``level`` from bg."""
    return dist.point(lambda v: 255 if v > level else 0)


def _figure_matte(panel: Image.Image, bg: Tuple[int, int, int]) -> Image.Image:
    """Feathered alpha for the drawn figure, with interior holes filled."""
    dist = _distance_map(panel, bg)
    near_bg = dist.point(lambda v: 255 if v <= SOFT_INK else 0)

    w, h = near_bg.size
    for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if near_bg.getpixel(seed) == 255:
            ImageDraw.floodfill(near_bg, seed, 128)

    # Anything the flood could not reach from the border is interior, so it
    # stays opaque even where it is as pale as the paper. The blur feathers the
    # silhouette edge so the composite has no hard cut-out fringe.
    alpha = near_bg.point(lambda v: 0 if v == 128 else 255)
    return alpha.filter(ImageFilter.GaussianBlur(0.8))


def _column_profile(mask: Image.Image) -> List[int]:
    """1 per column that contains any ink. Uses a C-level box reduction.

    ``tobytes`` rather than ``getdata``: the mask is mode L, so the buffer is one
    byte per pixel, and ``getdata`` is deprecated from Pillow 14.
    """
    w, _ = mask.size
    row = mask.resize((w, 1), Image.Resampling.BOX)
    return [1 if v > 0 else 0 for v in row.tobytes()]


def _row_profile(mask: Image.Image) -> List[int]:
    _, h = mask.size
    col = mask.resize((1, h), Image.Resampling.BOX)
    return [1 if v > 0 else 0 for v in col.tobytes()]


def _anchor_profile(mask: Image.Image, mode: str) -> List[int]:
    """Column profile used to line consecutive poses up horizontally.

    ``feet`` uses only the bottom band — correct when the athlete is standing,
    because the feet genuinely do not move. ``full`` uses the whole silhouette,
    for lying, seated or hanging exercises with no ground contact.
    """
    if mode == "full":
        return _column_profile(mask)
    w, h = mask.size
    rows = _row_profile(mask)
    filled = [i for i, v in enumerate(rows) if v]
    if not filled:
        return _column_profile(mask)
    bottom = filled[-1]
    top = max(0, bottom - round(h * ANCHOR_BAND))
    return _column_profile(mask.crop((0, top, w, bottom + 1)))


def _ground_row(mask: Image.Image) -> int:
    rows = _row_profile(mask)
    filled = [i for i, v in enumerate(rows) if v]
    return filled[-1] if filled else mask.size[1] - 1


def _best_shift(profile: Sequence[int], reference: Sequence[int], limit: int) -> int:
    """Horizontal shift that best overlaps this profile with the reference."""
    n = len(profile)
    ref_on = [i for i, v in enumerate(reference) if v]
    if not ref_on:
        return 0
    best_score, best_dx = -1, 0
    for dx in range(-limit, limit + 1):
        score = 0
        for i in ref_on:
            j = i - dx
            if 0 <= j < n and profile[j]:
                score += 1
        if score > best_score:
            best_score, best_dx = score, dx
    return best_dx


def _profile_centre(profile: Sequence[int]) -> float:
    on = [i for i, v in enumerate(profile) if v]
    return (on[0] + on[-1]) / 2 if on else 0.0


def build_frames(
    strip_path: str | Path, panels: int = 3, anchor: str = "feet",
    width: int = DEFAULT_WIDTH,
) -> Tuple[List[Image.Image], float]:
    """Slice a strip into aligned, background-matched key frames.

    Returns the frames plus the residual horizontal drift in pixels.
    """
    strip = Image.open(strip_path).convert("RGB")
    sw, sh = strip.size
    pw = sw // panels
    cuts = [
        strip.crop((i * pw + PANEL_INSET, PANEL_INSET,
                    (i + 1) * pw - PANEL_INSET, sh - PANEL_INSET))
        for i in range(panels)
    ]
    panel_w, panel_h = cuts[0].size
    canon = _background_colour(cuts[0])

    placed = []
    for panel in cuts:
        canvas = Image.new("RGB", (panel_w, panel_h), canon)
        canvas.paste(panel, (0, 0), _figure_matte(panel, _background_colour(panel)))
        placed.append(canvas)

    dists = [_distance_map(p, canon) for p in placed]
    solid = [_threshold(d, SOLID_INK) for d in dists]
    if not all(m.getbbox() for m in solid):
        raise ValueError("a panel contains no figure — check the strip and panel count")

    ground = [_ground_row(m) for m in solid]
    target_y = int(panel_h * 0.95)

    reference = _anchor_profile(solid[0], anchor)
    shifts = [_best_shift(_anchor_profile(m, anchor), reference, panel_w // 2)
              for m in solid]

    aligned = []
    for canvas, dx, gy in zip(placed, shifts, ground):
        out = Image.new("RGB", (panel_w, panel_h), canon)
        out.paste(canvas, (dx, target_y - gy))
        aligned.append(out)

    centres = [
        _profile_centre(_anchor_profile(_threshold(_distance_map(a, canon), SOLID_INK), anchor))
        for a in aligned
    ]
    drift = float(max(centres) - min(centres))

    boxes = []
    for a in aligned:
        box = _threshold(_distance_map(a, canon), SOFT_INK).getbbox()
        if box:
            boxes.append(box)
    x0 = max(0, min(b[0] for b in boxes) - CROP_PAD)
    y0 = max(0, min(b[1] for b in boxes) - CROP_PAD)
    x1 = min(panel_w, max(b[2] for b in boxes) + CROP_PAD)
    y1 = min(panel_h, max(b[3] for b in boxes) + CROP_PAD)

    keys = [a.crop((x0, y0, x1, y1)) for a in aligned]
    cw, ch = keys[0].size
    height = round(ch * width / cw)
    return [k.resize((width, height), Image.Resampling.LANCZOS) for k in keys], drift


def build_demo_gif(
    strip_path: str | Path, out_path: str | Path, panels: int = 3,
    anchor: str = "feet", width: int = DEFAULT_WIDTH,
    hold_end: int = DEFAULT_HOLD_END, hold_mid: int = DEFAULT_HOLD_MID,
    contact_sheet: str | Path | None = None,
) -> dict:
    """Build a looping demo GIF from a strip. Returns a small result dict."""
    keys, drift = build_frames(strip_path, panels, anchor, width)

    # Out and back without repeating the end poses: 0,1,..,N-1,..,1
    order = list(range(len(keys))) + list(range(len(keys) - 2, 0, -1))
    frames = [keys[i] for i in order]
    durations = [hold_end if i in (0, len(keys) - 1) else hold_mid for i in order]

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pal = [f.convert("P", palette=Image.Palette.ADAPTIVE, colors=GIF_COLORS)
           for f in frames]
    pal[0].save(out_path, save_all=True, append_images=pal[1:], duration=durations,
                loop=0, optimize=True, disposal=1)

    if contact_sheet:
        w, h = keys[0].size
        sheet = Image.new("RGB", (w * len(keys), h), (255, 255, 255))
        for i, k in enumerate(keys):
            sheet.paste(k, (i * w, 0))
        d = ImageDraw.Draw(sheet)
        for i in range(len(keys)):
            d.line([(i * w + w // 2, 0), (i * w + w // 2, h)], fill=(0, 120, 220), width=2)
            d.rectangle([i * w, 0, (i + 1) * w - 1, h - 1], outline=(200, 0, 0), width=2)
        sheet.save(contact_sheet)

    return {
        "path": str(out_path),
        "frames": len(frames),
        "durationMs": sum(durations),
        "bytes": out_path.stat().st_size,
        "driftPx": round(drift, 1),
        "drifts": drift > DRIFT_WARN_PX,
        "anchor": anchor,
    }
