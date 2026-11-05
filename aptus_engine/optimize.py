"""Optimize: convert and compress whole batches for a destination, without cropping.

Resize frames a photo for one slot. Optimize makes files ready for a place: right dimensions, format and
file size, with the aspect ratio always kept. Destinations live under the "optimize" key of presets.json,
separate from the Resize presets, so builds that predate Optimize still accept updated preset lists.
"""
from __future__ import annotations

import io
import json
import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from PIL import Image, ImageCms, ImageFilter, ImageOps

from .images import PIL_FORMAT, _prepare_mode, to_srgb

FORMATS = {"jpg", "png", "webp", "avif"}
FITS = {"long_edge", "width", "box"}
# The lowest quality Optimize will use before it steps the dimensions down instead. Quality 30 (Resize's
# floor) looks muddy; these are starting values to calibrate with SSIMULACRA2 on a real test set.
FLOORS = {"jpg": 65, "webp": 65, "avif": 50}
# Search with fast encoder settings, then make the final file with the slow, smaller ones.
SEARCH = {"webp": {"method": 4}, "avif": {"speed": 8}}
FINAL = {"webp": {"method": 6}, "avif": {"speed": 6}}
SHARPEN = {"off": None, "low": (0.8, 35, 3), "medium": (1.0, 60, 2)}   # UnsharpMask radius, percent, threshold
KEEPABLE = {"copyright", "date"}
SOURCE_FORMAT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "AVIF": "avif"}
FIELDS = {"id", "group", "name", "long_edge", "width", "height", "format", "quality", "max_bytes", "min_edge",
          "allow_downscale", "upload_cap", "ratio_min", "ratio_max", "ratio_note", "platform", "sharpen",
          "keep", "slug", "basis", "source_url", "checked"}

# Tags whose presence means a file can't be copied as-is, because publishing would leak them.
GPS_IFD, EXIF_IFD = 0x8825, 0x8769
SERIAL_TAGS = {0xA431, 0xA435, 0xC62F}   # body serial, lens serial, camera serial
ARTIST, COPYRIGHT, DATETIME, DATETIME_ORIGINAL, ORIENTATION = 0x013B, 0x8298, 0x0132, 0x9003, 0x0112


@dataclass(frozen=True)
class Destination:
    id: str
    group: str
    name: str
    format: str                       # jpg | png | webp | avif
    quality: int | None = None        # starting quality for lossy formats
    max_bytes: int | None = None      # the per-file ceiling; None = no target
    long_edge: int | None = None      # fit the longer side to this
    width: int | None = None          # or fit the width (with height: fit inside a box)
    height: int | None = None
    min_edge: int | None = None       # smallest long edge when stepping down to meet max_bytes
    allow_downscale: bool = True      # step dimensions down when the quality floor can't meet max_bytes
    upload_cap: int | None = None     # the platform's own limit, used as a guard when there's no max_bytes
    ratio_min: float | None = None    # width ÷ height the platform shows in full (tallest)
    ratio_max: float | None = None    # (widest)
    ratio_note: str = ""              # e.g. "3:4 to 1.91:1"
    platform: str = ""                # e.g. "Instagram"
    sharpen: str = "low"              # off | low | medium, applied once after downscaling
    keep: tuple[str, ...] = ()        # metadata to keep: "copyright", "date"
    slug: str = ""
    basis: str = ""
    source_url: str = ""              # where the platform spec came from
    checked: str = ""                 # when it was last checked, YYYY-MM-DD

    @property
    def fit(self) -> str:
        return "long_edge" if self.long_edge else "box" if self.height else "width"

    @property
    def cap(self) -> int | None:
        """The size the file must end up under: the target, or the platform's upload limit as a guard."""
        return self.max_bytes or self.upload_cap

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in FIELDS}
        d["keep"] = list(self.keep)
        d["fit"] = self.fit
        return d


