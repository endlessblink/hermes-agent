"""Turn a generated multi-panel exercise strip into a centred looping demo GIF.

The image model draws one strip containing N pose panels of a single exercise;
this slices it and assembles the animation. Everything here is deterministic —
no model, no network — so a bad result is a prompt problem, not a pipeline one.

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
from typing import List, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

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


def _background_colour(arr: np.ndarray) -> np.ndarray:
    border = np.concatenate([
        arr[:6].reshape(-1, 3), arr[-6:].reshape(-1, 3),
        arr[:, :6].reshape(-1, 3), arr[:, -6:].reshape(-1, 3),
    ])
    return np.median(border, axis=0)


def _figure_matte(panel: Image.Image) -> Image.Image:
    """Feathered alpha for the drawn figure, with interior holes filled."""
    arr = np.asarray(panel).astype(float)
    bg = _background_colour(arr)
    dist = np.abs(arr - bg).sum(axis=2)

    near_bg = (dist <= SOFT_INK).astype(np.uint8) * 255
    mask_img = Image.fromarray(near_bg, mode="L")
    w, h = mask_img.size
    for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if mask_img.getpixel(seed) == 255:
            ImageDraw.floodfill(mask_img, seed, 128)

    # Anything the flood could not reach from the border is interior, so it stays
    # fully opaque even where it is as pale as the paper. The blur feathers the
    # silhouette edge so the composite has no hard cut-out fringe.
    outside = np.asarray(mask_img) == 128
    alpha = np.where(outside, 0.0, 1.0)
    return Image.fromarray((alpha * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(0.8)
    )


def _solid_mask(arr: np.ndarray, bg: np.ndarray) -> np.ndarray:
    return np.abs(arr - bg).sum(axis=2) > SOLID_INK


def _anchor_profile(mask: np.ndarray, mode: str) -> np.ndarray:
    """Column profile used to line consecutive poses up horizontally.

    ``feet`` uses only the bottom band — correct when the athlete is standing,
    because the feet genuinely do not move. ``full`` uses the whole silhouette,
    for lying, seated or hanging exercises with no ground contact.
    """
    if mode == "full":
        return mask.any(axis=0).astype(float)
    rows = np.where(mask.any(axis=1))[0]
    if not len(rows):
        return mask.any(axis=0).astype(float)
    bottom = int(rows.max())
    top = max(0, bottom - round(mask.shape[0] * ANCHOR_BAND))
    band = np.zeros_like(mask)
    band[top:bottom + 1, :] = mask[top:bottom + 1, :]
    return band.any(axis=0).astype(float)


def _best_shift(profile: np.ndarray, reference: np.ndarray, limit: int) -> int:
    best_score, best_dx = -1.0, 0
    for dx in range(-limit, limit + 1):
        shifted = np.roll(profile, dx)
        if dx > 0:
            shifted[:dx] = 0
        elif dx < 0:
            shifted[dx:] = 0
        score = float((shifted * reference).sum())
        if score > best_score:
            best_score, best_dx = score, dx
    return best_dx


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
    canon = _background_colour(np.asarray(cuts[0]).astype(float))

    placed = []
    for panel in cuts:
        canvas = Image.new("RGB", (panel_w, panel_h), tuple(canon.astype(int)))
        canvas.paste(panel, (0, 0), _figure_matte(panel))
        placed.append(canvas)

    arrs = [np.asarray(p).astype(float) for p in placed]
    masks = [_solid_mask(a, canon) for a in arrs]
    if not all(m.any() for m in masks):
        raise ValueError("a panel contains no figure — check the strip and panel count")

    ground = [int(np.where(m.any(axis=1))[0].max()) for m in masks]
    target_y = int(panel_h * 0.95)

    reference = _anchor_profile(masks[0], anchor)
    shifts = [_best_shift(_anchor_profile(m, anchor), reference, panel_w // 2)
              for m in masks]

    aligned = []
    for canvas, dx, gy in zip(placed, shifts, ground):
        out = Image.new("RGB", (panel_w, panel_h), tuple(canon.astype(int)))
        out.paste(canvas, (dx, target_y - gy))
        aligned.append(out)

    centres = []
    for a in aligned:
        prof = _anchor_profile(_solid_mask(np.asarray(a).astype(float), canon), anchor)
        xs = np.where(prof > 0)[0]
        centres.append((xs.min() + xs.max()) / 2 if len(xs) else 0)
    drift = float(max(centres) - min(centres))

    boxes = []
    for a in aligned:
        m = np.abs(np.asarray(a).astype(float) - canon).sum(axis=2) > SOFT_INK
        ys, xs = np.where(m)
        boxes.append((xs.min(), ys.min(), xs.max(), ys.max()))
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
