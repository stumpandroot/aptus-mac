"""Engine tests. Run from the project folder: python -m pytest -q"""
import io
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from aptus_engine import (contact_sheet, export_image, grab_frame, interval_frames, load_presets,
                          probe, sharp_frames)
from aptus_engine.images import focal_crop_box

PRESETS = load_presets()


def photo(w=4000, h=3000, seed=1):
    """Noisy gradient: compresses like a real photo, so size targets are meaningful."""
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 255, w)[None, :, None]
    y = np.linspace(0, 255, h)[:, None, None]
    base = np.concatenate([np.broadcast_to(x, (h, w, 1)), np.broadcast_to(y, (h, w, 1)),
                           np.full((h, w, 1), 128.0)], axis=2)
    noise = rng.normal(0, 40, (h, w, 3))
    return Image.fromarray(np.clip(base + noise, 0, 255).astype(np.uint8), "RGB")


def decode(result):
    return Image.open(io.BytesIO(result.data))


# ---------- presets ----------

def test_presets_load_and_are_valid():
    assert len(PRESETS) == 12
    assert PRESETS["ig-feed"].width == 1080 and PRESETS["ig-feed"].height == 1440
    assert PRESETS["web-hero"].crops is False


# ---------- crop math ----------

def test_crop_box_centered():
    assert focal_crop_box(4000, 3000, 1.0, (0.5, 0.5)) == (500, 0, 3500, 3000)


def test_crop_box_clamps_to_edges():
    assert focal_crop_box(4000, 3000, 1.0, (0.0, 0.5)) == (0, 0, 3000, 3000)
    assert focal_crop_box(4000, 3000, 1.0, (1.0, 0.5)) == (1000, 0, 4000, 3000)


def test_crop_box_follows_focal_point():
    left, _, right, _ = focal_crop_box(4000, 3000, 1.0, (0.4, 0.5))
    assert (left + right) / 2 == 1600


def test_crop_box_tall_target_on_wide_image():
    box = focal_crop_box(1920, 1080, 3 / 4, (0.5, 0.5))
    assert box == (555, 0, 1365, 1080)


# ---------- exports: every preset ----------

@pytest.mark.parametrize("pid", [k for k, p in PRESETS.items() if not p.is_set])
def test_every_preset_produces_correct_file(pid):
    p = PRESETS[pid]
    src = photo()
    r = export_image(src, p)
    out = decode(r)
    fmt = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "avif": "AVIF"}[p.format]
    assert out.format == fmt
    if p.crops:
        assert out.size == (p.width, p.height)
    elif p.width:
        assert out.width == p.width
        assert out.height == round(3000 * p.width / 4000)
    else:
        assert out.size == src.size
    if p.max_bytes:
        assert r.size_target_met is True, (pid, r.bytes, r.warnings)
        assert r.bytes <= p.max_bytes


def test_size_target_search_lowers_quality():
    p = PRESETS["web-content"]
    r = export_image(photo(seed=2), p)
    assert r.bytes <= p.max_bytes
    assert r.quality <= p.quality


def test_impossible_target_is_reported():
    from dataclasses import replace
    p = replace(PRESETS["web-hero"], max_bytes=5_000)
    r = export_image(photo(), p)
    assert r.size_target_met is False
    assert any("Couldn't reach" in w for w in r.warnings)


def test_small_source_not_upscaled_for_width_presets():
    r = export_image(photo(800, 600), PRESETS["web-content"])
    assert decode(r).width == 800
    assert r.warnings


def test_small_source_warns_on_crop_presets():
    r = export_image(photo(800, 600), PRESETS["ig-feed"])
    assert decode(r).size == (1080, 1440)
    assert any("enlarged" in w for w in r.warnings)


def test_alpha_flattens_to_white_for_jpg_and_survives_for_webp():
    img = Image.new("RGBA", (2000, 2000), (255, 0, 0, 0))
    jpg = decode(export_image(img, PRESETS["li-square"]))
    assert jpg.mode == "RGB" and jpg.getpixel((600, 600))[0] > 245
    webp = decode(export_image(img, PRESETS["web-content"]))
    assert webp.mode == "RGBA"