def make_destination(raw: dict) -> Destination:
    """Build and check one destination. Raises ValueError with a plain-English reason."""
    unknown = set(raw) - FIELDS
    if unknown:
        raise ValueError(f"Unknown destination fields: {', '.join(sorted(unknown))}")
    raw = dict(raw)
    raw["keep"] = tuple(raw.get("keep") or ())
    try:
        d = Destination(**raw)
    except TypeError as e:
        raise ValueError(f"Destination is missing a field: {e}") from None
    name = d.name or d.id
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", d.id or ""):
        raise ValueError(f"{name}: the id must be lowercase letters, numbers and hyphens.")
    if d.format not in FORMATS:
        raise ValueError(f"{name}: unknown format {d.format!r}. Use jpg, png, webp or avif.")
    if bool(d.long_edge) == bool(d.width):
        raise ValueError(f"{name}: set either a long edge or a width.")
    if d.height and not d.width:
        raise ValueError(f"{name}: a height needs a width too.")
    for label, v in (("long edge", d.long_edge), ("width", d.width), ("height", d.height), ("minimum edge", d.min_edge)):
        if v is not None and not (16 <= v <= 12000):
            raise ValueError(f"{name}: {label} must be between 16 and 12000 pixels.")
    if d.format != "png" and not (d.quality and 30 <= d.quality <= 100):
        raise ValueError(f"{name}: {d.format} needs a quality between 30 and 100.")
    for label, v in (("max size", d.max_bytes), ("upload limit", d.upload_cap)):
        if v is not None and v < 10_000:
            raise ValueError(f"{name}: the {label} must be at least 10 KB.")
    if d.sharpen not in SHARPEN:
        raise ValueError(f"{name}: sharpen must be off, low or medium.")
    if set(d.keep) - KEEPABLE:
        raise ValueError(f"{name}: keep can list copyright and date.")
    return d


def load_destinations(path: str | Path) -> dict[str, Destination]:
    """The "optimize" list in a presets file. A file without one has no destinations."""
    data = json.loads(Path(path).read_text())
    out: dict[str, Destination] = {}
    for raw in data.get("optimize", []) or []:
        d = make_destination(raw)
        if d.id in out:
            raise ValueError(f"Duplicate destination id {d.id!r}")
        out[d.id] = d
    return out


@dataclass
class Settings:
    """What the person changed in the inspector. None keeps the destination's own value."""
    format: str | None = None
    max_bytes: int | None = None
    no_max: bool = False                   # explicitly no size target
    long_edge: int | None = None
    allow_downscale: bool | None = None
    sharpen: str | None = None
    keep: tuple[str, ...] | None = None
    reduce_colors: bool = False            # PNG only: palette reduction

    def apply(self, d: Destination) -> Destination:
        changes: dict = {}
        if self.format:
            if self.format not in FORMATS:
                raise ValueError(f"Unknown format {self.format!r}.")
            changes["format"] = self.format
            if self.format != "png" and not d.quality:
                changes["quality"] = 85
        if self.no_max:
            changes["max_bytes"] = None
        elif self.max_bytes:
            if self.max_bytes < 10_000:
                raise ValueError("The max size must be at least 10 KB.")
            changes["max_bytes"] = self.max_bytes
        if self.long_edge:
            if not 16 <= self.long_edge <= 12000:
                raise ValueError("The long edge must be between 16 and 12000 pixels.")
            changes.update(long_edge=self.long_edge, width=None, height=None)
            if d.min_edge and d.min_edge > self.long_edge:
                changes["min_edge"] = None
        if self.allow_downscale is not None:
            changes["allow_downscale"] = self.allow_downscale
        if self.sharpen:
            if self.sharpen not in SHARPEN:
                raise ValueError("Sharpen must be off, low or medium.")
            changes["sharpen"] = self.sharpen
        if self.keep is not None:
            if set(self.keep) - KEEPABLE:
                raise ValueError("Keep can list copyright and date.")
            changes["keep"] = tuple(self.keep)
        return replace(d, **changes)


@dataclass
class OptimizeResult:
    data: bytes | None          # None: copy the source file unchanged
    format: str
    width: int
    height: int
    quality: int | None
    nbytes: int
    state: str                  # done | already_fit | resized_to_fit | attention
    notes: list[str] = field(default_factory=list)


# ---------- pieces ----------

def output_size(w: int, h: int, d: Destination, long_edge: int | None = None) -> tuple[int, int]:
    """Dimensions for a source of w×h. Never upscales; keeps the aspect ratio within rounding."""
    if long_edge:
        s = long_edge / max(w, h)
    elif d.long_edge:
        s = d.long_edge / max(w, h)
    elif d.height:
        s = min(d.width / w, d.height / h)
    else:
        s = d.width / w
    s = min(1.0, s)
    return max(1, round(w * s)), max(1, round(h * s))


RATIOS = [(1, 1), (4, 5), (5, 4), (3, 4), (4, 3), (2, 3), (3, 2), (9, 16), (16, 9), (1.91, 1), (2, 1), (3, 1), (1, 2)]


def ratio_label(w: int, h: int) -> str:
    r = w / h
    for a, b in RATIOS:
        if abs(r - a / b) / (a / b) < 0.02:
            return f"{a:g}:{b:g}"
    return f"{r:.2f}:1" if r >= 1 else f"1:{1 / r:.2f}"


