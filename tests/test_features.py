"""Tests for naming, size sets, limits, custom presets, preset updates, settings and the Pro gate."""
import io
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from aptus_engine import Naming, export_set, file_name, limit_warnings, load_limits, load_presets, slugify
from aptus_engine import presets as pm

PRESETS = load_presets()


def photo(w=3000, h=2000):
    """Noisy gradient: compresses like a real photo (pure noise would not)."""
    rng = np.random.default_rng(3)
    x = np.linspace(0, 255, w)[None, :, None]
    y = np.linspace(0, 255, h)[:, None, None]
    base = np.concatenate([np.broadcast_to(x, (h, w, 1)), np.broadcast_to(y, (h, w, 1)),
                           np.full((h, w, 1), 128.0)], axis=2)
    return Image.fromarray(np.clip(base + rng.normal(0, 40, (h, w, 3)), 0, 255).astype(np.uint8), "RGB")


# ---------- naming (SR SEO image naming guide) ----------

def test_slugify():
    assert slugify("Café & Bar's Kitchen!") == "cafe-and-bars-kitchen"
    assert slugify("  --Hello__World--  ") == "hello-world"


def test_seo_formula_order_and_numbering():
    n = Naming(location="Lancaster PA", descriptor="Timber frame barn", client="Smith Builders", shot="Wide")
    assert file_name("IMG_4031", "hero", "webp", n, 3) == "lancaster-pa-timber-frame-barn-smith-builders-wide-03-hero.webp"
    assert file_name("IMG_4031", "", "webp", n, 3, "1600w") == "lancaster-pa-timber-frame-barn-smith-builders-wide-03-1600w.webp"


def test_name_falls_back_to_source_name():
    assert file_name("Barn Photo!", "og-image", "jpg", Naming()) == "barn-photo-og-image.jpg"


def test_unnumbered_and_alt_text():
    n = Naming(location="Lancaster, PA", descriptor="timber frame barn", client="Smith Builders",
               shot="Wide shot of", numbered=False)
    assert file_name("x", "hero", "jpg", n, 4) == "lancaster-pa-timber-frame-barn-smith-builders-wide-shot-of-hero.jpg"
    alt = n.alt_text()
    assert alt == "Wide shot of timber frame barn for Smith Builders in Lancaster, PA"
    assert len(Naming(descriptor="x " * 100).alt_text()) <= 125


# ---------- size set and limits ----------

def test_size_set_makes_one_file_per_width():
    out = export_set(photo(3000, 2000), PRESETS["web-set"])
    assert [w for w, _ in out] == [640, 1024, 1600, 2560]
    assert [Image.open(io.BytesIO(r.data)).width for _, r in out] == [640, 1024, 1600, 2560]
    assert all(r.size_target_met or r.warnings for _, r in out)
    real = export_set(Image.open(Path(__file__).parent.parent / "static" / "sample.jpg"), PRESETS["web-set"])
    assert all(r.bytes <= 500_000 and r.size_target_met for _, r in real)


def test_size_set_never_upscales():
    out = export_set(photo(1200, 800), PRESETS["web-set"])
    assert [w for w, _ in out] == [640, 1024]
    assert "skipped" in out[-1][1].warnings[-1]


def test_webflow_limit_warning():
    limits = load_limits()
    assert limit_warnings(3_900_000, limits) == []
    assert "Webflow" in limit_warnings(4_100_000, limits)[0]


# ---------- presets: validation, custom, updates ----------

def test_make_preset_rejects_bad_values():
    base = {"id": "my-x", "group": "g", "name": "X", "width": 1000, "height": None, "format": "jpg",
            "quality": 80, "max_bytes": None}
    pm.make_preset(base)
    for bad in ({"format": "gif"}, {"width": None, "height": 500}, {"quality": 10}, {"width": 5},
                {"max_bytes": 100}, {"id": "Bad Id"}):
        with pytest.raises(ValueError):
            pm.make_preset({**base, **bad})


