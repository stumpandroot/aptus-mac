"""App (server) tests. Run from the project folder: python -m pytest -q"""
import io
import subprocess

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app as aptus_app
    monkeypatch.setattr(aptus_app, "OUT", tmp_path / "exports")
    monkeypatch.setenv("APTUS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APTUS_PRO", "1")
    monkeypatch.setenv("APTUS_NO_UPDATE", "1")
    return TestClient(aptus_app.app), tmp_path / "exports"


def jpeg_bytes(w=3000, h=2000, orientation=None):
    buf = io.BytesIO()
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    Image.new("RGB", (w, h), (91, 200, 181)).save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def test_page_and_presets(client):
    c, _ = client
    assert "Aptus" in c.get("/").text
    assert len(c.get("/api/presets").json()) == 12


def test_rejects_unknown_file_type(client):
    c, _ = client
    r = c.post("/api/upload", files={"file": ("notes.txt", b"hi", "text/plain")})
    assert r.status_code == 400


def test_image_export_flow(client):
    c, out = client
    up = c.post("/api/upload", files={"file": ("Barn Photo!.jpg", jpeg_bytes(), "image/jpeg")}).json()
    assert (up["kind"], up["width"], up["height"]) == ("image", 3000, 2000)
    assert c.get(f"/api/preview/{up['id']}").headers["content-type"] == "image/jpeg"
    r = c.post("/api/export", json={"id": up["id"], "presets": ["ig-feed", "web-hero"], "focal": [0.3, 0.5]})
    assert r.status_code == 200, r.text
    files = sorted(p.name for p in out.iterdir())
    assert files == ["barn-photo-hero.webp", "barn-photo-instagram-3x4.jpg"]
    assert Image.open(out / "barn-photo-instagram-3x4.jpg").size == (1080, 1440)
    # A second export never overwrites the first.
    c.post("/api/export", json={"id": up["id"], "presets": ["ig-feed"]})
    assert (out / "barn-photo-instagram-3x4-2.jpg").exists()


def test_rotated_photo_reports_display_size(client):
    c, _ = client
    up = c.post("/api/upload", files={"file": ("phone.jpg", jpeg_bytes(orientation=6), "image/jpeg")}).json()
    assert (up["width"], up["height"]) == (2000, 3000)


def test_unknown_preset_rejected(client):
    c, _ = client
    up = c.post("/api/upload", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")}).json()
    assert c.post("/api/export", json={"id": up["id"], "presets": ["nope"]}).status_code == 400


def test_video_flow(client, tmp_path):
    c, out = client
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30:duration=4", "-pix_fmt", "yuv420p", str(clip)], check=True)
    up = c.post("/api/upload", files={"file": ("shop tour.mp4", clip.read_bytes(), "video/mp4")}).json()
    assert up["kind"] == "video" and abs(up["duration"] - 4) < 0.1
    assert c.get(f"/api/preview/{up['id']}?t=1.5").status_code == 200
    assert len(c.post("/api/sharp", json={"id": up["id"], "t": 2.0}).json()) >= 1
    r = c.post("/api/export", json={"id": up["id"], "presets": ["yt-thumb"], "t": 2.0}).json()
    assert r["results"][0]["file"] == "shop-tour-2-00s-youtube-thumbnail.jpg"
    assert len(c.post("/api/interval", json={"id": up["id"], "every": 1}).json()["files"]) == 4
    assert c.post("/api/contact-sheet", json={"id": up["id"]}).json()["file"] == "shop-tour-contact-sheet.jpg"


def test_page_is_a_full_document(client):
    c, _ = client
    html = c.get("/").text
    assert html.startswith("<!doctype html>") and "<title>Aptus</title>" in html


def test_export_thumbnails_served_and_traversal_blocked(client):
    c, out = client
    up = c.post("/api/upload", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")}).json()
    name = c.post("/api/export", json={"id": up["id"], "presets": ["li-square"]}).json()["results"][0]["file"]
    r = c.get(f"/api/file/{name}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert c.get("/api/file/..%2Fapp.py").status_code == 404


def test_export_with_hand_placed_box(client):
    c, out = client
    up = c.post("/api/upload", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")}).json()
    r = c.post("/api/export", json={"id": up["id"], "presets": ["ig-feed", "web-hero"],
                                    "boxes": {"ig-feed": [0.5, 0.1, 0.3, 0.675]}})
    assert r.status_code == 200
    assert r.json()["results"][0]["width"] == 1080
    bad = c.post("/api/export", json={"id": up["id"], "presets": ["ig-feed"], "boxes": {"ig-feed": [0.5, 0.1, 1.4, 0.6]}})
    assert bad.status_code == 400
