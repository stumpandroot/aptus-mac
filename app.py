"""Aptus local app: a small web server plus the page, used by the Mac app window and for testing.

Run:  python app.py   then open http://127.0.0.1:8765
Settings, custom presets and the license live in ~/Library/Application Support/GreenLaunch/aptus
(set APTUS_HOME to change it). Exports go to ~/Desktop/Aptus Exports unless changed in Settings.
"""
from __future__ import annotations

import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi import Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel

from aptus_engine import (Naming, contact_sheet, export_image, export_set, file_name, grab_frame,
                          limit_warnings, load_limits, load_presets, probe, sharp_frames, slugify)
from aptus_engine import presets as presets_mod
from aptus_engine import images as images_mod
from aptus_engine.images import render_image
from aptus_engine.video import interval_frames

try:   # opens iPhone HEIC photos when pillow-heif is installed
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
VERSION = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else "1.0.0-dev"
# The Mac app window sets this to its native folder dialog. Returns a path, or None if cancelled.
FOLDER_PICKER = None
IS_MAC = platform.system() == "Darwin"
WORK = Path(tempfile.mkdtemp(prefix="aptus-"))
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".heic", ".tif", ".tiff", ".bmp", ".gif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
# Where the online preset list lives. Host presets.json here (bump "version" on each change).
PRESETS_URL = os.environ.get("APTUS_PRESETS_URL", "https://greenlaunch.co/aptus/presets.json")


def home() -> Path:
    if os.environ.get("APTUS_HOME"):
        return Path(os.environ["APTUS_HOME"])
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "GreenLaunch" / "aptus"
    return Path.home() / ".config" / "aptus"


DEFAULT_OUT = Path.home() / "Desktop" / "Aptus Exports"


# ---------- settings ----------

DEFAULT_SETTINGS = {
    "export_folder": "",          # empty = DEFAULT_OUT
    "last_presets": ["web-hero", "ig-feed"],
    "naming": {"location": "", "descriptor": "", "client": "", "shot": "", "numbered": True},
    "check_updates": True,
}


def read_settings() -> dict:
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        saved = json.loads((home() / "settings.json").read_text())
        s.update({k: v for k, v in saved.items() if k in DEFAULT_SETTINGS})
    except (OSError, json.JSONDecodeError):
        pass
    return s


def write_settings(s: dict) -> None:
    home().mkdir(parents=True, exist_ok=True)
    tmp = home() / "settings.tmp"
    tmp.write_text(json.dumps(s, indent=2))
    tmp.replace(home() / "settings.json")


OUT = Path(os.environ["APTUS_OUT"]) if os.environ.get("APTUS_OUT") else None


def out_dir() -> Path:
    """The export folder: APTUS_OUT for testing, otherwise the Settings choice, otherwise the Desktop."""
    if OUT is not None:
        return Path(OUT)
    chosen = read_settings().get("export_folder")
    return Path(chosen) if chosen else DEFAULT_OUT


# ---------- presets ----------

def cache_path() -> Path:
    return home() / "presets-update.json"


BUILTIN = load_presets(presets_mod.active_presets_path(cache=cache_path()))
LIMITS = load_limits(presets_mod.active_presets_path(cache=cache_path()))


def reload_builtin() -> None:
    global BUILTIN, LIMITS
    path = presets_mod.active_presets_path(cache=cache_path())
    BUILTIN, LIMITS = load_presets(path), load_limits(path)


def custom_presets():
    return presets_mod.load_custom(home() / "custom-presets.json")


def all_presets():
    return {**BUILTIN, **custom_presets()}


# Kept for older callers and tests that import PRESETS.
PRESETS = BUILTIN


# ---------- license ----------

def license_manager():
    try:
        from greenlaunch_license import LicenseManager
        return LicenseManager(app="aptus", storage_dir=home())
    except Exception:
        return None   # the public key hasn't been pasted in yet, or cryptography is missing