def test_exif_rotation_is_applied():
    src = photo(3000, 2000)
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6   # rotate 90 degrees clockwise on display
    src.save(buf, "JPEG", exif=exif)
    rotated = Image.open(io.BytesIO(buf.getvalue()))
    r = export_image(rotated, PRESETS["portfolio"])
    assert decode(r).size == (2000, 3000)


def test_metadata_is_stripped():
    src = photo(3000, 2000)
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F] = "Canon"
    src.save(buf, "JPEG", exif=exif)
    r = export_image(Image.open(io.BytesIO(buf.getvalue())), PRESETS["yt-thumb"])
    assert not decode(r).getexif()


def test_source_image_is_not_modified():
    src = photo()
    before = src.tobytes()
    export_image(src, PRESETS["ig-feed"], focal=(0.2, 0.8))
    assert src.tobytes() == before


# ---------- video ----------

@pytest.fixture(scope="session")
def video(tmp_path_factory):
    """10 s 1920x1080 test clip, blurred everywhere except 4.9-5.1 s."""
    path = tmp_path_factory.mktemp("v") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=10",
         "-vf", "gblur=sigma=12:enable='not(between(t,4.9,5.1))'", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def test_probe(video):
    info = probe(video)
    assert (info.width, info.height) == (1920, 1080)
    assert abs(info.duration - 10) < 0.1
    assert abs(info.fps - 30) < 0.01


def test_grab_frame_full_resolution(video):
    assert grab_frame(video, 3.0).size == (1920, 1080)
    assert grab_frame(video, 3.0, max_width=640).size == (640, 360)


def test_sharp_frames_finds_the_sharp_moment(video):
    picks = sharp_frames(video, 5.3, window=1.0)
    assert len(picks) >= 1
    assert 4.85 <= picks[0]["t"] <= 5.15, picks


def test_interval_frames(video):
    frames = interval_frames(video, 2.5)
    assert [round(t, 1) for t, _ in frames] == [0.0, 2.5, 5.0, 7.5]
    assert frames[0][1].size == (1920, 1080)


def test_contact_sheet(video):
    sheet = contact_sheet(video, count=12, columns=4, tile_width=480, gap=12)
    assert sheet.size == (4 * 480 + 5 * 12, 3 * 270 + 4 * 12)


def test_frame_to_preset(video):
    frame = grab_frame(video, 5.0)
    r = export_image(frame, PRESETS["ig-feed"], focal=(0.5, 0.5))
    assert decode(r).size == (1080, 1440)


# ---------- hand-placed crops ----------

from aptus_engine.images import box_from_fractions


def test_hand_box_used_as_given_when_shape_matches():
    # 1080x1440 box at the right edge of a 4000x3000 image, as fractions
    assert box_from_fractions(4000, 3000, 3 / 4, (0.6, 0.2, 0.27, 0.48)) == (2400, 600, 3480, 2040)


def test_hand_box_snaps_to_exact_shape_and_stays_inside():
    left, top, right, bottom = box_from_fractions(4000, 3000, 3 / 4, (0.9, 0.9, 0.3, 0.3))
    assert abs((right - left) / (bottom - top) - 0.75) < 0.002
    assert right <= 4000 and bottom <= 3000 and left >= 0 and top >= 0


def test_hand_box_moves_the_crop():
    img = Image.new("RGB", (4000, 3000), (255, 0, 0))
    img.paste((0, 0, 255), (2000, 0, 4000, 3000))   # right half blue
    p = PRESETS["li-square"]
    left = decode(export_image(img, p, box=(0.02, 0.1, 0.3, 0.4)))
    right = decode(export_image(img, p, box=(0.68, 0.1, 0.3, 0.4)))
    assert left.getpixel((600, 600))[0] > 200 and right.getpixel((600, 600))[2] > 200


def test_hand_box_ignored_for_width_only_presets():
    r = export_image(photo(), PRESETS["web-content"], box=(0.5, 0.5, 0.1, 0.1))
    assert decode(r).size == (1600, 1200)
