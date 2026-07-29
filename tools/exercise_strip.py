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

# Three poses held for over a second each is a slideshow, not an animation: the
# first pose occupied a third of the loop, and on a phone — where Telegram
# transcodes the GIF and autoplays it — that reads as a still image that keeps
# resetting. Short, even steps read as movement.
#
# Every frame gets DEFAULT_HOLD_MID. A GIF with mixed delays has no single frame
# rate, so re-encoding it to constant-rate video — which is what a phone does —
# lands frames unevenly and the movement stutters, while a desktop GIF player
# honours each delay and looks fine. DEFAULT_HOLD_END is kept for callers that
# still pass it and is no longer used.
DEFAULT_HOLD_END = 150
DEFAULT_HOLD_MID = 150

# A 460×1301 demo is a sliver on a phone, and Telegram rejects extreme aspect
# ratios as photos outright (Photo_invalid_dimensions). Taller than this and the
# frame is scaled down to fit rather than shipped as a ribbon.
MAX_ASPECT = 1.7

LABEL_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
)
LABEL_BAND = 34       # px of caption strip added under the figure
LABEL_PAD = 8

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
    strip_path: str | Path, panels: int = 3, anchor: str = "auto",
    width: int = DEFAULT_WIDTH,
) -> Tuple[List[Image.Image], float]:
    """Slice a strip into aligned, background-matched key frames.

    Returns the frames plus the residual horizontal drift in pixels.

    ``anchor="auto"`` builds both ways and keeps whichever leaves less drift.
    Choosing by hand went badly: "feet" is the obvious pick for a standing
    exercise, but held equipment and a stance that widens or narrows through the
    movement drag the bottom-band correlation. Measured across the drawn library,
    "full" beat the hand-picked anchor on four of the five worst demos — a sumo
    high pull went from 84px of slide to 31, a two-arm row from 36.5 to 8.5, a
    pistol squat from 88 to 10. The measurement is already computed, so letting
    it decide costs one extra pass over a handful of small images and no
    generations.
    """
    if anchor == "auto":
        best_frames, best_drift = None, None
        for candidate in ("feet", "full"):
            try:
                frames, drift = build_frames(strip_path, panels, candidate, width)
            except ValueError:
                continue
            if best_drift is None or drift < best_drift:
                best_frames, best_drift = frames, drift
        if best_frames is None:
            raise ValueError("a panel contains no figure — check the strip and panel count")
        return best_frames, best_drift
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
    scaled = [k.resize((width, height), Image.Resampling.LANCZOS) for k in keys]
    if height > width * MAX_ASPECT:
        # Widen the canvas rather than shrinking the athlete. Scaling both sides
        # would keep the same ribbon shape; padding brings the ratio down while
        # the figure stays exactly as large as it was drawn.
        padded_w = round(height / MAX_ASPECT)
        offset = (padded_w - width) // 2
        widened = []
        for frame in scaled:
            canvas = Image.new("RGB", (padded_w, height), (255, 255, 255))
            canvas.paste(frame, (offset, 0))
            widened.append(canvas)
        scaled = widened
    return scaled, drift


def _label_font(size: int):
    """Bold face for the caption band, or Pillow's built-in if none is installed."""
    for path in LABEL_FONTS:
        if Path(path).is_file():
            try:
                from PIL import ImageFont

                return ImageFont.truetype(path, size)
            except Exception:
                continue
    from PIL import ImageFont

    return ImageFont.load_default()