def license_state() -> dict:
    """Pro or Free, and why. APTUS_PRO=1/0 forces it (testing). Running from source counts as Pro."""
    lm = license_manager()
    configured = lm is not None
    forced = os.environ.get("APTUS_PRO")
    if forced in ("0", "1"):
        return {"pro": forced == "1", "source": "forced", "configured": configured}
    if lm is not None:
        try:
            lic = lm.current_license()
        except Exception:
            lic = None
        if lic:
            return {"pro": True, "source": "license", "configured": True, "email": lic.get("email", "")}
    if not getattr(sys, "frozen", False):
        return {"pro": True, "source": "developer", "configured": configured}
    return {"pro": False, "source": "free", "configured": configured}


def need_pro(feature: str) -> None:
    if not license_state()["pro"]:
        raise HTTPException(402, f"{feature} is part of Aptus Pro.")


# ---------- app ----------

@asynccontextmanager
async def lifespan(_app):
    check_for_preset_update()
    yield


app = FastAPI(title="Aptus", lifespan=lifespan)

# Only this Mac's own pages may use the engine. The Host check stops DNS-rebinding tricks and the
# Origin check stops other websites from sending requests. In the Mac app, a random token from the
# window's first page load (kept in a same-site cookie) is also required.
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


@app.middleware("http")
async def guard(request: Request, call_next):
    host_header = request.headers.get("host", "")
    if host_header.rsplit(":", 1)[0] not in ALLOWED_HOSTS:
        return Response("Aptus only answers its own window.", status_code=403)
    origin = request.headers.get("origin")
    if origin and origin not in ("null",) and origin.split("://", 1)[-1] != host_header:
        return Response("Aptus only answers its own window.", status_code=403)
    token = os.environ.get("APTUS_TOKEN")
    if token and not request.url.path.startswith("/static/") and request.url.path != "/favicon.ico":
        if request.url.path == "/" and request.query_params.get("token") == token:
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie("aptus", token, httponly=True, samesite="strict")
            return resp
        if request.cookies.get("aptus") != token:
            return Response("Open Aptus from its app window.", status_code=403)
    return await call_next(request)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
MEDIA: dict[str, dict] = {}   # id -> {"path": Path, "kind": "image"|"video", "stem": str}


def check_for_preset_update():
    """Download a newer preset list in the background. Offline or a bad file changes nothing."""
    if read_settings().get("check_updates") and not os.environ.get("APTUS_NO_UPDATE"):
        def run():
            if presets_mod.fetch_update(PRESETS_URL, cache_path()) == "updated":
                reload_builtin()
        threading.Thread(target=run, daemon=True).start()


def media(mid: str) -> dict:
    if mid not in MEDIA:
        raise HTTPException(404, "File not found. Drop it in again.")
    return MEDIA[mid]


def safe_stem(name: str) -> str:
    stem = re.sub(r"[^\w\-]+", "-", Path(name).stem).strip("-")
    return stem or "image"


def unique_path(path: Path) -> Path:
    n, candidate = 2, path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1
    return candidate


def jpeg_response(img: Image.Image, max_width: int = 1400) -> Response:
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg")


def export_file(name: str) -> Path:
    folder = out_dir().resolve()
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        raise HTTPException(404, "That export isn't in the export folder.")
    return path


PAGE_HEAD = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>')


@app.get("/")
def index():
    # The page is written as a body fragment so the same file also publishes as the browser preview.
    html = (ROOT / "static" / "index.html").read_text()
    return Response(PAGE_HEAD + html + "</body></html>", media_type="text/html")


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/api/file/{name}")
def exported_file(name: str):
    """Serve a file from the export folder, for result thumbnails and the 100% check."""
    return FileResponse(export_file(name))


@app.get("/api/presets")
def presets():
    return [p.to_dict() for p in all_presets().values()]


@app.get("/api/state")
def state():
    s = read_settings()
    return {
        "version": VERSION, "mac": IS_MAC, "license": license_state(), "settings": s,
        "export_folder": str(out_dir()), "default_folder": str(DEFAULT_OUT),
        "limits": [lim.__dict__ for lim in LIMITS],
        "presets_version": presets_mod.file_version(presets_mod.active_presets_path(cache=cache_path())),
    }