def test_custom_presets_round_trip(tmp_path):
    p = pm.make_preset({"id": "my-banner", "group": "My presets", "name": "Banner", "width": 1920,
                        "height": 600, "format": "webp", "quality": 80, "max_bytes": 300000})
    pm.save_custom(tmp_path / "c.json", {p.id: p})
    assert pm.load_custom(tmp_path / "c.json") == {"my-banner": p}
    (tmp_path / "bad.json").write_text("{not json")
    assert pm.load_custom(tmp_path / "bad.json") == {}


def test_update_only_when_newer_and_valid(tmp_path):
    bundled = pm.DEFAULT_PATH
    data = json.loads(bundled.read_text())
    cache = tmp_path / "cache.json"
    same = tmp_path / "same.json"
    same.write_text(json.dumps(data))
    assert pm.fetch_update(same.as_uri(), cache) == "current"
    newer = tmp_path / "newer.json"
    data["version"] += 1
    data["presets"][0]["quality"] = 75
    newer.write_text(json.dumps(data))
    assert pm.fetch_update(newer.as_uri(), cache) == "updated"
    assert pm.active_presets_path(cache=cache) == cache
    assert load_presets(pm.active_presets_path(cache=cache))["web-hero"].quality == 75
    broken = tmp_path / "broken.json"
    data["version"] += 1
    data["presets"][0]["format"] = "gif"
    broken.write_text(json.dumps(data))
    assert pm.fetch_update(broken.as_uri(), cache).startswith("skipped")
    assert pm.fetch_update("http://127.0.0.1:9/none.json", cache, timeout=1).startswith("skipped")


# ---------- server ----------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app as aptus_app
    monkeypatch.setattr(aptus_app, "OUT", None)
    monkeypatch.setattr(aptus_app, "DEFAULT_OUT", tmp_path / "default-out")
    monkeypatch.setenv("APTUS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APTUS_PRO", "1")
    monkeypatch.setenv("APTUS_NO_UPDATE", "1")
    return TestClient(aptus_app.app), tmp_path


def upload(c, w=3000, h=2000, name="a.jpg"):
    buf = io.BytesIO()
    photo(w, h).save(buf, "JPEG", quality=90)
    return c.post("/api/upload", files={"file": (name, buf.getvalue(), "image/jpeg")}).json()["id"]


def test_export_folder_setting(client):
    c, tmp = client
    mid = upload(c)
    c.post("/api/export", json={"id": mid, "presets": ["li-square"]})
    assert (tmp / "default-out" / "a-linkedin-square.jpg").exists()
    chosen = tmp / "chosen"
    st = c.post("/api/settings", json={"export_folder": str(chosen)}).json()
    assert st["export_folder"] == str(chosen)
    c.post("/api/export", json={"id": mid, "presets": ["li-square"]})
    assert (chosen / "a-linkedin-square.jpg").exists()
    assert c.post("/api/settings", json={"export_folder": "relative/path"}).status_code == 400
    assert c.get("/api/state").json()["settings"]["last_presets"] == ["li-square"]


def test_seo_names_size_set_and_alt(client):
    c, tmp = client
    mid = upload(c, name="IMG_0001.jpg")
    r = c.post("/api/export", json={"id": mid, "presets": ["web-set", "web-og"], "number": 2,
                                    "naming": {"location": "Lancaster", "descriptor": "barn studio",
                                               "client": "Stump and Root", "shot": "wide"}}).json()
    names = [x["file"] for x in r["results"]]
    assert names == ["lancaster-barn-studio-stump-and-root-wide-02-640w.webp",
                     "lancaster-barn-studio-stump-and-root-wide-02-1024w.webp",
                     "lancaster-barn-studio-stump-and-root-wide-02-1600w.webp",
                     "lancaster-barn-studio-stump-and-root-wide-02-2560w.webp",
                     "lancaster-barn-studio-stump-and-root-wide-02-og-image.jpg"]
    assert r["alt"] == "Wide barn studio for Stump and Root in Lancaster"
    assert c.get("/api/state").json()["settings"]["naming"]["client"] == "Stump and Root"


