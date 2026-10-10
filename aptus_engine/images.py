"""Resize, crop, convert, and compress one image for one destination preset."""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image, ImageCms, ImageOps

from .presets import Preset

PIL_FORMAT = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "avif": "AVIF"}
MIN_QUALITY = 30


@dataclass
class ExportResult:
    data: bytes
    width: int
    height: int
    format: str
    quality: int | None
    size_target_met: bool | None   # None when the preset has no target
    warnings: list[str] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return len(self.data)


def to_srgb(img: Image.Image) -> Image.Image:
    """Convert an image with an embedded color profile to sRGB, so browsers show true colors."""
    icc = img.info.get("icc_profile")
    if not icc:
        return img
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        dst = ImageCms.createProfile("sRGB")
        mode = "RGBA" if img.mode in ("RGBA", "LA", "PA") else "RGB"
        out = ImageCms.profileToProfile(img.convert(mode), src, dst, outputMode=mode)
        out.info.pop("icc_profile", None)
        return out
    except Exception:
        # A broken profile shouldn't block an export; fall back to the raw pixels.
        return img


def focal_crop_box(w: int, h: int, aspect: float, focal: tuple[float, float]) -> tuple[int, int, int, int]:
    """Largest box with the target aspect ratio, centered on the focal point and kept inside the image.

    focal is (x, y) as fractions of width and height, 0.0-1.0.
    """
    fx = min(max(focal[0], 0.0), 1.0)
    fy = min(max(focal[1], 0.0), 1.0)
    if w / h > aspect:
        cw, ch = round(h * aspect), h
    else:
        cw, ch = w, round(w / aspect)
    cw, ch = max(1, min(cw, w)), max(1, min(ch, h))
    left = round(fx * w - cw / 2)
    top = round(fy * h - ch / 2)
    left = min(max(left, 0), w - cw)
    top = min(max(top, 0), h - ch)
    return left, top, left + cw, top + ch


def box_from_fractions(w: int, h: int, aspect: float, frac) -> tuple[int, int, int, int]:
    """A crop box the person placed by hand, given as (x, y, width, height) fractions of the image.

    The box is snapped to the preset's exact shape around its own center, then kept inside the image,
    so a slightly-off box from the interface can never distort the export.
    """
    fx, fy, fw, fh = (float(v) for v in frac)
    cx, cy = (fx + fw / 2) * w, (fy + fh / 2) * h
    cw = max(1, round(fw * w))
    ch = max(1, round(cw / aspect))
    if ch > h:
        ch, cw = h, max(1, round(h * aspect))
    if cw > w:
        cw, ch = w, max(1, round(w / aspect))
    left = min(max(round(cx - cw / 2), 0), w - cw)
    top = min(max(round(cy - ch / 2), 0), h - ch)
    return left, top, left + cw, top + ch


def _prepare_mode(img: Image.Image, fmt: str) -> Image.Image:
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    if fmt == "jpg":
        if has_alpha:
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.getchannel("A"))
            return bg
        return img.convert("RGB")
    return img.convert("RGBA" if has_alpha else "RGB")


def _encode(img: Image.Image, fmt: str, quality: int | None) -> bytes:
    buf = io.BytesIO()
    kwargs: dict = {}
    if fmt == "jpg":
        kwargs = {"quality": quality, "optimize": True, "progressive": True}
    elif fmt == "webp":
        kwargs = {"quality": quality, "method": 6}
    elif fmt == "avif":
        kwargs = {"quality": quality}
    elif fmt == "png":
        kwargs = {"optimize": True}
    img.save(buf, PIL_FORMAT[fmt], **kwargs)
    return buf.getvalue()


def _encode_to_target(img: Image.Image, preset: Preset) -> tuple[bytes, int | None, bool | None]:
    """Encode at the preset quality; if over the size target, search for the highest quality that fits."""
    fmt, target = preset.format, preset.max_bytes
    if fmt == "png" or preset.quality is None:
        data = _encode(img, fmt, None)
        return data, None, (None if target is None else len(data) <= target)

    data = _encode(img, fmt, preset.quality)
    if target is None or len(data) <= target:
        return data, preset.quality, (None if target is None else True)

    lo, hi = MIN_QUALITY, preset.quality - 1
    best: tuple[bytes, int] | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        trial = _encode(img, fmt, mid)
        if len(trial) <= target:
            best = (trial, mid)
            lo = mid + 1
        else:
            hi = mid - 1
    if best:
        return best[0], best[1], True
    floor = _encode(img, fmt, MIN_QUALITY)
    return floor, MIN_QUALITY, False


def render_image(
    img: Image.Image,
    preset: Preset,
    focal: tuple[float, float] = (0.5, 0.5),
    box=None,
) -> tuple[Image.Image, list[str]]:
    """Crop and resize for a preset, before any compression. Returns the pixels and any warnings."""
    warnings: list[str] = []
    work = ImageOps.exif_transpose(img)        # respect camera rotation
    work = to_srgb(work)
    w, h = work.size

    if preset.crops:
        tw, th = preset.width, preset.height
        region = box_from_fractions(w, h, tw / th, box) if box else focal_crop_box(w, h, tw / th, focal)
        work = work.crop(region)
        if work.width < tw or work.height < th:
            warnings.append(
                f"Source area is {work.width}×{work.height}, smaller than {tw}×{th}; "
                "the image is enlarged and may look soft."
            )
        work = work.resize((tw, th), Image.Resampling.LANCZOS)
    elif preset.width is not None:
        if w > preset.width:
            nh = round(h * preset.width / w)
            work = work.resize((preset.width, nh), Image.Resampling.LANCZOS)
        else:
            warnings.append(f"Source is {w}px wide, under {preset.width}px; kept at its original width.")
    return work, warnings


def export_image(
    img: Image.Image,
    preset: Preset,
    focal: tuple[float, float] = (0.5, 0.5),
    box=None,
) -> ExportResult:
    """Produce one file for one preset. The source image is never modified.

    box: an optional hand-placed crop as (x, y, width, height) fractions. It overrides the focal point
    for presets with an exact size and is ignored for width-only presets.
    """
    work, warnings = render_image(img, preset, focal, box)
    work = _prepare_mode(work, preset.format)
    data, quality, met = _encode_to_target(work, preset)
    if met is False:
        warnings.append(
            f"Couldn't reach the {preset.max_bytes / 1_000_000:g} MB target; file is "
            f"{len(data) / 1_000_000:.2f} MB at the lowest allowed quality."
        )
    return ExportResult(data, work.width, work.height, preset.format, quality, met, warnings)


def export_set(img: Image.Image, preset: Preset) -> list[tuple[int, ExportResult]]:
    """A size set: one file per width, for a responsive srcset. Widths wider than the source are
    skipped (never upscaled); if every width is wider, one file at the source width is made."""
    from dataclasses import replace

    src_w = ImageOps.exif_transpose(img).width
    widths = [w for w in preset.widths if w <= src_w] or [src_w]
    skipped = [w for w in preset.widths if w > src_w]
    out = []
    for w in widths:
        r = export_image(img, replace(preset, width=w, widths=None))
        r.warnings = [x for x in r.warnings if "kept at its original width" not in x]
        out.append((w, r))
    if skipped:
        out[-1][1].warnings.append(
            f"Source is {src_w}px wide, so {', '.join(f'{w}px' for w in skipped)} "
            f"{'was' if len(skipped) == 1 else 'were'} skipped rather than enlarged.")
    return out
