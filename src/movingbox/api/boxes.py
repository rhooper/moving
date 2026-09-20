"""Box, item, event and search endpoints."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .. import store, summarise
from ..config import Config
from . import events
from .app import get_config, get_conn, get_events
from .schemas import BoxWrite, ItemCreate, ItemUpdate, LocationChange, StatusChange

router = APIRouter(prefix="/api", tags=["boxes"])


def _require(conn: sqlite3.Connection, code: str) -> dict:
    """The record, for something that is about to change it.

    A deleted record is reported as deleted rather than missing: 404 would say
    it never existed, when the answer is "restore it first".
    """
    box = store.get_box(conn, code, include_deleted=True)
    if box is None:
        raise HTTPException(status_code=404, detail=f"No box {code}")
    if box["deleted_at"] is not None:
        raise HTTPException(
            status_code=409, detail=f"{code} is deleted; restore it before changing it"
        )
    return box


def _require_readable(conn: sqlite3.Connection, code: str) -> dict:
    """The record, for something that is only going to read it."""
    box = store.get_box(conn, code, include_deleted=True)
    if box is None:
        raise HTTPException(status_code=404, detail=f"No box {code}")
    return box


@router.get("/boxes")
def list_boxes(
    q: str | None = None,
    status: str | None = None,
    room_id: int | None = None,
    location: str | None = None,
    fragile: bool | None = None,
    limit: int = Query(default=100, le=1000),
    offset: int = 0,
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[dict]:
    return store.list_boxes(
        conn,
        q=q,
        status=status,
        room_id=room_id,
        location=location,
        fragile=fragile,
        limit=limit,
        offset=offset,
    )


@router.get("/boxes/deleted")
def list_deleted(conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    """What is in the bin. Declared above /boxes/{code} so it is not read as one."""
    return store.deleted_boxes(conn)


@router.post("/boxes", status_code=201)
def create_box(
    body: BoxWrite,
    conn: sqlite3.Connection = Depends(get_conn),
    changes: events.Publisher = Depends(get_events),
) -> dict:
    # Every publish below happens after the store call returns, so a request
    # that failed announces nothing and no client refetches for no reason.
    try:
        box = store.create_box(conn, **body.set_fields())
    except ValueError as bad:
        # The schema checks each value; only the store knows whether they make
        # sense together -- a size on something that is not a container.
        raise HTTPException(status_code=422, detail=str(bad)) from bad
    changes.publish(events.BOX_CREATED, box["code"])
    return box


@router.get("/boxes/{code}")
def get_box(code: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """The record, including one in the bin.

    It is already out of the list and out of search, so getting here means you
    know the code -- you scanned it, or came from the bin. Answering 404 would
    tell someone who had just deleted a box by mistake that it never existed.
    """
    box = store.get_box(conn, code, include_deleted=True)
    if box is None:
        raise HTTPException(status_code=404, detail=f"No box {code}")
    return box


@router.patch("/boxes/{code}")
def update_box(
    code: str,
    body: BoxWrite,
    conn: sqlite3.Connection = Depends(get_conn),
    changes: events.Publisher = Depends(get_events),
) -> dict:
    _require(conn, code)
    try:
        box = store.update_box(conn, code, **body.set_fields())
    except ValueError as bad:
        raise HTTPException(status_code=422, detail=str(bad)) from bad
    changes.publish(events.BOX_UPDATED, code)
    return box


@router.delete("/boxes/{code}", status_code=204)
def delete_box(
    code: str,
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
    changes: events.Publisher = Depends(get_events),
) -> Response:
    if not store.delete_box(conn, config, code):
        raise HTTPException(status_code=404, detail=f"No box {code}")
    changes.publish(events.BOX_DELETED, code)
    return Response(status_code=204)


@router.post("/boxes/{code}/restore")
def restore_box(
    code: str,
    conn: sqlite3.Connection = Depends(get_conn),
    changes: events.Publisher = Depends(get_events),
) -> dict:
    box = store.restore_box(conn, code)
    if box is None:
        raise HTTPException(status_code=404, detail=f"No box {code}")
    changes.publish(events.BOX_RESTORED, code)
    return box


@router.delete("/boxes/{code}/purge", status_code=204)
def purge_box(
    code: str,
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
    changes: events.Publisher = Depends(get_events),
) -> Response:
    """Destroy a record for good. Only reachable once it is already deleted."""
    try:
        gone = store.purge_box(conn, config, code)
    except store.NotDeleted as live:
        # 409: the request is fine, the record's state is not.
        raise HTTPException(status_code=409, detail=str(live)) from live
    if not gone:
        raise HTTPException(status_code=404, detail=f"No box {code}")
    changes.publish(events.BOX_DELETED, code)
    return Response(status_code=204)


@router.post("/boxes/{code}/status")
def set_status(
    code: str,
    body: StatusChange,
    conn: sqlite3.Connection = Depends(get_conn),
    changes: events.Publisher = Depends(get_events),
) -> dict:
    _require(conn, code)
    box = store.set_status(conn, code, body.status, actor=body.actor)
    changes.publish(events.BOX_STATUS, code)
    return box


@router.post("/boxes/{code}/location")
def set_location(
    code: str,
    body: LocationChange,
    conn: sqlite3.Connection = Depends(get_conn),
    changes: events.Publisher = Depends(get_events),
) -> dict:
    _require(conn, code)
    box = store.set_location(conn, code, body.current_location, actor=body.actor)
    changes.publish(events.BOX_LOCATION, code)
    return box


@router.get("/boxes/{code}/items")
def list_items(code: str, conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    _require_readable(conn, code)
    return store.list_items(conn, code)


@router.post("/boxes/{code}/items", status_code=201)
def add_item(
    code: str,
    body: ItemCreate,
    conn: sqlite3.Connection = Depends(get_conn),
    changes: events.Publisher = Depends(get_events),
) -> dict:
    _require(conn, code)
    item = store.add_item(conn, code, **body.model_dump())
    changes.publish(events.ITEMS_CHANGED, code)
    return item


@router.patch("/items/{item_id}")
def update_item(
    item_id: int,
    body: ItemUpdate,
    conn: sqlite3.Connection = Depends(get_conn),
    changes: events.Publisher = Depends(get_events),
) -> dict:
    """Rename or re-count one item.

    Renaming an autogenerated item makes it the person's: `store.update_item`
    flips its source to 'manual', and photo analysis leaves it alone from then.
    """
    try:
        item = store.update_item(conn, item_id, **body.model_dump(exclude_unset=True))
    except ValueError as bad:
        raise HTTPException(status_code=422, detail=str(bad)) from bad
    if item is None:
        raise HTTPException(status_code=404, detail=f"No item {item_id}")
    changes.publish(events.ITEMS_CHANGED, store.code_for_item(conn, item_id))
    return item


@router.delete("/items/{item_id}", status_code=204)
def delete_item(
    item_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
    changes: events.Publisher = Depends(get_events),
) -> Response:
    # Which box owns it, read before the row goes. The notification names a
    # box because that is what a client re-fetches by, and by the time it is
    # sent the item id refers to nothing.
    owner = store.code_for_item(conn, item_id)
    if not store.delete_item(conn, item_id):
        raise HTTPException(status_code=404, detail=f"No item {item_id}")
    changes.publish(events.ITEMS_CHANGED, owner)
    return Response(status_code=204)


@router.get("/boxes/{code}/summary-suggestion")
def suggest_summary(code: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """A summary assembled from the box's items.

    Proposed, never applied -- the same rule as a photo draft. The caller puts
    it in the field and decides whether to keep it.
    """
    _require_readable(conn, code)
    return {"summary": summarise.from_items(store.list_items(conn, code))}


@router.get("/boxes/{code}/events")
def list_events(code: str, conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    _require_readable(conn, code)
    return store.events_for(conn, code)


@router.get("/search")
def search_boxes(
    q: str,
    limit: int = Query(default=50, le=500),
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[dict]:
    return store.list_boxes(conn, q=q, limit=limit)
