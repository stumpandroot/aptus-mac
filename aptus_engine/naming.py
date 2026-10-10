"""File names and alt text that follow the Stump & Root SEO image naming guide.

Formula: [location]-[descriptor]-[client-or-industry]-[shot-type]-[number].ext
Lowercase, hyphens only, two-digit numbers. Aptus adds the destination's short tag
(for example "hero" or "og-image") at the end so one photo's exports don't collide.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

ALT_MAX = 125


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = text.replace("&", " and ").replace("'", "")
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-")


@dataclass
class Naming:
    """Fields from the naming panel. All optional; empty ones are skipped."""
    location: str = ""
    descriptor: str = ""
    client: str = ""
    shot: str = ""
    numbered: bool = True

    @property
    def active(self) -> bool:
        return any(slugify(v) for v in (self.location, self.descriptor, self.client, self.shot))

    def base(self, number: int | None) -> str:
        parts = [slugify(v, 40) for v in (self.location, self.descriptor, self.client, self.shot)]
        parts = [p for p in parts if p]
        if self.numbered and number is not None:
            parts.append(f"{number:02d}")
        return "-".join(parts)

    def alt_text(self) -> str:
        """Subject + context + location/brand, under 125 characters. A starting point to edit."""
        subject = " ".join(v.strip() for v in (self.shot, self.descriptor) if v.strip())
        subject = subject[:1].upper() + subject[1:] if subject else ""
        tail = []
        if self.client.strip():
            tail.append(f"for {self.client.strip()}")
        if self.location.strip():
            tail.append(f"in {self.location.strip()}")
        alt = " ".join([subject] + tail).strip() if subject else " ".join(tail).strip()
        if len(alt) > ALT_MAX:   # cut at a word break; the page does the same
            alt = re.sub(r"\s+\S*$", "", alt[:ALT_MAX])
        return alt


def file_name(stem: str, preset_slug: str, ext: str, naming: Naming | None = None,
              number: int | None = None, extra: str = "") -> str:
    """Build one export file name.

    stem: the source file name (used when no naming fields are filled in).
    extra: a further tag such as a frame time ("2-00s") or a width ("1600w").
    """
    if naming and naming.active:
        base = naming.base(number)
    else:
        base = slugify(stem) or "image"
    parts = [base] + [p for p in (slugify(extra), slugify(preset_slug)) if p]
    return "-".join(parts) + "." + ext
