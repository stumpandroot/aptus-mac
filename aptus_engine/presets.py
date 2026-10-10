"""Destination presets: the built-in list, the person's own presets, and online updates.

The preset list is plain data so it can be updated without a new app release,
and reused unchanged by a future native app.
"""
from __future__ import annotations

import json
import re
import tempfile
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "presets.json"
FORMATS = {"jpg", "png", "webp", "avif"}
CUSTOM_PREFIX = "my-"
FIELDS = {"id", "group", "name", "width", "height", "format", "quality", "max_bytes", "basis", "slug", "widths"}


@dataclass(frozen=True)
class Preset:
    id: str
    group: str
    name: str
    width: int | None       # None = keep source width
    height: int | None      # None = keep aspect ratio (no crop)
    format: str             # jpg | png | webp | avif
    quality: int | None     # starting quality for lossy formats
    max_bytes: int | None   # file-size target per file; None = no target
    basis: str = ""
    slug: str = ""          # short tag added to file names, e.g. "hero" or "og-image"
    widths: tuple[int, ...] | None = None   # a size set: one file per width (srcset)

    @property
    def crops(self) -> bool:
        """True when the preset forces an exact width and height."""
        return self.width is not None and self.height is not None

    @property
    def is_set(self) -> bool:
        return bool(self.widths)

    @property
    def custom(self) -> bool:
        return self.id.startswith(CUSTOM_PREFIX)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["widths"] = list(self.widths) if self.widths else None
        d["crops"], d["custom"] = self.crops, self.custom
        return d


@dataclass(frozen=True)
class Limit:
    id: str
    name: str
    max_bytes: int


def make_preset(raw: dict) -> Preset:
    """Build and check one preset from a dict. Raises ValueError with a plain-English reason."""
    unknown = set(raw) - FIELDS
    if unknown:
        raise ValueError(f"Unknown preset fields: {', '.join(sorted(unknown))}")
    raw = dict(raw)
    if raw.get("widths"):
        raw["widths"] = tuple(sorted(int(w) for w in raw["widths"]))
    try:
        p = Preset(**raw)
    except TypeError as e:
        raise ValueError(f"Preset is missing a field: {e}") from None
    name = p.name or p.id
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", p.id or ""):
        raise ValueError(f"{name}: the id must be lowercase letters, numbers and hyphens.")
    if not p.name.strip():
        raise ValueError("Give the preset a name.")
    if p.format not in FORMATS:
        raise ValueError(f"{name}: unknown format {p.format!r}. Use jpg, png, webp or avif.")
    if p.height is not None and p.width is None:
        raise ValueError(f"{name}: a height needs a width too.")
    for label, v in (("width", p.width), ("height", p.height)):
        if v is not None and not (16 <= v <= 12000):
            raise ValueError(f"{name}: {label} must be between 16 and 12000 pixels.")
    if p.quality is not None and not (30 <= p.quality <= 100):
        raise ValueError(f"{name}: quality must be between 30 and 100.")
    if p.format != "png" and p.quality is None:
        raise ValueError(f"{name}: {p.format} needs a quality.")
    if p.max_bytes is not None and p.max_bytes < 10_000:
        raise ValueError(f"{name}: the size target must be at least 10 KB.")
    if p.widths and (p.height is not None or any(not 16 <= w <= 12000 for w in p.widths)):
        raise ValueError(f"{name}: a size set keeps the shape, so it takes widths only (16-12000 px).")
    return p


def _read(path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    if not isinstance(data.get("presets"), list):
        raise ValueError("No presets list in the file.")
    return data


def load_presets(path: str | Path | None = None) -> dict[str, Preset]:
    presets: dict[str, Preset] = {}
    for raw in _read(Path(path or DEFAULT_PATH))["presets"]:
        p = make_preset(raw)
        if p.id in presets:
            raise ValueError(f"Duplicate preset id {p.id!r}")
        if p.custom:
            raise ValueError(f"Built-in preset ids can't start with {CUSTOM_PREFIX!r}")
        presets[p.id] = p
    return presets


def load_limits(path: str | Path | None = None) -> list[Limit]:
    return [Limit(**raw) for raw in _read(Path(path or DEFAULT_PATH)).get("limits", [])]


def limit_warnings(nbytes: int, limits: list[Limit]) -> list[str]:
    """Plain warnings for upload limits a file breaks, such as Webflow's 4 MB image limit."""
    return [f"Too big for {lim.name} ({lim.max_bytes / 1_000_000:g} MB limit)."
            for lim in limits if nbytes > lim.max_bytes]


def file_version(path: str | Path) -> int:
    try:
        return int(_read(Path(path)).get("version", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        return -1


# ---------- the person's own presets ----------

def load_custom(path: Path) -> dict[str, Preset]:
    """Custom presets saved on this Mac. A missing or damaged file means none."""
    try:
        raws = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, Preset] = {}
    for raw in raws if isinstance(raws, list) else []:
        try:
            p = make_preset(raw)
        except ValueError:
            continue
        if p.custom:
            out[p.id] = p
    return out


def save_custom(path: Path, presets: dict[str, Preset]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in presets.values():
        d = asdict(p)
        d["widths"] = list(p.widths) if p.widths else None
        rows.append(d)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=2))
    tmp.replace(path)


def new_custom_id(name: str, taken) -> str:
    base = CUSTOM_PREFIX + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "preset")
    pid, n = base, 2
    while pid in taken:
        pid, n = f"{base}-{n}", n + 1
    return pid


# ---------- online updates to the built-in list ----------

def active_presets_path(bundled: Path = DEFAULT_PATH, cache: Path | None = None) -> Path:
    """Use a downloaded list only when it is newer than the one inside the app and loads cleanly."""
    if cache and Path(cache).exists() and file_version(cache) > file_version(bundled):
        try:
            load_presets(cache)
            return Path(cache)
        except (ValueError, OSError, json.JSONDecodeError):
            pass
    return Path(bundled)


def fetch_update(url: str, cache: Path, bundled: Path = DEFAULT_PATH, timeout: float = 6) -> str:
    """Download the latest preset list. Keeps the current list on any problem.

    Returns "updated", "current", or a short reason it was skipped.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read(2_000_000)
    except Exception as e:   # offline, DNS, HTTP error: never block the app
        return f"skipped: {type(e).__name__}"
    with tempfile.TemporaryDirectory() as tmp:
        trial = Path(tmp) / "presets.json"
        trial.write_bytes(body)
        try:
            load_presets(trial)
            load_limits(trial)
        except Exception:
            return "skipped: the downloaded list didn't pass checks"
        current = active_presets_path(bundled, cache)
        if file_version(trial) <= file_version(current):
            return "current"
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        Path(cache).write_bytes(body)
    return "updated"
