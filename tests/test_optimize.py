"""Optimize tests: destinations, the size pipeline, metadata, copies, and the batch endpoint.
Run from the project folder: python -m pytest -q tests/test_optimize.py"""
import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from aptus_engine import presets as presets_mod
from aptus_engine.optimize import (FLOORS, Settings, load_destinations, make_destination, optimize_image,
                                   output_size)

D = load_destinations(presets_mod.DEFAULT_PATH)


def photo(w=3000, h=2000, noise=20, seed=3):
    """A gradient with grain: compresses like a detailed photo."""
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 255, w)[None, :, None]
    y = np.linspace(0, 255, h)[:, None, None]
    base = np.concatenate([np.broadcast_to(x, (h, w, 1)), np.broadcast_to(y, (h, w, 1)),
                           np.full((h, w, 1), 128.0)], axis=2)
    return Image.fromarray(np.clip(base + rng.normal(0, noise, (h, w, 3)), 0, 255).astype(np.uint8), "RGB")


def as_file(img, fmt="JPEG", **kw):
    """Save and reopen, so the image has a real format and info like a dropped file."""
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    data = buf.getvalue()
    return Image.open(io.BytesIO(data)), len(data), data


def decode(r):
    return Image.open(io.BytesIO(r.data))


def with_gps_and_credit():
    e = Image.Exif()
    e[0x013B] = "Tim Medina"
    e[0x8298] = "Stump and Root Co."
    e.get_ifd(0x8769)[0x9003] = "2026:10:01 09:30:00"
    e.get_ifd(0x8825)[2] = (40.0, 1.0, 0.0)   # GPS latitude
    return e


# ---------- destinations ----------

def test_destinations_load_and_are_valid():
    assert len(D) == 13
    assert D["web-content"].long_edge == 1600 and D["web-content"].max_bytes == 200_000
    assert D["web-hero"].max_bytes == 300_000
    assert D["webflow-master"].format == "webp" and D["webflow-master"].upload_cap == 4_000_000
    assert D["proof"].keep == ("copyright", "date")
    assert all(d.format not in ("avif", "jxl") for d in D.values() if d.group == "Website")   # Webflow-safe defaults
    assert D["archive"].archive and D["archive"].format == "jxl" and D["archive"].fit == "original"


def test_destination_checks():
    base = {"id": "x", "group": "G", "name": "X", "format": "webp", "quality": 80, "long_edge": 1000}
    make_destination(base)
    with pytest.raises(ValueError, match="Unknown"):
        make_destination({**base, "crop": True})
    with pytest.raises(ValueError, match="either a long edge or a width"):
        make_destination({**base, "width": 800})
    with pytest.raises(ValueError, match="quality"):
        make_destination({**base, "quality": None})


def test_preset_update_with_a_bad_destination_is_refused(tmp_path, monkeypatch):
    data = json.loads(presets_mod.DEFAULT_PATH.read_text())
    data["version"] += 1
    data["optimize"][0]["format"] = "gif"
    served = tmp_path / "served.json"
    served.write_text(json.dumps(data))
    status = presets_mod.fetch_update(served.as_uri(), tmp_path / "cache.json")
    assert status.startswith("skipped")
    assert not (tmp_path / "cache.json").exists()


# ---------- size and shape ----------

@pytest.mark.parametrize("did", [k for k, d in D.items() if not d.archive])
def test_every_destination_keeps_shape_and_meets_its_cap(did):
    d = D[did]
    src = photo()
    r = optimize_image(src, d)
    out = decode(r)
    assert abs(out.width / out.height - 1.5) < 0.01                 # never cropped or stretched
    assert out.width <= 3000 and out.height <= 2000                 # never enlarged
    if r.state in ("done", "resized_to_fit"):
        assert d.cap is None or r.nbytes <= d.cap
    if r.quality is not None:
        assert r.quality >= FLOORS[r.format]