class SettingsPatch(BaseModel):
    export_folder: str | None = None
    last_presets: list[str] | None = None
    naming: dict | None = None
    check_updates: bool | None = None


@app.post("/api/settings")
def save_settings(patch: SettingsPatch):
    s = read_settings()
    if patch.export_folder is not None:
        folder = patch.export_folder.strip()
        if folder:
            p = Path(folder).expanduser()
            if not p.is_absolute():
                raise HTTPException(400, "Use a full folder path, like /Users/you/Desktop/Exports.")
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError:
                raise HTTPException(400, "Aptus can't create or write to that folder.")
            folder = str(p)
        s["export_folder"] = folder
    if patch.last_presets is not None:
        s["last_presets"] = [x for x in patch.last_presets if isinstance(x, str)][:40]
    if patch.naming is not None:
        n = s["naming"]
        for k in ("location", "descriptor", "client", "shot"):
            if k in patch.naming:
                n[k] = str(patch.naming[k])[:80]
        if "numbered" in patch.naming:
            n["numbered"] = bool(patch.naming["numbered"])
    if patch.check_updates is not None:
        s["check_updates"] = patch.check_updates
    write_settings(s)
    return state()


@app.post("/api/choose-folder")
def choose_folder():
    """Native folder picker on a Mac (works from the app window and from a browser on the same Mac)."""
    if FOLDER_PICKER is not None:
        chosen = FOLDER_PICKER()
    elif IS_MAC:   # a browser on the same Mac: AppleScript shows the standard folder panel
        r = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt "Save Aptus exports to:")'],
                           capture_output=True, text=True)
        chosen = r.stdout.strip() if r.returncode == 0 else None
    else:
        raise HTTPException(501, "The folder picker needs a Mac. Type a folder path instead.")
    if not chosen:
        return {"cancelled": True}
    return save_settings(SettingsPatch(export_folder=chosen))


class FileAction(BaseModel):
    file: str | None = None


@app.post("/api/reveal")
def reveal(req: FileAction):
    """Show in Finder: the file selected, or the export folder."""
    if not IS_MAC:
        raise HTTPException(501, "Show in Finder needs a Mac.")
    if req.file:
        subprocess.Popen(["open", "-R", str(export_file(req.file))])
    else:
        out_dir().mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["open", str(out_dir())])
    return {"ok": True}


@app.post("/api/quicklook")
def quicklook(req: FileAction):
    if not IS_MAC:
        raise HTTPException(501, "Quick Look needs a Mac.")
    subprocess.Popen(["qlmanage", "-p", str(export_file(req.file or ""))],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True}


# ---------- license endpoints ----------

class KeyRequest(BaseModel):
    key: str


@app.post("/api/license/activate")
def activate(req: KeyRequest):
    lm = license_manager()
    if lm is None:
        raise HTTPException(503, "Licensing isn't set up in this build yet.")
    r = lm.activate(req.key)
    if not r.ok:
        raise HTTPException(400, r.message)
    return {"message": r.message, "license": license_state()}


@app.post("/api/license/deactivate")
def deactivate():
    lm = license_manager()
    if lm is None:
        raise HTTPException(503, "Licensing isn't set up in this build yet.")
    return {"message": lm.deactivate().message, "license": license_state()}


# ---------- custom presets ----------

class CustomPreset(BaseModel):
    id: str | None = None          # set to edit an existing custom preset
    name: str
    width: int | None = None
    height: int | None = None
    format: str = "jpg"
    quality: int | None = 85
    max_kb: int | None = None


