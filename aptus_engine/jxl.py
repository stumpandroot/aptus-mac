"""JPEG XL through libjxl's cjxl tool.

Aptus calls cjxl (BSD-3-Clause) instead of the Python JPEG XL plugin, which is GPL-3.0 and so can't ship in a
paid, closed-source app. The Mac app bundles cjxl (mac/build_jxl.sh). Without it, JPEG XL is unavailable and
the interface says so.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image


def cjxl_path() -> str | None:
    """The copy bundled inside the Mac app first, then APTUS_JXL_DIR, then PATH."""
    for folder in (getattr(sys, "_MEIPASS", None) and Path(sys._MEIPASS) / "jxl", os.environ.get("APTUS_JXL_DIR")):
        if folder and (Path(folder) / "cjxl").is_file():
            return str(Path(folder) / "cjxl")
    return shutil.which("cjxl")


def available() -> bool:
    return cjxl_path() is not None


def _run(src: Path, args: list[str]) -> bytes:
    tool = cjxl_path()
    if not tool:
        raise RuntimeError("JPEG XL needs the cjxl encoder, which isn't in this build.")
    with tempfile.TemporaryDirectory(prefix="aptus-jxl-") as tmp:
        out = Path(tmp) / "out.jxl"
        proc = subprocess.run([tool, str(src), str(out), "--quiet", *args], capture_output=True, text=True, timeout=600)
        if proc.returncode != 0 or not out.is_file():
            msg = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise RuntimeError("JPEG XL encoding failed" + (f": {msg[-1]}" if msg else "."))
        return out.read_bytes()


def encode(img: Image.Image, quality: int | None, effort: int, exif: bytes | None = None,
           icc: bytes | None = None) -> bytes:
    """Encode pixels. quality None means mathematically lossless. exif and icc carry over into the file."""
    if img.mode not in ("RGB", "RGBA", "L", "LA"):
        img = img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB")
    with tempfile.TemporaryDirectory(prefix="aptus-jxl-in-") as tmp:
        src = Path(tmp) / "in.png"
        kw = {"compress_level": 1}
        if exif:
            kw["exif"] = exif
        if icc:
            kw["icc_profile"] = icc
        img.save(src, "PNG", **kw)
        args = ["-e", str(effort)] + (["-d", "0"] if quality is None else ["-q", str(quality)])
        return _run(src, args)


def transcode_jpeg(path: str | Path, effort: int = 7) -> bytes:
    """Repack a JPEG losslessly: same pixels and metadata, smaller file, reversible to the exact original."""
    return _run(Path(path), ["-e", str(effort), "--lossless_jpeg=1"])


def lossless_file(path: str | Path, effort: int = 7) -> bytes:
    """A lossless JPEG XL straight from a PNG, keeping its metadata."""
    return _run(Path(path), ["-e", str(effort), "-d", "0"])