def _with_label(frame: Image.Image, label: str) -> Image.Image:
    """Return the frame with the exercise name printed under it.

    The name belongs *in* the picture. Captions are written by the model in the
    surrounding chat text, so nothing stopped it from presenting a Figure-8 clip
    as a Halo — which is exactly what happened in production. A label burned into
    the frame travels with the file and cannot be contradicted.
    """
    if not label:
        return frame
    w, h = frame.size
    out = Image.new("RGB", (w, h + LABEL_BAND), (255, 255, 255))
    out.paste(frame, (0, 0))
    draw = ImageDraw.Draw(out)
    text = label.replace("_", " ").strip()

    size = LABEL_BAND - 2 * LABEL_PAD + 4
    while size > 8:
        font = _label_font(size)
        if draw.textlength(text, font=font) <= w - 2 * LABEL_PAD:
            break
        size -= 1
    else:
        font = _label_font(9)

    draw.text((w // 2, h + LABEL_BAND // 2), text, font=font,
              fill=(40, 40, 40), anchor="mm")
    return out


def _evened(frame: Image.Image) -> Image.Image:
    """Trim to even width and height.

    Phones do not play the GIF — Telegram re-encodes it to H.264 video, which
    stores colour at half resolution and therefore requires both dimensions to
    be even. An odd side forces the encoder to pad or rescale, and the result
    shimmers on the phone while the original plays perfectly on a desktop, which
    is exactly the "fine on the computer, glitchy on mobile" report.
    """
    w, h = frame.size
    if w % 2 == 0 and h % 2 == 0:
        return frame
    return frame.crop((0, 0, w - (w % 2), h - (h % 2)))


def normalize_demo_gif(
    gif_path: str | Path, out_path: str | Path | None = None,
    frame_ms: int = DEFAULT_HOLD_MID, label: str = "",
) -> dict:
    """Re-encode an existing demo so a phone can transcode it cleanly.

    The three properties that break the mobile re-encode — odd dimensions, mixed
    frame delays, per-frame palettes — are all in the *encoding*, not in the
    drawing. A demo whose source strip was not kept can therefore still be fixed
    from its own frames, without spending a generation to draw it again.

    This cannot add motion: a clip built from three poses still has three poses.
    Redrawing is the only cure for that.
    """
    src = Image.open(gif_path)
    frames = []
    for i in range(getattr(src, "n_frames", 1)):
        src.seek(i)
        frames.append(_evened(src.convert("RGB")))
    if label:
        frames = [_evened(_with_label(f, label)) for f in frames]

    target = Path(out_path or gif_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    base = frames[0].quantize(colors=GIF_COLORS, method=Image.Quantize.MEDIANCUT)
    pal = [base] + [f.quantize(palette=base, dither=Image.Dither.FLOYDSTEINBERG)
                    for f in frames[1:]]
    pal[0].save(target, save_all=True, append_images=pal[1:],
                duration=[frame_ms] * len(pal), loop=0, optimize=True, disposal=1)
    return {
        "path": str(target),
        "frames": len(pal),
        "size": frames[0].size,
        "bytes": target.stat().st_size,
    }


def build_demo_gif(
    strip_path: str | Path, out_path: str | Path, panels: int = 3,
    anchor: str = "auto", width: int = DEFAULT_WIDTH,
    hold_end: int = DEFAULT_HOLD_END, hold_mid: int = DEFAULT_HOLD_MID,
    contact_sheet: str | Path | None = None,
    label: str = "",
) -> dict:
    """Build a looping demo GIF from a strip. Returns a small result dict."""
    keys, drift = build_frames(strip_path, panels, anchor, width)
    if label:
        keys = [_with_label(k, label) for k in keys]

    keys = [_evened(k) for k in keys]

    # Out and back without repeating the end poses: 0,1,..,N-1,..,1
    order = list(range(len(keys))) + list(range(len(keys) - 2, 0, -1))
    frames = [keys[i] for i in order]
    # One delay for every frame. A pause at the ends was tried two ways and
    # both lose: a longer delay on one frame leaves the clip with no single
    # frame rate, and repeating the end frame does nothing because the GIF
    # encoder merges identical neighbours back into one long frame. The
    # out-and-back order already gives the rep its rhythm.
    durations = [hold_mid] * len(frames)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # One palette for the whole animation. Per-frame adaptive palettes shift the
    # colour table between frames, which survives a GIF player but shows as the
    # picture flickering once a phone re-encodes the clip to video.
    base = frames[0].quantize(colors=GIF_COLORS, method=Image.Quantize.MEDIANCUT)
    pal = [base] + [f.quantize(palette=base, dither=Image.Dither.FLOYDSTEINBERG)
                    for f in frames[1:]]
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