def ratio_note(w: int, h: int, d: Destination) -> str | None:
    """A plain warning when the platform will crop this shape, else None."""
    r = w / h
    tall = d.ratio_min is not None and r < d.ratio_min * 0.99
    wide = d.ratio_max is not None and r > d.ratio_max * 1.01
    if not (tall or wide):
        return None
    who = d.platform or d.name
    return (f"{who} shows {d.ratio_note or 'this range'} in full; this photo is {ratio_label(w, h)}, "
            f"so {who} crops it. Frame it in Resize to choose the crop.")


def kept_exif(src: Image.Image, keep) -> bytes | None:
    """Only the metadata the person chose to keep. GPS, serial numbers and everything else are dropped."""
    if not keep:
        return None
    s = src.getexif()
    out = Image.Exif()
    if "copyright" in keep:
        for tag in (ARTIST, COPYRIGHT):
            if s.get(tag):
                out[tag] = s[tag]
    if "date" in keep:
        taken = s.get_ifd(EXIF_IFD).get(DATETIME_ORIGINAL)
        if taken:
            out.get_ifd(EXIF_IFD)[DATETIME_ORIGINAL] = taken
        elif s.get(DATETIME):
            out[DATETIME] = s[DATETIME]
    data = out.tobytes()
    return data if (len(out) or out.get_ifd(EXIF_IFD)) else None


def _is_srgb(icc: bytes | None) -> bool:
    if not icc:
        return True
    try:
        desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc)))
    except Exception:
        return False
    return "srgb" in desc.lower()


def has_private_metadata(src: Image.Image) -> bool:
    exif = src.getexif()
    if exif.get_ifd(GPS_IFD) or GPS_IFD in exif:
        return True
    if SERIAL_TAGS & (set(exif) | set(exif.get_ifd(EXIF_IFD))):
        return True
    xmp = src.info.get("xmp") or src.info.get("XML:com.adobe.xmp") or b""
    if isinstance(xmp, str):
        xmp = xmp.encode("utf-8", "ignore")
    return b"GPS" in xmp


def already_fits(src: Image.Image, nbytes: int, d: Destination) -> bool:
    """True when copying the file unchanged meets every rule: format, size, dimensions and metadata."""
    if SOURCE_FORMAT.get(src.format or "") != d.format or d.cap is None or nbytes > d.cap:
        return False
    if src.getexif().get(ORIENTATION, 1) != 1 or has_private_metadata(src):
        return False
    if not _is_srgb(src.info.get("icc_profile")):
        return False
    return output_size(*src.size, d) == src.size