def test_never_upscales_a_small_photo():
    r = optimize_image(photo(900, 600), D["web-content"])
    assert (r.width, r.height) == (900, 600)
    assert any("never enlarged" in n for n in r.notes)


def test_long_edge_fits_portraits_by_height():
    assert output_size(2000, 3000, D["web-content"]) == (1067, 1600)
    assert output_size(3000, 2000, D["social-x"]) == (1600, 1067)        # fits inside 1600 × 1600
    assert output_size(2000, 3000, D["social-instagram"]) == (1080, 1620)  # width-fit keeps the full height


def test_steps_dimensions_down_instead_of_going_below_the_floor():
    d = Settings(max_bytes=300_000).apply(D["web-hero"])   # this grain is 654 KB at 2400 px and quality 65
    r = optimize_image(photo(), d)
    assert r.state == "resized_to_fit"
    assert r.nbytes <= 300_000 and r.quality >= FLOORS["webp"]
    assert 1800 <= max(r.width, r.height) < 2400   # one 25% step, not a jump to the 1600 px minimum
    assert any("Stepped down" in n for n in r.notes)


def test_reports_a_target_it_cannot_meet():
    d = Settings(max_bytes=20_000, allow_downscale=False).apply(D["web-hero"])
    r = optimize_image(photo(), d)
    assert r.state == "attention"
    assert (r.width, r.height) == (2400, 1600)
    assert any("Couldn't get under 20 KB" in n for n in r.notes)


def test_settings_override_format_and_size():
    d = Settings(format="avif", long_edge=1000, max_bytes=60_000).apply(D["web-content"])
    r = optimize_image(photo(), d)
    assert decode(r).format == "AVIF" and max(r.width, r.height) <= 1000 and r.nbytes <= 60_000


# ---------- platforms ----------

def test_warns_when_a_platform_will_crop():
    tall = photo(1080, 1920)   # 9:16
    r = optimize_image(tall, D["social-instagram"])
    assert r.state == "attention" and r.data
    assert any("Instagram shows 3:4 to 1.91:1" in n and "9:16" in n for n in r.notes)
    ok = optimize_image(photo(1080, 1350), D["social-instagram"])   # 4:5 is inside the range
    assert ok.state == "done"


def test_transparency_becomes_png_for_jpeg_destinations():
    img = Image.new("RGBA", (1600, 1000), (200, 50, 50, 0))
    img.paste((20, 20, 20, 255), (200, 200, 900, 700))
    r = optimize_image(img, D["email"])
    assert r.format == "png" and decode(r).mode == "RGBA"


# ---------- metadata ----------

def test_strips_gps_and_everything_else_by_default():
    src, n, _ = as_file(photo(), exif=with_gps_and_credit())
    exif = decode(optimize_image(src, D["web-content"], n)).getexif()
    assert not exif.get_ifd(0x8825) and 0x013B not in exif and 0x8298 not in exif


def test_keeps_copyright_and_capture_date_when_asked():
    src, n, _ = as_file(photo(), exif=with_gps_and_credit())
    exif = decode(optimize_image(src, D["proof"], n)).getexif()
    assert exif[0x013B] == "Tim Medina" and exif[0x8298] == "Stump and Root Co."
    assert exif.get_ifd(0x8769)[0x9003] == "2026:10:01 09:30:00"
    assert not exif.get_ifd(0x8825)   # GPS never survives


# ---------- already fits ----------

def test_copies_a_file_that_already_fits():
    src, n, _ = as_file(photo(1000, 667), "WEBP", quality=70)
    r = optimize_image(src, D["web-content"], n)
    assert r.state == "already_fit" and r.data is None and r.nbytes == n


def test_reencodes_a_small_file_with_gps():
    e = Image.Exif()
    e.get_ifd(0x8825)[2] = (40.0, 1.0, 0.0)
    src, n, _ = as_file(photo(1000, 667), "WEBP", quality=70, exif=e)
    r = optimize_image(src, D["web-content"], n)
    assert r.data is not None and not decode(r).getexif().get_ifd(0x8825)


