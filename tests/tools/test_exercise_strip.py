"""The finished demo has to read as movement and say what it is.

Two production failures drive these tests. First, demos played as a still image
that kept resetting: the GIF held three distinct pictures, the first for over a
second, and Telegram's mobile client transcodes and autoplays that as what looks
like a frozen frame. Second, the bot presented a Figure-8 clip as a Halo, which
nothing in the file contradicted because the caption is model-authored chat text.

Both are fixed in the asset itself rather than in a prompt: more frames, even
timing, and the exercise name printed inside the picture.
"""

from __future__ import annotations

from PIL import Image

from tools import exercise_strip as strip


def _strip_image(path, panels=5, w=1500, h=300):
    """A synthetic pose strip: one blob per panel, each at a different height."""
    im = Image.new("RGB", (w, h), (255, 255, 255))
    pw = w // panels
    step = 120 // max(1, panels - 1)
    for i in range(panels):
        top = 60 + i * step
        for x in range(i * pw + pw // 4, i * pw + pw // 4 + max(20, pw // 3)):
            for y in range(top, 250):
                im.putpixel((x, y), (20, 20, 20))
    im.save(path)
    return path


def _frames(path):
    im = Image.open(path)
    out = []
    for i in range(getattr(im, "n_frames", 1)):
        im.seek(i)
        out.append(im.info.get("duration"))
    return out


def test_five_panels_produce_a_loop_long_enough_to_read_as_motion(tmp_path):
    src = _strip_image(tmp_path / "s.png", panels=5)
    result = strip.build_demo_gif(src, tmp_path / "out.gif", panels=5)

    # Out and back without repeating the ends: 5 poses → 8 frames.
    assert result["frames"] == 8


def test_both_dimensions_are_even(tmp_path):
    """Phones re-encode the clip to H.264, which needs even sides. An odd side
    makes the encoder pad or rescale, and the result shimmers on mobile while
    playing perfectly on a desktop GIF player."""
    for w, h, panels in ((601, 331, 3), (900, 300, 5), (777, 999, 3)):
        src = _strip_image(tmp_path / f"s{w}x{h}.png", panels=panels, w=w, h=h)
        out = tmp_path / f"o{w}x{h}.gif"
        strip.build_demo_gif(src, out, panels=panels, label="Test Move")
        with Image.open(out) as im:
            assert im.size[0] % 2 == 0 and im.size[1] % 2 == 0, im.size


def test_every_frame_has_the_same_delay(tmp_path):
    """Mixed delays give the clip no single frame rate, so re-encoding it to
    constant-rate video lands frames unevenly and the movement stutters."""
    src = _strip_image(tmp_path / "s.png", panels=5)
    out = tmp_path / "out.gif"
    strip.build_demo_gif(src, out, panels=5)

    durations = _frames(out)
    assert len(set(durations)) == 1, durations


def test_one_palette_for_the_whole_animation(tmp_path):
    """A colour table that changes between frames survives a GIF player but
    shows as the picture flickering once a phone re-encodes it."""
    src = _strip_image(tmp_path / "s.png", panels=5)
    out = tmp_path / "out.gif"
    strip.build_demo_gif(src, out, panels=5)

    palettes = set()
    with Image.open(out) as im:
        for i in range(im.n_frames):
            im.seek(i)
            pal = im.getpalette()
            if pal:
                palettes.add(bytes(pal))
    assert len(palettes) == 1, "frames must not carry differing colour tables"


def test_no_frame_is_held_long_enough_to_look_like_a_still(tmp_path):
    """The old timing held the first pose 1100ms of a 3240ms loop — a third of
    the animation on one picture, which is what users saw as 'it doesn't play'."""
    src = _strip_image(tmp_path / "s.png", panels=5)
    out = tmp_path / "out.gif"
    strip.build_demo_gif(src, out, panels=5)

    durations = _frames(out)
    assert max(durations) <= 200, durations


def test_a_tall_demo_is_scaled_down_rather_than_shipped_as_a_ribbon(tmp_path):
    """460x1301 is a sliver on a phone, and Telegram rejects extreme aspect
    ratios as photos outright."""
    src = _strip_image(tmp_path / "tall.png", panels=3, w=600, h=1400)
    out = tmp_path / "tall.gif"
    strip.build_demo_gif(src, out, panels=3)

    with Image.open(out) as im:
        w, h = im.size
    assert h / w <= strip.MAX_ASPECT + 0.01, f"{w}x{h} is too tall to watch"


def test_the_exercise_name_is_printed_inside_the_picture(tmp_path):
    """A label in the frame travels with the file, so a mislabelled demo
    contradicts itself instead of passing silently."""
    src = _strip_image(tmp_path / "s.png", panels=3)
    plain = tmp_path / "plain.gif"
    named = tmp_path / "named.gif"

    strip.build_demo_gif(src, plain, panels=3)
    strip.build_demo_gif(src, named, panels=3, label="Kettlebell Halo")

    with Image.open(plain) as a, Image.open(named) as b:
        assert b.size[1] > a.size[1], "the labelled frame needs its caption band"
        band = b.convert("RGB").crop((0, a.size[1], b.size[0], b.size[1]))

    # Something was drawn in the band — it is not just blank paper.
    assert band.getbbox() is not None
    assert min(band.convert("L").getextrema()) < 200, "the caption band looks empty"


def test_auto_anchor_never_does_worse_than_either_fixed_choice(tmp_path):
    """Measured across the drawn library, the hand-picked anchor lost four times
    out of five: a pistol squat slid 88px on 'feet' and 10px on 'full'. The
    residual drift is already computed, so it picks."""
    src = _strip_image(tmp_path / "s.png", panels=5)

    _, feet = strip.build_frames(src, 5, "feet")
    _, full = strip.build_frames(src, 5, "full")
    _, auto = strip.build_frames(src, 5, "auto")

    assert auto <= min(feet, full) + 1e-6


def test_auto_is_the_default(tmp_path):
    src = _strip_image(tmp_path / "s.png", panels=3)
    _, chosen = strip.build_frames(src, 3)
    _, auto = strip.build_frames(src, 3, "auto")
    assert chosen == auto


def test_an_unlabelled_demo_keeps_the_frame_size(tmp_path):
    """Labelling is opt-in — an unlabelled demo gains no caption band, and only
    ever loses the odd row or column that would break the mobile re-encode."""
    src = _strip_image(tmp_path / "s.png", panels=3)
    out = tmp_path / "out.gif"
    strip.build_demo_gif(src, out, panels=3, label="")

    frames, _ = strip.build_frames(src, 3)
    want = tuple(v - (v % 2) for v in frames[0].size)
    with Image.open(out) as im:
        assert im.size == want