def test_custom_preset_api(client):
    c, _ = client
    made = c.post("/api/custom-presets", json={"name": "Shop banner", "width": 1920, "height": 600,
                                               "format": "webp", "quality": 80, "max_kb": 300}).json()
    assert made["id"] == "my-shop-banner" and made["max_bytes"] == 300000
    assert any(p["id"] == "my-shop-banner" for p in c.get("/api/presets").json())
    mid = upload(c)
    r = c.post("/api/export", json={"id": mid, "presets": ["my-shop-banner"]}).json()
    assert (r["results"][0]["width"], r["results"][0]["height"]) == (1920, 600)
    assert c.post("/api/custom-presets", json={"name": "Bad", "width": 5}).status_code == 400
    assert c.delete("/api/custom-presets/my-shop-banner").status_code == 200
    assert c.delete("/api/custom-presets/my-shop-banner").status_code == 404


def test_free_tier_gates(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("APTUS_PRO", "0")
    mid = upload(c)
    assert c.get("/api/state").json()["license"]["pro"] is False
    assert c.post("/api/export", json={"id": mid, "presets": ["li-square"]}).status_code == 200
    assert c.post("/api/export", json={"id": mid, "presets": ["li-square", "web-og"]}).status_code == 402
    assert c.post("/api/export", json={"id": mid, "presets": ["li-square"], "batch": True}).status_code == 402
    assert c.post("/api/custom-presets", json={"name": "X", "width": 800}).status_code == 402
    assert c.post("/api/contact-sheet", json={"id": mid}).status_code == 402
    assert c.post("/api/interval", json={"id": mid}).status_code == 402


def test_reference_matches_export_size(client):
    c, _ = client
    mid = upload(c)
    r = c.post("/api/reference", json={"id": mid, "preset": "ig-feed", "box": [0.1, 0.1, 0.3, 0.6]})
    assert r.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(r.content)).size == (1080, 1440)
    r = c.post("/api/reference", json={"id": mid, "preset": "web-set", "width": 1024})
    assert Image.open(io.BytesIO(r.content)).width == 1024


def test_mac_only_actions_explain_themselves(client, monkeypatch):
    import app as aptus_app
    c, _ = client
    monkeypatch.setattr(aptus_app, "IS_MAC", False)
    r = c.post("/api/reveal", json={})
    assert r.status_code == 501 and "Mac" in r.json()["detail"]


# ---------- review fixes ----------

def test_rotated_phone_video_reports_upright_size(tmp_path):
    import subprocess
    from aptus_engine import contact_sheet, grab_frame, probe
    flat, rot = tmp_path / "flat.mp4", tmp_path / "rot.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
                    "-pix_fmt", "yuv420p", str(flat)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(flat), "-c", "copy", str(rot)],
                   check=True)
    info = probe(rot)
    assert (info.width, info.height) == (180, 320) == grab_frame(rot, 0.5).size
    sheet = contact_sheet(rot, count=4, columns=2, tile_width=100, gap=0)
    assert sheet.size == (200, 2 * round(100 * 320 / 180))


def test_alt_text_cuts_at_a_word_break():
    alt = Naming(descriptor="word  " * 40).alt_text()
    assert len(alt) <= 125 and not alt.endswith(" ")


def test_local_only_guard(client, monkeypatch):
    import app as aptus_app
    c, _ = client
    assert c.get("/api/state", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/license/deactivate", headers={"Origin": "https://evil.example"}).status_code == 403
    monkeypatch.setattr(aptus_app, "ALLOWED_HOSTS", {"127.0.0.1"})
    assert c.get("/api/state").status_code == 403          # Host: testserver, like a DNS-rebinding page
    monkeypatch.setattr(aptus_app, "ALLOWED_HOSTS", {"127.0.0.1", "testserver"})
    monkeypatch.setenv("APTUS_TOKEN", "secret-token")
    assert c.get("/api/state").status_code == 403          # no cookie yet
    assert c.get("/static/sample.jpg").status_code == 200  # static files stay open
    r = c.get("/?token=secret-token", follow_redirects=False)
    assert r.status_code == 303 and "aptus=secret-token" in r.headers["set-cookie"]
    c.cookies.set("aptus", "secret-token")
    assert c.get("/api/state").status_code == 200


def test_slug_cuts_at_a_word_break():
    assert slugify("timber frame barn restoration with original hand hewn beams", 40) == "timber-frame-barn-restoration-with"
    assert slugify("a" * 50, 40) == "a" * 40   # one long word still gets cut
