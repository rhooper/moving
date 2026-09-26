"""Photo upload and serving, and AI drafting from those photos."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse

from .. import ai, analysis, renditions, storage, store
from ..config import Config
from ..vision import base
from . import events
from .app import get_analyst, get_config, get_conn, get_events, get_vision_provider
from .schemas import CaptionUpdate, DraftRequest

router = APIRouter(tags=["photos"])

#: Bigger than any phone photo worth keeping, small enough to refuse a mistake.
MAX_UPLOAD = 25 * 1024 * 1024


@router.post("/api/boxes/{code}/photos", status_code=201)
def upload(
    code: str,
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
    changes: events.Publisher = Depends(get_events),
    analyst: analysis.Analyst | None = Depends(get_analyst),
) -> dict:
    # Sync, reading file.file rather than `await file.read()`: an async
    # endpoint would use the threadpool's sqlite connection on the event loop.
    data = file.file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail=f"photo is larger than {MAX_UPLOAD} bytes")

    try:
        photo = storage.save_photo(conn, config, code, data, filename=file.filename or "photo.jpg")
    except store.UnknownBox as missing:
        raise HTTPException(status_code=404, detail=f"No box {code}") from missing
    except storage.NotAnImage as bad:
        raise HTTPException(status_code=415, detail=str(bad)) from bad

    # Idempotent, so a retried upload is not analysed twice.
    analysis.enqueue(conn, config, photo["id"])
    if analyst is not None:
        analyst.wake()

    # Announced even for a duplicate upload.
    changes.publish(events.PHOTOS_CHANGED, code)
    return storage.with_analysis(conn, photo)


@router.get("/api/boxes/{code}/photos")
def list_for_box(code: str, conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    # A binned record keeps its photos, and the page offering restore shows them.
    try:
        return storage.list_photos(conn, code)
    except store.UnknownBox as missing:
        raise HTTPException(status_code=404, detail=f"No box {code}") from missing


@router.get("/photos/{photo_id}/{size}")
def serve(
    photo_id: int,
    size: str,
    v: str | None = None,
    k: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
) -> FileResponse:
    if size not in ("full", "thumb", "strip"):
        raise HTTPException(status_code=404, detail="size must be 'full', 'thumb' or 'strip'")

    photo = storage.get_photo(conn, photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail=f"No photo {photo_id}")
    # A URL keyed to other bytes is a photo that no longer exists, even if its
    # id was given to a new one: answering with the new one's bytes would cache
    # them under the old URL for a year. No key is served, for a page loaded
    # before keys existed; it reloads itself after the deploy.
    if k is not None and k != renditions.key(photo):
        raise HTTPException(status_code=404, detail=f"No photo {photo_id} with key {k}")

    if size == "strip":
        return _strip(photo, v, config)

    name = photo["filename"] if size == "full" else photo["thumb_filename"]
    path = config.photo_dir / (name or photo["filename"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="the photo file is missing from disk")
    # Cached for a year under a URL keyed by id and `k`, so these files must
    # never be rewritten in place. Anything regenerable goes through a
    # versioned URL.
    return FileResponse(
        path, media_type="image/jpeg", headers={"cache-control": "public, max-age=31536000"}
    )


def _strip(photo: dict, v: str | None, config: Config) -> FileResponse:
    """The strip image at the current version, made now if missing; any other version is 404.

    Serving the current bytes under an old version's URL would cache them as
    that version for a year.
    """
    if v != renditions.VERSION:
        raise HTTPException(status_code=404, detail="no strip image at that version")
    if not (config.photo_dir / photo["filename"]).is_file():
        raise HTTPException(status_code=404, detail="the photo file is missing from disk")

    renditions.write_strip(config.photo_dir, photo["filename"])
    return FileResponse(
        config.photo_dir / renditions.strip_name(photo["filename"]),
        media_type="image/jpeg",
        headers={"cache-control": "public, max-age=31536000, immutable"},
    )


@router.patch("/photos/{photo_id}")
def update_caption(
    photo_id: int,
    body: CaptionUpdate,
    conn: sqlite3.Connection = Depends(get_conn),
    changes: events.Publisher = Depends(get_events),
) -> dict:
    try:
        photo = storage.set_caption(conn, photo_id, body.caption)
    except LookupError as missing:
        raise HTTPException(status_code=404, detail=f"No photo {photo_id}") from missing

    changes.publish(events.PHOTOS_CHANGED, store.code_of(conn, photo["box_id"]))
    return photo


@router.post("/photos/{photo_id}/cover")
def make_cover(
    photo_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
    changes: events.Publisher = Depends(get_events),
) -> dict:
    """Make this photo the one its box is shown with in a list."""
    try:
        photo = storage.set_cover(conn, photo_id)
    except LookupError as missing:
        raise HTTPException(status_code=404, detail=f"No photo {photo_id}") from missing

    changes.publish(events.PHOTOS_CHANGED, store.code_of(conn, photo["box_id"]))
    return photo


@router.delete("/photos/{photo_id}", status_code=204)
def delete(
    photo_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
    changes: events.Publisher = Depends(get_events),
) -> Response:
    # Read before the row goes: events name the box, not the photo.
    photo = storage.get_photo(conn, photo_id)
    if not storage.delete_photo(conn, config, photo_id):
        raise HTTPException(status_code=404, detail=f"No photo {photo_id}")
    changes.publish(events.PHOTOS_CHANGED, store.code_of(conn, photo["box_id"]))
    return Response(status_code=204)


@router.post("/photos/{photo_id}/analyse", status_code=202)
def analyse(
    photo_id: int,
    detail: bool = Query(default=False, description="A closer look: the slower, careful model"),
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
    changes: events.Publisher = Depends(get_events),
    analyst: analysis.Analyst | None = Depends(get_analyst),
) -> dict:
    """Queue one photo for analysis again. The result arrives as events."""
    photo = storage.get_photo(conn, photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail=f"No photo {photo_id}")
    if analysis.enqueue(conn, config, photo_id, again=True, detail=detail) is None:
        raise HTTPException(
            status_code=409,
            detail="This is a single thing, not a box: there are no contents to list.",
        )
    if analyst is not None:
        analyst.wake()
    changes.publish(events.PHOTOS_CHANGED, store.code_of(conn, photo["box_id"]))
    return storage.with_analysis(conn, photo)


@router.post("/api/boxes/{code}/ai/draft")
def draft(
    code: str,
    body: DraftRequest,
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
    provider: base.VisionProvider = Depends(get_vision_provider),
) -> dict:
    """Propose contents from the box's photos. Never applies them."""
    try:
        return ai.draft_for_box(
            conn, config, code, provider, photo_ids=body.photo_ids, model=body.model
        )
    except store.UnknownBox as missing:
        raise HTTPException(status_code=404, detail=f"No box {code}") from missing
    except ai.NoPhotos as empty:
        raise HTTPException(status_code=400, detail=str(empty)) from empty
    except base.DraftUnreadable as failure:
        raise HTTPException(status_code=502, detail=str(failure)) from failure


@router.get("/api/ai/jobs/{job_id}")
def job(job_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    found = ai.get_job(conn, job_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id}")
    return found
