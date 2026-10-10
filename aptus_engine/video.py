"""Frame grabbing with ffmpeg: single frames, sharp-frame suggestions, interval grabs, and contact sheets."""
from __future__ import annotations

import io
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


def _bin(name: str) -> str:
    """ffmpeg/ffprobe: the copy bundled inside the Mac app first, then APTUS_FFMPEG_DIR, then PATH."""
    import os
    import sys
    for folder in (getattr(sys, "_MEIPASS", None) and Path(sys._MEIPASS) / "ffmpeg",
                   os.environ.get("APTUS_FFMPEG_DIR")):
        if folder and (Path(folder) / name).is_file():
            return str(Path(folder) / name)
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} not found. Install it with: brew install ffmpeg")
    return path


@dataclass
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float


def probe(path: str | Path) -> VideoInfo:
    out = subprocess.run(
        [_bin("ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,avg_frame_rate,duration:stream_tags=rotate"
         ":stream_side_data=rotation:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(out.stdout)
    if not info.get("streams"):
        raise ValueError("No video stream found.")
    s = info["streams"][0]
    num, _, den = s.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    duration = float(s.get("duration") or info.get("format", {}).get("duration") or 0)
    w, h = int(s["width"]), int(s["height"])
    if rotation(s) % 180 == 90:   # phone videos shot upright: ffmpeg turns the frames, so the shape swaps
        w, h = h, w
    return VideoInfo(duration, w, h, fps)


def rotation(stream: dict) -> int:
    """Display rotation in degrees (0, 90, 180, 270) from the stream's side data or its older rotate tag."""
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                return round(float(sd["rotation"])) % 360
            except (TypeError, ValueError):
                pass
    try:
        return round(float((stream.get("tags") or {}).get("rotate", 0))) % 360
    except (TypeError, ValueError):
        return 0


def grab_frame(path: str | Path, t: float, max_width: int | None = None) -> Image.Image:
    """Full-resolution frame at time t (seconds). max_width scales it down, for previews."""
    vf = ["-vf", f"scale='min({max_width},iw)':-2"] if max_width else []
    out = subprocess.run(
        [_bin("ffmpeg"), "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", str(path),
         "-frames:v", "1", *vf, "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True, check=True,
    )
    if not out.stdout:
        raise ValueError(f"No frame at {t:.2f}s.")
    return Image.open(io.BytesIO(out.stdout)).convert("RGB")


def sharpness(img: Image.Image) -> float:
    """Variance of the Laplacian on a grayscale copy: higher means sharper."""
    g = np.asarray(img.convert("L").resize((480, round(480 * img.height / img.width))), dtype=np.float32)
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def _frames_between(path: str | Path, start: float, end: float, rate: float) -> list[tuple[float, Image.Image]]:
    """Small frames sampled at `rate` per second between start and end."""
    out = subprocess.run(
        [_bin("ffmpeg"), "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(path),
         "-vf", f"fps={rate},scale=480:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True, check=True,
    )
    frames, data, pos = [], out.stdout, 0
    sig = b"\x89PNG\r\n\x1a\n"
    starts = []
    while True:
        i = data.find(sig, pos)
        if i < 0:
            break
        starts.append(i)
        pos = i + 1
    for n, s in enumerate(starts):
        chunk = data[s: starts[n + 1] if n + 1 < len(starts) else len(data)]
        frames.append((start + n / rate, Image.open(io.BytesIO(chunk)).convert("RGB")))
    return frames


def sharp_frames(path: str | Path, t: float, window: float = 1.0, count: int = 3) -> list[dict]:
    """The sharpest frames within +/- window seconds of t, best first."""
    info = probe(path)
    start, end = max(0.0, t - window), min(info.duration, t + window)
    rate = min(info.fps or 30, 30)
    scored = [{"t": round(ft, 3), "score": round(sharpness(img), 1)}
              for ft, img in _frames_between(path, start, end, rate)]
    scored.sort(key=lambda d: d["score"], reverse=True)
    picks: list[dict] = []
    for d in scored:   # keep picks at least 0.2 s apart so they aren't near-duplicates
        if all(abs(d["t"] - p["t"]) >= 0.2 for p in picks):
            picks.append(d)
        if len(picks) == count:
            break
    return picks


def interval_frames(path: str | Path, every: float) -> list[tuple[float, Image.Image]]:
    """Full-resolution frames every `every` seconds."""
    info = probe(path)
    times = [i * every for i in range(int(info.duration // every) + 1) if i * every < info.duration]
    return [(t, grab_frame(path, t)) for t in times]


def contact_sheet(path: str | Path, count: int = 12, columns: int = 4, tile_width: int = 480,
                  gap: int = 12, background: str = "#ffffff") -> Image.Image:
    """A grid of `count` frames spread evenly across the video."""
    info = probe(path)
    times = [info.duration * (i + 0.5) / count for i in range(count)]
    tiles = [grab_frame(path, t, max_width=tile_width) for t in times]
    tw = tile_width
    th = round(tw * tiles[0].height / tiles[0].width)   # from a real frame, so rotated video keeps its shape
    rows = math.ceil(count / columns)
    sheet = Image.new("RGB", (columns * tw + (columns + 1) * gap, rows * th + (rows + 1) * gap), background)
    for i, tile in enumerate(tiles):
        r, c = divmod(i, columns)
        sheet.paste(tile.resize((tw, th)), (gap + c * (tw + gap), gap + r * (th + gap)))
    return sheet
