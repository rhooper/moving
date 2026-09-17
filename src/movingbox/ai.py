"""Drafting box contents from photographs.

A draft is a *proposal*. It is recorded, returned, and never applied: the model
suggests, a person accepts. Auto-applying would silently overwrite contents
someone typed, and would blur the line between what was observed and what was
guessed -- which is exactly the line ``items.source`` exists to keep.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from typing import Any

from . import storage, store
from .config import Config
from .vision import base


class NoPhotos(ValueError):
    """The box has no photo to look at."""


def _record_start(conn, box_id, provider, model) -> int:
    cursor = conn.execute(
        """
        INSERT INTO ai_jobs (box_id, provider, model, prompt_version, status)
        VALUES (?, ?, ?, ?, 'running')
        """,
        (box_id, provider, model, base.PROMPT_VERSION),
    )
    return cursor.lastrowid


def get_job(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def draft_for_box(
    conn: sqlite3.Connection,
    config: Config,
    code: str,
    provider: base.VisionProvider,
    *,
    photo_ids: list[int] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Ask a vision provider to describe a box's photos.

    Returns ``{"job_id", "draft"}``. Raises NoPhotos, or DraftUnreadable if the
    model's reply could not be used -- the job row records either outcome so a
    bad prompt or a wedged model is visible afterwards.
    """
    box = store.get_box(conn, code)
    if box is None:
        raise store.UnknownBox(code)

    photos = storage.list_photos(conn, code)
    if photo_ids is not None:
        wanted = set(photo_ids)
        photos = [p for p in photos if p["id"] in wanted]
    if not photos:
        raise NoPhotos(f"{code} has no photo to draft from -- take one first")

    images = []
    for photo in photos:
        path = config.photo_dir / photo["filename"]
        if path.is_file():
            images.append(path.read_bytes())
    if not images:
        raise NoPhotos(f"{code}'s photo files are missing from {config.photo_dir}")

    model = model or config.vision_model
    job_id = _record_start(conn, box["id"], provider.name, model)

    try:
        draft = provider.draft(images, model=model)
    except base.DraftUnreadable as failure:
        conn.execute(
            """
            UPDATE ai_jobs
               SET status = 'error', error = ?, completed_at = datetime('now')
             WHERE id = ?
            """,
            (str(failure), job_id),
        )
        raise

    conn.execute(
        """
        UPDATE ai_jobs
           SET status = 'done', raw_response = ?, completed_at = datetime('now')
         WHERE id = ?
        """,
        (json.dumps(dataclasses.asdict(draft)), job_id),
    )
    return {"job_id": job_id, "draft": dataclasses.asdict(draft)}