def test_reencodes_when_the_format_differs():
    src, n, _ = as_file(photo(1000, 667), "JPEG", quality=70)
    assert optimize_image(src, D["web-content"], n).data is not None


# ---------- the endpoint ----------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app as aptus_app
    monkeypatch.setattr(aptus_app, "OUT", tmp_path / "exports")
    monkeypatch.setenv("APTUS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APTUS_PRO", "1")
    monkeypatch.setenv("APTUS_NO_UPDATE", "1")
    return TestClient(aptus_app.app), tmp_path / "exports"


def upload(c, name="Barn.jpg", img=None):
    _, _, data = as_file(img or photo())
    return c.post("/api/upload", files={"file": (name, data, "image/jpeg")}).json()


def test_optimize_endpoint_writes_files(client):
    c, out = client
    assert len(c.get("/api/optimize/destinations").json()) == 13
    up = upload(c)
    d = c.post("/api/optimize", json={"id": up["id"], "destinations": ["web-content", "email"]}).json()
    assert [r["destination_id"] for r in d["results"]] == ["web-content", "email"]
    for r in d["results"]:
        f = out / r["file"]
        assert f.is_file() and f.stat().st_size == r["bytes"] <= r["max_bytes"]
        assert r["state"] in ("done", "resized_to_fit") and r["src_width"] == 3000
    assert {Image.open(out / r["file"]).format for r in d["results"]} == {"WEBP", "JPEG"}


def test_optimize_settings_and_reference(client):
    c, out = client
    up = upload(c)
    d = c.post("/api/optimize", json={"id": up["id"], "destinations": ["web-content"],
                                      "settings": {"max_kb": 80, "long_edge": 1200, "format": "jpg"}}).json()
    r = d["results"][0]
    assert r["format"] == "jpg" and r["bytes"] <= 80_000 and max(r["width"], r["height"]) <= 1200
    ref = c.post("/api/optimize/reference", json={"id": up["id"], "width": r["width"], "height": r["height"]})
    assert Image.open(io.BytesIO(ref.content)).size == (r["width"], r["height"])
    bad = c.post("/api/optimize", json={"id": up["id"], "destinations": ["web-content"], "settings": {"max_kb": 2}})
    assert bad.status_code == 400


def test_optimize_refuses_unknown_destinations(client):
    c, _ = client
    up = upload(c)
    assert c.post("/api/optimize", json={"id": up["id"], "destinations": ["nope"]}).status_code == 400


