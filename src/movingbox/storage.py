"""Photo storage.

These are inventory snapshots, not photographs: a phone's 4000 px original
buys nothing and fills the disk, so images are downscaled on the way in.

Two things are done deliberately rather than left to the viewer:

* **EXIF orientation is baked in.** An unrotated phone photo displays sideways
  in an ``<img>``, and not every viewer honours the tag.
* **All other metadata is stripped.** Photos taken indoors carry GPS, and this
  database is served across a tailnet and exported to JSON. The location of
  the house should not travel with a picture of a box of saucepans.
"""

from __future__ import annotations

import hashlib
import io
import sqlite3
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from . import search, store
from .config import Config

#: Long edge for the stored image. Ample for reading a label in a box.
FULL_MAX = 2048
#: Long edge for the list thumbnail.
THUMB_MAX = 400
JPEG_QUALITY = 82


class NotAnImage(ValueError):
    """The upload could not be decoded as an image."""


def _prepare(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise NotAnImage("that upload is not a readable image") from exc

    # exif_transpose applies the orientation tag and drops it, so the pixels
    # are upright and nothing downstream has to interpret it.
    image = ImageOps.exif_transpose(image)
    return image.convert("RGB")


def _write(image: Image.Image, path: Path, longest: int) -> tuple[int, int]:
    copy = image.copy()
    copy.thumbnail((longest, longest), Image.LANCZOS)
    # No exif= argument: a fresh JPEG carries no metadata from the original.
    copy.save(path, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return copy.size


def save_photo(
    conn: sqlite3.Connection,
    config: Config,
    code: str,
    data: bytes,
    *,
    filename: str = "photo.jpg",
    caption: str | None = None,
) -> dict[str, Any]:
    """Store a photo against a box. Re-uploading the same bytes is a no-op.

    The phone retries failed uploads, so identical bytes will arrive twice;
    the ``(box_id, sha256)`` uniqueness makes that harmless rather than
    producing duplicates.
    """
    box = store.get_box(conn, code)
    if box is None:
        raise store.UnknownBox(code)

    # Decode before touching the database or the disk, so a bad upload leaves
    # nothing behind.
    image = _prepare(data)
    digest = hashlib.sha256(data).hexdigest()

    existing = conn.execute(
        "SELECT * FROM photos WHERE box_id = ? AND sha256 = ?", (box["id"], digest)
    ).fetchone()
    if existing is not None:
        return dict(existing)

    config.photo_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{box['code']}-{digest[:12]}"
    full_name, thumb_name = f"{stem}.jpg", f"{stem}-thumb.jpg"

    width, height = _write(image, config.photo_dir / full_name, FULL_MAX)
    _write(image, config.photo_dir / thumb_name, THUMB_MAX)
    size = (config.photo_dir / full_name).stat().st_size

    is_first = (
        conn.execute("SELECT count(*) FROM photos WHERE box_id = ?", (box["id"],)).fetchone()[0]
        == 0
    )

    cursor = conn.execute(
        """
        INSERT INTO photos
            (box_id, filename, thumb_filename, width, height, bytes, sha256,
             caption, is_primary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            box["id"],
            full_name,
            thumb_name,
            width,
            height,
            size,
            digest,
            caption,
            1 if is_first else 0,
        ),
    )
    search.reindex_box(conn, box["id"])
    row = conn.execute("SELECT * FROM photos WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


def list_photos(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    box = store.get_box(conn, code)
    if box is None:
        raise store.UnknownBox(code)
    rows = conn.execute(
        "SELECT * FROM photos WHERE box_id = ? ORDER BY is_primary DESC, id", (box["id"],)
    )
    return [dict(r) for r in rows]


def get_photo(conn: sqlite3.Connection, photo_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    return dict(row) if row else None


def set_caption(conn: sqlite3.Connection, photo_id: int, caption: str | None) -> dict[str, Any]:
    photo = get_photo(conn, photo_id)
    if photo is None:
        raise LookupError(photo_id)
    conn.execute("UPDATE photos SET caption = ? WHERE id = ?", (caption, photo_id))
    search.reindex_box(conn, photo["box_id"])
    return get_photo(conn, photo_id)


def set_cover(conn: sqlite3.Connection, photo_id: int) -> dict[str, Any]:
    """Make this photo the one its box is recognised by.

    Demote first, promote second: the schema allows exactly one flagged photo
    per box (see migration 0003), so the other order would collide with the
    outgoing cover. Choosing the photo that is already the cover is a no-op
    rather than an error -- two phones can tap the same picture.

    No reindex: ``is_primary`` is not indexed text, and the captions FTS reads
    are untouched.
    """
    photo = get_photo(conn, photo_id)
    if photo is None:
        raise LookupError(photo_id)

    conn.execute(
        "UPDATE photos SET is_primary = 0 WHERE box_id = ? AND id != ?",
        (photo["box_id"], photo_id),
    )
    conn.execute("UPDATE photos SET is_primary = 1 WHERE id = ?", (photo_id,))
    return get_photo(conn, photo_id)


def delete_photo(conn: sqlite3.Connection, config: Config, photo_id: int) -> bool:
    photo = get_photo(conn, photo_id)
    if photo is None:
        return False

    for name in (photo["filename"], photo["thumb_filename"]):
        if name:
            (config.photo_dir / name).unlink(missing_ok=True)

    conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    # Promote another photo so a box that still has photos does not lose its
    # cover. Only when the *cover* went: promoting unconditionally used to be
    # harmless when the cover was always the oldest photo, but once it is
    # somebody's choice it silently changes the picture on the box -- and
    # leaves two flagged photos, which the schema now refuses outright.
    if photo["is_primary"]:
        remaining = conn.execute(
            "SELECT id FROM photos WHERE box_id = ? ORDER BY id LIMIT 1", (photo["box_id"],)
        ).fetchone()
        if remaining is not None:
            conn.execute("UPDATE photos SET is_primary = 1 WHERE id = ?", (remaining["id"],))
    search.reindex_box(conn, photo["box_id"])
    return True
