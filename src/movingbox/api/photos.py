"""Photo upload and serving, and AI drafting from those photos."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from .. import ai, analysis, storage, store
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
    # Deliberately sync, and reading through file.file rather than `await
    # file.read()`. An async endpoint runs on the event loop while the sync
    # get_conn dependency runs in a threadpool, so the sqlite connection would
    # be created in one thread and used in another -- which sqlite3 refuses.
    data = file.file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail=f"photo is larger than {MAX_UPLOAD} bytes")

    try:
        photo = storage.save_photo(conn, config, code, data, filename=file.filename or "photo.jpg")
    except store.UnknownBox as missing:
        raise HTTPException(status_code=404, detail=f"No box {code}") from missing
    except storage.NotAnImage as bad:
        # 415, not 400: the request was well formed, the payload was not usable.
        raise HTTPException(status_code=415, detail=str(bad)) from bad

    # Analysis starts without being asked. Queued, not run: the model takes
    # seconds to tens of seconds and this request should not. Idempotent, so
    # the phone retrying an upload does not analyse the same photo twice.
    analysis.enqueue(conn, config, photo["id"])
    if analyst is not None:
        analyst.wake()

    # Announced even when the sha256 matched an existing photo and nothing was
    # written: the phone retrying an upload is exactly when another device
    # most wants to know a photo landed.
    changes.publish(events.PHOTOS_CHANGED, code)
    return storage.with_analysis(conn, photo)


@router.get("/api/boxes/{code}/photos")
def list_for_box(code: str, conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    # Not gated on deletion: a deleted record keeps its photos, and the page
    # that offers to restore it shows them.
    try:
        return storage.list_photos(conn, code)
    except store.UnknownBox as missing:
        raise HTTPException(status_code=404, detail=f"No box {code}") from missing


@router.get("/photos/{photo_id}/{size}")
def serve(
    photo_id: int,
    size: str,
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
) -> FileResponse:
    if size not in ("full", "thumb"):
        raise HTTPException(status_code=404, detail="size must be 'full' or 'thumb'")

    photo = storage.get_photo(conn, photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail=f"No photo {photo_id}")

    name = photo["filename"] if size == "full" else photo["thumb_filename"]
    path = config.photo_dir / (name or photo["filename"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="the photo file is missing from disk")
    # Content-addressed filenames, so this can be cached hard.
    return FileResponse(
        path, media_type="image/jpeg", headers={"cache-control": "public, max-age=31536000"}
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
    """Mark this photo as the one its box is recognised by in a list.

    Sync `def`, like everything here that takes get_conn: an async endpoint
    would run on the event loop while the connection was opened in a
    threadpool worker, which sqlite3 refuses.

    There is no body -- the photo id in the path is the whole request -- so
    this needs no schema and stays out of schemas.py.
    """
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
    # Which box it belonged to, read before the row goes: the notification
    # names a box, and the photo id will refer to nothing by the time it lands.
    photo = storage.get_photo(conn, photo_id)
    if not storage.delete_photo(conn, config, photo_id):
        raise HTTPException(status_code=404, detail=f"No photo {photo_id}")
    changes.publish(events.PHOTOS_CHANGED, store.code_of(conn, photo["box_id"]))
    return Response(status_code=204)


@router.post("/photos/{photo_id}/analyse", status_code=202)
def analyse(
    photo_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
    changes: events.Publisher = Depends(get_events),
    analyst: analysis.Analyst | None = Depends(get_analyst),
) -> dict:
    """Queue one photo for analysis again: a retry after an error, or a re-run.

    202, not 200: the answer is "queued", and the result arrives as events.
    """
    photo = storage.get_photo(conn, photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail=f"No photo {photo_id}")
    if analysis.enqueue(conn, config, photo_id, again=True) is None:
        # 409: the request is fine; this record is a thing, not a container.
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
        # 502: we are the gateway to the model, and it is the model that failed.
        raise HTTPException(status_code=502, detail=str(failure)) from failure


@router.get("/api/ai/jobs/{job_id}")
def job(job_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    found = ai.get_job(conn, job_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id}")
    return found