def test_free_optimizes_ten_files_for_one_destination(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("APTUS_PRO", "0")
    small = photo(400, 300)
    ids = [upload(c, f"p{i}.jpg", small)["id"] for i in range(11)]
    two = c.post("/api/optimize", json={"id": ids[0], "destinations": ["web-content", "email"]})
    assert two.status_code == 402
    big = c.post("/api/optimize", json={"id": ids[0], "destinations": ["web-content"], "batch_total": 11})
    assert big.status_code == 402
    for i in ids[:10]:
        r = c.post("/api/optimize", json={"id": i, "destinations": ["web-content"], "batch_id": "b1", "batch_total": 10})
        assert r.status_code == 200
    eleventh = c.post("/api/optimize", json={"id": ids[10], "destinations": ["web-content"], "batch_id": "b1", "batch_total": 10})
    assert eleventh.status_code == 402 and "10 files" in eleventh.json()["detail"]


def test_preview_thumbnail_width(client):
    c, _ = client
    up = upload(c)
    thumb = Image.open(io.BytesIO(c.get(f"/api/preview/{up['id']}?w=120").content))
    assert thumb.width == 120


# ---------- Pro in built apps ----------

def test_built_app_without_licensing_is_pro(monkeypatch):
    import sys
    import app as aptus_app
    monkeypatch.delenv("APTUS_PRO", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(aptus_app, "license_manager", lambda: None)
    assert aptus_app.license_state() == {"pro": True, "source": "unlicensed", "configured": False}


def test_built_app_with_licensing_and_no_license_is_free(monkeypatch):
    import sys
    import app as aptus_app

    class NoLicense:
        def current_license(self):
            return None

    monkeypatch.delenv("APTUS_PRO", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(aptus_app, "license_manager", lambda: NoLicense())
    assert aptus_app.license_state()["pro"] is False


# ---------- JPEG XL ----------
# These need libjxl's cjxl. They skip on a machine without it, except in the Mac build, which sets
# APTUS_REQUIRE_JXL so a missing or broken encoder fails the build instead of passing quietly.
import os

from aptus_engine import jxl

if os.environ.get("APTUS_REQUIRE_JXL") == "1":
    assert jxl.available(), "APTUS_REQUIRE_JXL is set but cjxl wasn't found"
needs_jxl = pytest.mark.skipif(not jxl.available(), reason="cjxl isn't installed")


def is_jxl(data):
    return data[:2] == b"\xff\x0a" or data[4:8] == b"JXL "


def test_archive_destination_checks():
    base = {"id": "a", "group": "Archive", "name": "A", "format": "jxl", "archive": True}
    make_destination(base)
    with pytest.raises(ValueError, match="archive"):
        make_destination({**base, "long_edge": 2000})
    with pytest.raises(ValueError, match="archive"):
        make_destination({**base, "format": "webp"})
    assert Settings(format="webp", max_bytes=50_000).apply(D["archive"]) == D["archive"]


def test_archive_copies_already_lossy_files(tmp_path):
    src, n, data = as_file(photo(800, 600), "WEBP", quality=80)
    r = optimize_image(src, D["archive"], n, source_path=tmp_path / "unused.webp")
    assert r.data is None and r.state == "already_fit" and "copied unchanged" in r.notes[0]


@needs_jxl
def test_archive_repacks_a_jpeg_losslessly(tmp_path):
    src, n, data = as_file(photo(1600, 1067), "JPEG", quality=92, exif=with_gps_and_credit())
    path = tmp_path / "shoot.jpg"
    path.write_bytes(data)
    r = optimize_image(src, D["archive"], n, source_path=path)
    assert r.state == "done" and r.format == "jxl" and is_jxl(r.data)
    assert r.nbytes < n and (r.width, r.height) == (1600, 1067)
    assert "exact original JPEG" in r.notes[0]


@needs_jxl
def test_archive_makes_lossless_jxl_from_png(tmp_path):
    src, n, data = as_file(photo(800, 600, noise=4), "PNG")
    path = tmp_path / "graphic.png"
    path.write_bytes(data)
    r = optimize_image(src, D["archive"], n, source_path=path)
    assert r.format == "jxl" and is_jxl(r.data) and r.nbytes < n


@needs_jxl
def test_jxl_format_meets_the_cap_and_warns_for_webflow():
    d = Settings(format="jxl").apply(D["web-content"])
    r = optimize_image(photo(), d)
    assert r.format == "jxl" and is_jxl(r.data) and r.nbytes <= 200_000
    assert r.quality >= FLOORS["jxl"] and max(r.width, r.height) <= 1600
    assert r.state == "attention" and any("Webflow doesn't accept JPEG XL" in n for n in r.notes)


@needs_jxl
def test_jxl_for_delivery_has_no_warning():
    r = optimize_image(photo(1200, 800), Settings(format="jxl").apply(D["proof"]))
    assert r.format == "jxl" and r.state == "done"


def test_server_refuses_jxl_without_the_encoder(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(jxl, "available", lambda: False)
    up = upload(c)
    r = c.post("/api/optimize", json={"id": up["id"], "destinations": ["web-content"], "settings": {"format": "jxl"}})
    assert r.status_code == 400 and "cjxl" in r.json()["detail"]
    assert c.get("/api/state").json()["jxl"] is False
