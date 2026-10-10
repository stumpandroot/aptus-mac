"""Aptus for Mac: the app window.

The window shows the same page as the browser preview. Inside the app the page finds the
local engine (app.py) on a private port, so exports are made by Python, saved to a folder,
and the Mac extras (Show in Finder, Quick Look, the folder picker, licensing) switch on.

Run from source:   python3 mac/main.py
Checks:            Aptus --self-test    (bundled parts are present and work)
                   Aptus --smoke-test   (starts the engine, exports the sample, opens and closes the window)
"""
from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

if not getattr(sys, "frozen", False):   # running from source: make the project importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("APTUS_APP", "1")

# The app carries its own list of trusted certificates (certifi), so preset updates and license
# activation can make HTTPS connections on any Mac.
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_engine(port: int):
    """Run the local engine in a background thread and wait until it answers."""
    import uvicorn

    import app as aptus_app

    server = uvicorn.Server(uvicorn.Config(aptus_app.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/static/sample.jpg", timeout=1):
                return server
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("The Aptus engine didn't start.")


# ---------- checks used by the build ----------

def self_test() -> int:
    """Confirm every bundled part is present and working. Prints one line per check."""
    problems = []

    def check(name, fn):
        try:
            fn()
            print(f"ok    {name}")
        except Exception as e:   # report every failure, not just the first
            problems.append(name)
            print(f"FAIL  {name}: {e}")

    def encoders():
        from PIL import Image
        img = Image.new("RGB", (64, 64), (63, 184, 164))
        for fmt in ("JPEG", "PNG", "WEBP", "AVIF"):
            buf = io.BytesIO()
            img.save(buf, fmt)
            assert buf.tell() > 0, fmt

    def heic():
        import pillow_heif  # noqa: F401

    def ffmpeg():
        import subprocess
        from aptus_engine.video import _bin
        for name in ("ffmpeg", "ffprobe"):
            path = _bin(name)
            out = subprocess.run([path, "-hide_banner", "-version"], capture_output=True, text=True, check=True).stdout
            assert "--enable-gpl" not in out, f"{name} is a GPL build; Aptus ships LGPL only"
            if getattr(sys, "frozen", False):
                assert path.startswith(sys._MEIPASS), f"{name} isn't the bundled copy: {path}"

    def presets():
        from aptus_engine import load_presets
        assert len(load_presets()) >= 12

    def page():
        import app as aptus_app
        assert (aptus_app.ROOT / "static" / "index.html").is_file()
        assert (aptus_app.ROOT / "static" / "sample.jpg").is_file()
        assert (aptus_app.ROOT / "static" / "fonts" / "caveat-latin-600-normal.woff2").is_file()

    def licensing():
        import greenlaunch_license  # noqa: F401
        import cryptography  # noqa: F401

    def window():
        import webview  # noqa: F401

    def https():
        import ssl
        assert Path(os.environ.get("SSL_CERT_FILE", "")).is_file(), "no certificate list bundled"
        assert ssl.create_default_context().cert_store_stats()["x509_ca"] > 50, "certificate list is empty"

    for name, fn in [("image encoders (JPG, PNG, WebP, AVIF)", encoders), ("HEIC support", heic),
                     ("ffmpeg and ffprobe (LGPL)", ffmpeg), ("presets", presets), ("interface files", page),
                     ("license check", licensing), ("window (pywebview)", window),
                     ("HTTPS certificates", https)]:
        check(name, fn)
    print("Self-test passed." if not problems else f"Self-test failed: {', '.join(problems)}")
    return 0 if not problems else 1


def smoke_test(window: bool = True) -> int:
    """Start the engine, export the sample to a temporary folder, then open and close the window."""
    out = Path(tempfile.mkdtemp(prefix="aptus-smoke-"))
    os.environ.update({"APTUS_OUT": str(out), "APTUS_HOME": str(out / "home"), "APTUS_NO_UPDATE": "1"})
    import app as aptus_app
    aptus_app.OUT = out
    port = free_port()
    start_engine(port)
    base = f"http://127.0.0.1:{port}"

    sample = (aptus_app.ROOT / "static" / "sample.jpg").read_bytes()
    boundary = "aptusSmokeTest"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"sample.jpg\"\r\n"
            f"Content-Type: image/jpeg\r\n\r\n").encode() + sample + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{base}/api/upload", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    mid = json.load(urllib.request.urlopen(req, timeout=30))["id"]
    req = urllib.request.Request(f"{base}/api/export", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"id": mid, "presets": ["li-square"]}).encode())
    result = json.load(urllib.request.urlopen(req, timeout=60))["results"][0]
    assert (out / result["file"]).is_file() and result["width"] == 1200, result
    print(f"ok    engine exported {result['file']} ({result['bytes']} bytes)")

    if not window:
        return 0
    import webview
    status = {"ok": False}

    def check_page(win):
        for _ in range(60):
            time.sleep(0.5)
            try:
                if "Running on your Mac" in (win.evaluate_js("document.getElementById('modeTag').textContent") or ""):
                    status["ok"] = True
                    break
            except Exception:
                pass
        print("ok    window loaded the interface" if status["ok"] else "FAIL  window never showed the interface")
        win.destroy()

    win = webview.create_window("Aptus smoke test", base, width=1100, height=760)
    webview.start(check_page, win)
    return 0 if status["ok"] else 1


# ---------- the app ----------

def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if "--smoke-test" in sys.argv:
        return smoke_test(window="--no-window" not in sys.argv)

    import secrets

    import webview

    token = secrets.token_urlsafe(24)
    os.environ["APTUS_TOKEN"] = token   # only this window can use the engine
    port = free_port()
    start_engine(port)

    def weekly_license_refresh():
        import app as aptus_app
        lm = aptus_app.license_manager()
        if lm is not None:
            try:
                lm.refresh_if_due()
            except Exception:
                pass

    threading.Thread(target=weekly_license_refresh, daemon=True).start()
    win = webview.create_window(
        "Aptus", f"http://127.0.0.1:{port}/?token={token}",
        width=1320, height=880, min_size=(900, 640), background_color="#FAF8F2",
        text_select=True,
    )
    import app as aptus_app

    def pick_folder():   # the native folder panel, attached to the Aptus window
        chosen = win.create_file_dialog(webview.FOLDER_DIALOG)
        return chosen[0] if chosen else None

    aptus_app.FOLDER_PICKER = pick_folder
    # private_mode=False keeps the window's storage between launches.
    webview.start(private_mode=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
