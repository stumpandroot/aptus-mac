# PyInstaller recipe for Aptus.app. Build from the project folder:
#   pyinstaller --noconfirm mac/Aptus.spec
# Expects ffmpeg and ffprobe in mac/vendor/ffmpeg/ (mac/build_ffmpeg.sh makes them).
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
VERSION = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else "1.0.0"
FFMPEG = ROOT / "mac" / "vendor" / "ffmpeg"
ICON = ROOT / "mac" / "Aptus.icns"

for tool in ("ffmpeg", "ffprobe"):
    if not (FFMPEG / tool).is_file():
        raise SystemExit(f"Missing {FFMPEG / tool}. Run mac/build_ffmpeg.sh first.")

datas = [
    (str(ROOT / "static"), "static"),
    (str(ROOT / "presets.json"), "."),
]
if (ROOT / "VERSION").exists():
    datas.append((str(ROOT / "VERSION"), "."))

a = Analysis(
    [str(ROOT / "mac" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[(str(FFMPEG / "ffmpeg"), "ffmpeg"), (str(FFMPEG / "ffprobe"), "ffmpeg")],
    datas=datas,
    hiddenimports=(
        ["app", "greenlaunch_license", "pillow_heif", "certifi", "webview.platforms.cocoa"]
        + collect_submodules("uvicorn")
        + collect_submodules("aptus_engine")
    ),
    excludes=["tkinter", "pytest", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Aptus",
    console=False,
    codesign_identity=None,       # signing happens in mac/sign_and_package.sh
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Aptus")
app = BUNDLE(
    coll,
    name="Aptus.app",
    icon=str(ICON) if ICON.exists() else None,
    bundle_identifier="co.greenlaunch.aptus",
    version=VERSION,
    info_plist={
        "CFBundleName": "Aptus",
        "CFBundleDisplayName": "Aptus",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        "LSApplicationCategoryType": "public.app-category.photography",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "© GreenLaunch",
        # The folder picker uses AppleScript's "choose folder", which needs this to show its prompt.
        "NSAppleEventsUsageDescription": "Aptus uses this to show the folder picker for your exports.",
    },
)