def _has_alpha(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        lo, _ = img.convert("RGBA").getchannel("A").getextrema()
        return lo < 255
    return False


def _encode(img: Image.Image, fmt: str, quality: int | None, final: bool, exif: bytes | None) -> bytes:
    buf = io.BytesIO()
    kw: dict = {}
    if fmt == "jpg":
        kw = {"quality": quality, "optimize": True, "progressive": True}
    elif fmt in ("webp", "avif"):
        kw = {"quality": quality, **(FINAL if final else SEARCH)[fmt]}
    elif fmt == "png":
        kw = {"optimize": True}
    if exif:
        kw["exif"] = exif
    img.save(buf, PIL_FORMAT[fmt], **kw)
    return buf.getvalue()


def fit_quality(img, fmt, start, floor, cap, exif=None) -> tuple[bytes, int | None, bool, int]:
    """Highest quality between floor and start that fits under cap.

    Returns (data, quality, met, smallest bytes seen). Searches with fast encoder settings, then encodes the
    winner with the final settings and keeps whichever measured file is under the cap. Bytes don't always fall
    in step with quality, so the best measured candidate wins rather than an assumed one.
    """
    if fmt == "png" or start is None:
        data = _encode(img, fmt, None, True, exif)
        return data, None, cap is None or len(data) <= cap, len(data)
    if cap is None:
        data = _encode(img, fmt, start, True, exif)
        return data, start, True, len(data)
    same = fmt == "jpg"   # JPEG has no separate fast mode
    trial = _encode(img, fmt, start, False, exif)
    smallest = len(trial)
    best = (trial, start) if len(trial) <= cap else None
    if best is None:
        lo, hi = floor, start - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            trial = _encode(img, fmt, mid, False, exif)
            smallest = min(smallest, len(trial))
            if len(trial) <= cap:
                best, lo = (trial, mid), mid + 1
            else:
                hi = mid - 1
    if best is None:
        return trial, floor, False, smallest
    data, q = best
    if not same:
        final = _encode(img, fmt, q, True, exif)
        if len(final) <= cap:
            data = final
    return data, q, True, smallest


def _resize(img: Image.Image, size: tuple[int, int], sharpen: str) -> Image.Image:
    if size == img.size:
        return img
    out = img.resize(size, Image.Resampling.LANCZOS)
    params = SHARPEN.get(sharpen)
    if params and size[0] < img.width * 0.95:   # only after a real downscale, and only once
        out = out.filter(ImageFilter.UnsharpMask(*params))
    return out


def prepared(src: Image.Image) -> Image.Image:
    """Camera rotation applied and colors in sRGB: the picture every attempt starts from."""
    return to_srgb(ImageOps.exif_transpose(src))


# ---------- the pipeline ----------

def optimize_image(src: Image.Image, d: Destination, nbytes: int | None = None,
                   reduce_colors: bool = False, base: Image.Image | None = None) -> OptimizeResult:
    """Produce one file for one destination. The source is never modified.

    nbytes: the source file's size, to decide whether it can be copied unchanged.
    base: the prepared picture (see prepared()), when the caller already has it.
    """
    notes: list[str] = []
    work = base if base is not None else prepared(src)
    w0, h0 = work.size
    fmt = d.format
    if fmt == "jpg" and _has_alpha(work):
        fmt = "png"
        notes.append("Has transparency, so it's saved as PNG instead of JPEG.")
    crop_note = ratio_note(w0, h0, d)

    if nbytes is not None and fmt == d.format and not reduce_colors and already_fits(src, nbytes, d):
        state = "attention" if crop_note else "already_fit"
        return OptimizeResult(None, fmt, w0, h0, None, nbytes, state,
                              notes + ([crop_note] if crop_note else ["Already fits, so it's copied unchanged."]))

    exif = kept_exif(src, d.keep)
    cap = d.cap
    floor = FLOORS.get(fmt, 0)
    start = d.quality if fmt != "png" else None
    if start is not None and start < floor:
        floor = start
    tw, th = output_size(w0, h0, d)
    first_long = max(tw, th)
    min_long = min(d.min_edge or round(first_long * 0.6), first_long)

    def render(size):
        img = _resize(work, size, d.sharpen)
        if fmt == "png" and reduce_colors:
            rgba = img.mode == "RGBA"
            img = img.quantize(256, method=Image.Quantize.FASTOCTREE if rgba else Image.Quantize.MEDIANCUT)
            return img
        return _prepare_mode(img, fmt)

    size = (tw, th)
    while True:
        data, q, met, smallest = fit_quality(render(size), fmt, start, floor, cap, exif)
        long = max(size)
        if met or not d.allow_downscale or long <= min_long:
            break
        # Bytes scale roughly with area, so aim for the size that should fit, but step at most 25% at a time:
        # grainy photos shrink faster than their area, and a big jump gives up resolution it didn't need to.
        f = min(0.92, max(0.75, math.sqrt(cap / max(smallest, 1))))
        new_long = max(min_long, round(long * f))
        if new_long >= long:
            break
        size = output_size(w0, h0, d, long_edge=new_long)

    w, h = size
    if not met:
        parts = ([f"{w}×{h}"] if (w, h) != (tw, th) else []) + ([f"quality {q} (the lowest Optimize uses)"] if q else [])
        why = f"Couldn't get under {fmt_bytes(cap)}" + (f", even at {' and '.join(parts)}." if parts else ".")
        fix = "Raise the max size" + (", turn on Allow smaller dimensions," if not d.allow_downscale else "") + \
              (" or pick WebP or JPEG." if fmt == "png" else " or pick a more efficient format.")
        notes.append(f"{why} {fix}")
        state = "attention"
    elif (w, h) != (tw, th):
        notes.append(f"Stepped down from {tw}×{th} to {w}×{h} to stay under {fmt_bytes(cap)}.")
        state = "resized_to_fit"
    else:
        state = "done"
    smaller = (d.long_edge and max(w0, h0) < d.long_edge) or (d.width and not d.long_edge and w0 < d.width
                                                              and not (d.height and h0 >= d.height))
    if (w0, h0) == (tw, th) and smaller:
        notes.append(f"Source is {w0}×{h0}, smaller than the destination, so it's kept at its own size, never enlarged.")
    if crop_note:
        notes.append(crop_note)
        state = "attention"
    return OptimizeResult(data, fmt, w, h, q, len(data), state, notes)


def fmt_bytes(n: int | None) -> str:
    if n is None:
        return "no limit"
    return f"{n / 1_000_000:.2f} MB" if n >= 1_000_000 else f"{round(n / 1000)} KB"
