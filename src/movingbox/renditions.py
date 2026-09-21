"""Derived images: made from the stored full photo, and never from each other.

The full image is the record -- uploads are deduplicated by its hash, and
nothing here ever writes it. Everything in this module is derived from it, so
all of it can be thrown away and remade.

**Why there are two sizes.** Measured in headless Chrome at a 412 px viewport
(2026-09-21), how many device pixels each source pixel had to cover:

    list row cover     42 x 42 css    0.42 at 3x   fine -- 2.4x oversupplied
    nested rows        64 x 64 css    0.64 at 3x   fine
    photo strip       186 x 248 css   1.86 at 3x   upscaled, and soft
    sub-item modal    160 x 213 css   1.60 at 3x   upscaled, and soft

The list was never the problem, so its thumbnail is left exactly as it was:
same size, no sharpening, same bytes. Growing that one to suit the strip would
have taken a screenful of forty rows from about 1.0 MB to 3.4 MB for nothing
visible. The strip gets its own image instead.

**The rule the design rests on: a strip URL names one set of bytes, forever.**
The service worker answers from its cache without asking the network, and the
photo route is cached for a year, so a URL whose bytes change is simply never
fetched again -- the phone keeps the old soft image indefinitely. So:

- the version in the URL is a hash of the *recipe* (size, sharpening,
  quality). Change the recipe and every strip gets a new URL, which is a cache
  miss everywhere. It is derived, never typed: a hand-bumped version is one
  nobody bumps, which is how sw.js once kept phones on a stale app;
- every path that makes a strip -- an upload, `moving thumbnails`, a request
  for one not yet made -- renders it from the same stored file with the same
  recipe, so it is the same bytes whichever made it;
- a request for any other version is a 404, never "the current one instead".
  A fallback under a versioned URL would be cached as that version for a year.
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

#: Long edge of the list thumbnail. Unchanged: see the table above.
THUMB_MAX = 400

#: Long edge of the strip image. A portrait strip figure is 186 css px wide on
#: the phone, 558 device px at 3x; 800 on the long edge is 600 across. The
#: desktop strip is 252 css px, 504 at 2x. 1000 would cover a 3.5x screen too,
#: at 45% more bytes for a difference nobody could see at that size.
STRIP_MAX = 800

#: Unsharp mask after the downscale: (radius, percent, threshold). Chosen by
#: looking -- the same crops of real photos, drawn at the strip's device-pixel
#: size, side by side. Resolution did most of the work: 800 px alone turned
#: "22-18AWG 0.5-1.5mm2" on a parts box from a smear into print. Sharpening is
#: the refinement on top.
#:
#: - radius 0.8: matched to what a 2.5x downscale loses, about a pixel of edge;
#:   at 1.5 the letters grew visible light halos.
#: - percent 80: at 50 it was indistinguishable from none; at 120 the flat
#:   cardboard of a box began to speckle -- JPEG noise, amplified.
#: - threshold 3: leaves low-contrast differences (grain, compression noise)
#:   alone, and every edge worth keeping is far above it. 2 let the cardboard
#:   grain through; 4 kept the text as crisp and nothing more.
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

    Mirrors Pillow's own rounding, which picks whichever of floor and ceil keeps
    the aspect closest. srcset's width descriptors are made from this, so they
    describe the image actually served rather than an approximation of it.
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


def strip_name(filename: str, v: str = VERSION) -> str:
    """The strip's filename, beside the full image it was made from."""
    return f"{Path(filename).stem}-strip-{v}.jpg"


def strip_files(photo_dir: Path, filename: str) -> list[Path]:
    """Every strip on disk for this photo, of any recipe.

    Matched exactly rather than by a loose glob, so another photo whose name
    happens to start with this one's is never taken for it.
    """
    stem = Path(filename).stem
    pattern = re.compile(rf"{re.escape(stem)}-strip-[0-9a-f]{{{_VERSION_LENGTH}}}\.jpg")
    return sorted(
        path for path in photo_dir.glob(f"{stem}-strip-*.jpg") if pattern.fullmatch(path.name)
    )


def render_strip(full: Image.Image) -> Image.Image:
    """The strip image, from the full photo.

    Works on a loaded copy, so Pillow's JPEG draft shortcut can never apply:
    it would decode at a reduced scale when handed an unread file, and the
    same photo would then make different bytes depending on how it was opened.
    """
    image = full.convert("RGB")
    image.thumbnail((STRIP_MAX, STRIP_MAX), Image.LANCZOS)
    return image.filter(ImageFilter.UnsharpMask(*UNSHARP))


def write_strip(photo_dir: Path, filename: str) -> bool:
    """Make this photo's strip if it is not there yet. True if one was written.

    From the **full image**, never from the old thumbnail -- remaking a
    thumbnail from a thumbnail compounds the blur. Written beside the target
    under a hidden name and renamed into place, which is atomic on one
    filesystem: the service may be serving this directory at the time, and a
    reader must never see half a JPEG.
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
    """The images a photo can be drawn from, for an `<img srcset>`.

    The thumbnail and the strip, each described by its real width, so the
    browser picks by the space the image fills and the screen's density: a 3x
    phone takes the strip, a 1x desktop the thumbnail. Built from the row
    alone -- the strip needs no existence check, because a request for one not
    yet made makes it.
    """
    width, height = photo.get("width"), photo.get("height")
    if not width or not height:
        return ""
    photo_id = photo["id"]
    thumb_width, _ = fitted(width, height, THUMB_MAX)
    strip_width, _ = fitted(width, height, STRIP_MAX)
    thumb = f"/photos/{photo_id}/thumb {thumb_width}w"
    if strip_width <= thumb_width:
        # A photo too small to have a larger version: two candidates of one
        # width is not a choice, and a duplicate descriptor is invalid.
        return thumb
    return f"{thumb}, /photos/{photo_id}/strip?v={VERSION} {strip_width}w"


def backfill(
    conn: sqlite3.Connection, photo_dir: Path, *, dry_run: bool = False, prune: bool = False
) -> dict[str, Any]:
    """Make every photo's strip that is missing. What `moving thumbnails` runs.

    Safe to re-run and safe while the service is running: it only ever *reads*
    the database, never touches a full image or a list thumbnail, and each
    strip is renamed into place whole. A strip already there is left alone, so
    a second run does nothing.

    `prune` also removes strips of an older recipe. Off by default: a copy of
    the service still running the old code would be advertising exactly those.
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