@app.post("/api/custom-presets")
def save_custom_preset(req: CustomPreset):
    need_pro("Custom presets")
    current = custom_presets()
    if req.id and req.id not in current:
        raise HTTPException(404, "That custom preset no longer exists.")
    pid = req.id or presets_mod.new_custom_id(req.name, set(current) | set(BUILTIN))
    raw = {"id": pid, "group": "My presets", "name": req.name.strip()[:60], "width": req.width,
           "height": req.height if req.width else None, "format": req.format,
           "quality": None if req.format == "png" else (req.quality or 85),
           "max_bytes": req.max_kb * 1000 if req.max_kb else None, "basis": "Your preset",
           "slug": slugify(req.name, 40)}
    try:
        p = presets_mod.make_preset(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    current[pid] = p
    presets_mod.save_custom(home() / "custom-presets.json", current)
    return p.to_dict()


@app.delete("/api/custom-presets/{pid}")
def delete_custom_preset(pid: str):
    current = custom_presets()
    if pid not in current:
        raise HTTPException(404, "That custom preset no longer exists.")
    del current[pid]
    presets_mod.save_custom(home() / "custom-presets.json", current)
    return {"ok": True}


@app.post("/api/presets/update")
def update_presets():
    status = presets_mod.fetch_update(PRESETS_URL, cache_path())
    if status == "updated":
        reload_builtin()
    return {"status": status, "presets_version": state()["presets_version"]}


# ---------- media ----------

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    kind = "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else None
    if not kind:
        raise HTTPException(400, f"Aptus doesn't open {ext or 'that'} files.")
    mid = uuid.uuid4().hex[:12]
    path = WORK / f"{mid}{ext}"
    with path.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    MEDIA[mid] = {"path": path, "kind": kind, "stem": safe_stem(file.filename or "file")}
    if kind == "image":
        try:
            with Image.open(path) as im:
                w, h = im.size
                if im.getexif().get(0x0112) in (5, 6, 7, 8):   # rotated 90 degrees on display
                    w, h = h, w
        except Exception:
            raise HTTPException(400, "That image couldn't be read.")
        return {"id": mid, "kind": kind, "width": w, "height": h}
    try:
        info = probe(path)
    except Exception:
        raise HTTPException(400, "That video couldn't be read.")
    return {"id": mid, "kind": kind, "width": info.width, "height": info.height,
            "duration": info.duration, "fps": info.fps}


@app.get("/api/preview/{mid}")
def preview(mid: str, t: float = 0.0):
    m = media(mid)
    if m["kind"] == "video":
        return jpeg_response(grab_frame(m["path"], t, max_width=1400))
    with Image.open(m["path"]) as im:
        return jpeg_response(ImageOps.exif_transpose(im))


def source_image(m: dict, t: float | None) -> tuple[Image.Image, str]:
    if m["kind"] == "video":
        return grab_frame(m["path"], t or 0.0), f"{(t or 0):.2f}s"
    src = Image.open(m["path"])
    src.load()
    return src, ""


def check_box(box, name):
    if box is not None and (len(box) != 4 or not all(0 <= v <= 1 for v in box) or box[2] <= 0 or box[3] <= 0):
        raise HTTPException(400, f"The crop for {name} is outside the image.")


class ExportRequest(BaseModel):
    id: str
    presets: list[str]
    focal: tuple[float, float] = (0.5, 0.5)
    t: float | None = None   # frame time, for videos
    boxes: dict[str, list[float]] = {}   # hand-placed crops: preset id -> [x, y, w, h] as fractions
    naming: dict | None = None           # SEO name fields: location, descriptor, client, shot, numbered
    number: int | None = None            # the photo's number in a batch (01, 02, ...)
    batch: bool = False                  # part of a multi-file batch


@app.post("/api/export")
def export(req: ExportRequest):
    m = media(req.id)
    known = all_presets()
    unknown = [p for p in req.presets if p not in known]
    if unknown or not req.presets:
        raise HTTPException(400, f"Unknown or missing presets: {unknown}")
    if len(req.presets) > 1:
        need_pro("Exporting to several destinations at once")
    if req.batch:
        need_pro("Batch exports")
    if any(known[p].custom for p in req.presets):
        need_pro("Custom presets")
    for pid in req.presets:
        check_box(req.boxes.get(pid), known[pid].name)
    naming = Naming(**{k: v for k, v in (req.naming or {}).items()
                       if k in ("location", "descriptor", "client", "shot", "numbered")})
    number = req.number if req.number is not None else 1
    src, extra = source_image(m, req.t)
    folder = out_dir()
    folder.mkdir(parents=True, exist_ok=True)
    results = []

    def save(p, r, tag):
        path = unique_path(folder / file_name(m["stem"], p.slug, p.format, naming, number, tag))
        path.write_bytes(r.data)
        results.append({"preset": p.name, "preset_id": p.id, "file": path.name, "width": r.width,
                        "height": r.height, "bytes": r.bytes, "quality": r.quality,
                        "target_met": r.size_target_met,
                        "warnings": r.warnings + limit_warnings(r.bytes, LIMITS)})

    for pid in req.presets:
        p = known[pid]
        if p.is_set:
            for w, r in export_set(src, p):
                save(p, r, "-".join(x for x in (extra, f"{w}w") if x))
        else:
            save(p, export_image(src, p, focal=req.focal, box=req.boxes.get(pid)), extra)
    s = read_settings()
    s["last_presets"] = req.presets
    if naming.active:
        s["naming"] = {"location": naming.location, "descriptor": naming.descriptor,
                       "client": naming.client, "shot": naming.shot, "numbered": naming.numbered}
    write_settings(s)
    return {"folder": str(folder), "results": results, "alt": naming.alt_text() if naming.active else ""}


class ReferenceRequest(BaseModel):
    id: str
    preset: str
    focal: tuple[float, float] = (0.5, 0.5)
    t: float | None = None
    box: list[float] | None = None
    width: int | None = None   # for size sets: which width


@app.post("/api/reference")
def reference(req: ReferenceRequest):
    """The same crop and size as an export, saved losslessly, for the before/after check at 100%."""
    m = media(req.id)
    known = all_presets()
    if req.preset not in known:
        raise HTTPException(400, "Unknown preset.")
    check_box(req.box, known[req.preset].name)
    p = known[req.preset]
    if p.is_set:
        p = replace(p, width=req.width or p.widths[-1], widths=None)
    src, _ = source_image(m, req.t)
    work, _ = render_image(src, p, focal=req.focal, box=req.box)
    if p.format == "jpg":   # match the export's white background behind any transparency
        work = images_mod._prepare_mode(work, "jpg")
    buf = io.BytesIO()
    work.save(buf, "PNG", compress_level=1)   # lossless and quick; this file is only for looking
    return Response(buf.getvalue(), media_type="image/png")


class SharpRequest(BaseModel):
    id: str
    t: float
    window: float = 1.0


@app.post("/api/sharp")
def sharp(req: SharpRequest):
    m = media(req.id)
    if m["kind"] != "video":
        raise HTTPException(400, "Sharp-frame suggestions need a video.")
    return sharp_frames(m["path"], req.t, window=req.window)


class SheetRequest(BaseModel):
    id: str
    count: int = 12
    columns: int = 4


@app.post("/api/contact-sheet")
def sheet(req: SheetRequest):
    need_pro("Contact sheets")
    m = media(req.id)
    if m["kind"] != "video":
        raise HTTPException(400, "Contact sheets need a video.")
    folder = out_dir()
    folder.mkdir(parents=True, exist_ok=True)
    img = contact_sheet(m["path"], count=req.count, columns=req.columns)
    path = unique_path(folder / file_name(m["stem"], "contact-sheet", "jpg"))
    img.save(path, "JPEG", quality=90)
    return {"folder": str(folder), "file": path.name, "width": img.width, "height": img.height}


class IntervalRequest(BaseModel):
    id: str
    every: float = 2.0


@app.post("/api/interval")
def interval(req: IntervalRequest):
    need_pro("Interval grabs")
    m = media(req.id)
    if m["kind"] != "video":
        raise HTTPException(400, "Interval grabs need a video.")
    if req.every <= 0:
        raise HTTPException(400, "Interval must be more than 0 seconds.")
    folder = out_dir()
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for t, img in interval_frames(m["path"], req.every):
        path = unique_path(folder / file_name(m["stem"], "frame", "png", extra=f"{t:.2f}s"))
        img.save(path, "PNG", optimize=True)
        files.append(path.name)
    return {"folder": str(folder), "files": files}


def serve(port: int = 8765) -> None:
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    print(f"Aptus is running at http://127.0.0.1:8765  (exports go to {out_dir()})")
    serve()
