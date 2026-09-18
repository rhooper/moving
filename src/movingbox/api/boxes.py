"""Box, item, event and search endpoints."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .. import store, summarise
from ..config import Config
from . import events
from .app import get_config, get_conn, get_events
from .schemas import BoxWrite, ItemCreate, LocationChange, StatusChange

router = APIRouter(prefix="/api", tags=["boxes"])


def _require(conn: sqlite3.Connection, code: str) -> dict:
    box = store.get_box(conn, code)
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


@router.post("/boxes", status_code=201)
def create_box(
    body: BoxWrite,
    conn: sqlite3.Connection = Depends(get_conn),
    changes: events.Publisher = Depends(get_events),
) -> dict:
    # Every publish below happens after the store call returns, so a request
    # that failed announces nothing and no client refetches for no reason.
    box = store.create_box(conn, **body.set_fields())
    changes.publish(events.BOX_CREATED, box["code"])
    return box


@router.get("/boxes/{code}")
def get_box(code: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return _require(conn, code)


@router.patch("/boxes/{code}")
def update_box(
    code: str,
    body: BoxWrite,
    conn: sqlite3.Connection = Depends(get_conn),
    changes: events.Publisher = Depends(get_events),
) -> dict:
    _require(conn, code)
    box = store.update_box(conn, code, **body.set_fields())
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
    _require(conn, code)
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
    _require(conn, code)
    return {"summary": summarise.from_items(store.list_items(conn, code))}


@router.get("/boxes/{code}/events")
def list_events(code: str, conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    _require(conn, code)
    return store.events_for(conn, code)


@router.get("/search")
def search_boxes(
    q: str,
    limit: int = Query(default=50, le=500),
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[dict]:
    return store.list_boxes(conn, q=q, limit=limit)
