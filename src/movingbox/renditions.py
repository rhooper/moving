"""Derived images, made from the stored full photo and never from each other.

The full image is the record and is never written here; everything derived can
be thrown away and remade. The list thumbnail is small; the strip (the photo
strip and the sub-item modal) gets its own larger, sharpened image.

A strip URL names one set of bytes forever, since the service worker and a
year-long cache never refetch it:

- the version in the URL is a hash of the recipe, so changing the recipe
  changes every URL;
- every path that makes a strip renders it from the same stored file with the
  same recipe, so it is the same bytes whichever made it;
- a request for any other version is a 404, never the current bytes.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

#: Long edge of the list thumbnail. Its URL is not versioned, so existing
#: thumbnails are never regenerated.
THUMB_MAX = 400

#: Long edge of the strip image: covers the phone's strip figure at 3x.
STRIP_MAX = 800

#: Unsharp mask after the downscale: (radius, percent, threshold), tuned by eye.
#: A larger radius halos text; more percent or less threshold speckles cardboard.
UNSHARP = (0.8, 80, 3)

#: The same quality the stored photos use.
QUALITY = 82

_VERSION_LENGTH = 10


def version(
    *,
    strip_max: int = STRIP_MAX,
    unsharp: tuple[float, int, int] = UNSHARP,
    quality: int = QUALITY,
) -> str:
    """The strip version: a short hash of everything that decides its bytes."""
    recipe = f"strip|{strip_max}|{unsharp}|{quality}|lanczos-then-unsharp"
    return hashlib.sha256(recipe.encode()).hexdigest()[:_VERSION_LENGTH]


VERSION = version()


def fitted(width: int, height: int, longest: int) -> tuple[int, int]:
    """The size `Image.thumbnail((longest, longest))` produces, without an image.

    Mirrors Pillow's rounding exactly, so srcset widths describe the image served.
    """
    if longest >= width and longest >= height:
        return width, height
    aspect = width / height
    x, y = longest, longest

    def rounded(number: float, key) -> int:
        return max(min(math.floor(number), math.ceil(number), key=key), 1)

    if x / y >= aspect:
        x = rounded(y * aspect, key=lambda n: abs(aspect - n / y))
    else:
        y = rounded(x / aspect, key=lambda n: 0 if n == 0 else abs(aspect - x / n))
    return x, y


def strip_name(filename: str, v: str | None = None) -> str:
    """The strip's filename, beside the full image it was made from.

    `v` defaults to `VERSION` looked up at call time, not bound at import.
    """
    return f"{Path(filename).stem}-strip-{v or VERSION}.jpg"


def strip_files(photo_dir: Path, filename: str) -> list[Path]:
    """Every strip on disk for this photo, of any recipe.

    Matched exactly, so a photo whose name starts with this one's is not taken for it.
    """
    stem = Path(filename).stem
    pattern = re.compile(rf"{re.escape(stem)}-strip-[0-9a-f]{{{_VERSION_LENGTH}}}\.jpg")
    return sorted(
        path for path in photo_dir.glob(f"{stem}-strip-*.jpg") if pattern.fullmatch(path.name)
    )


def render_strip(full: Image.Image) -> Image.Image:
    """The strip image, from the full photo.

    Works on a loaded copy: Pillow's JPEG draft mode on an unread file would
    make different bytes from the same photo.
    """
    image = full.convert("RGB")
    image.thumbnail((STRIP_MAX, STRIP_MAX), Image.LANCZOS)
    return image.filter(ImageFilter.UnsharpMask(*UNSHARP))


def write_strip(photo_dir: Path, filename: str) -> bool:
    """Make this photo's strip, from the full image, if it is not there yet.

    Returns True if one was written. Renamed into place, so a reader never sees
    half a JPEG.
    """
    target = photo_dir / strip_name(filename)
    if target.is_file():
        return False

    with Image.open(photo_dir / filename) as full:
        strip = render_strip(full)

    handle, temporary = tempfile.mkstemp(prefix=".strip-", suffix=".jpg", dir=photo_dir)
    try:
        with os.fdopen(handle, "wb") as out:
            # No exif= argument: a fresh JPEG carries no metadata from the full.
            strip.save(out, format="JPEG", quality=QUALITY, optimize=True)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return True


def srcset(photo: dict[str, Any]) -> str:
    """The thumbnail and the strip, each with its real width, for an `<img srcset>`.

    No existence check: a request for a strip not yet made makes it.
    """
    width, height = photo.get("width"), photo.get("height")
    if not width or not height:
        return ""
    photo_id = photo["id"]
    thumb_width, _ = fitted(width, height, THUMB_MAX)
    strip_width, _ = fitted(width, height, STRIP_MAX)
    thumb = f"/photos/{photo_id}/thumb {thumb_width}w"
    if strip_width <= thumb_width:
        # Too small for a larger version; a duplicate width descriptor is invalid.
        return thumb
    return f"{thumb}, /photos/{photo_id}/strip?v={VERSION} {strip_width}w"


def backfill(
    conn: sqlite3.Connection, photo_dir: Path, *, dry_run: bool = False, prune: bool = False
) -> dict[str, Any]:
    """Make every missing strip (`moving thumbnails`). Safe to re-run, and while serving.

    `prune` also removes strips of an older recipe -- which a service still
    running the old code would be advertising.
    """
    report: dict[str, Any] = {"made": 0, "present": 0, "pruned": 0, "missing": []}
    for row in conn.execute("SELECT id, filename FROM photos ORDER BY id"):
        name = row["filename"]
        if not (photo_dir / name).is_file():
            report["missing"].append(name)
            continue
        if (photo_dir / strip_name(name)).is_file():
            report["present"] += 1
        else:
            if not dry_run:
                write_strip(photo_dir, name)
            report["made"] += 1
        if prune:
            for stale in strip_files(photo_dir, name):
                if stale.name == strip_name(name):
                    continue
                if not dry_run:
                    stale.unlink(missing_ok=True)
                report["pruned"] += 1
    return report
