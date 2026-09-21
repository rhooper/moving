"""Box, item, event and search endpoints."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .. import phrasing, store, summarise
from ..config import Config
from . import events
from .app import get_config, get_conn, get_events, get_phraser
from .schemas import BoxWrite, ItemCreate, ItemUpdate, LocationChange, StatusChange

router = APIRouter(prefix="/api", tags=["boxes"])


def _require(conn: sqlite3.Connection, code: str) -> dict:
    """The record, for something about to change it. A binned one is 409, not 404."""
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
    try:
        box = store.create_box(conn, **body.set_fields())
    except ValueError as bad:
        # The store checks combinations the schema cannot, such as a size on
        # a single thing.
        raise HTTPException(status_code=422, detail=str(bad)) from bad
    changes.publish(events.BOX_CREATED, box["code"])
    box = _with_nesting(conn, box)
    if box["parent"]:
        changes.publish(events.BOX_UPDATED, box["parent"]["code"])
    return box


@router.get("/boxes/{code}")
def get_box(code: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """The record, including one in the bin, so a mistaken delete can be restored."""
    box = store.get_box(conn, code, include_deleted=True)
    if box is None:
        raise HTTPException(status_code=404, detail=f"No box {code}")
    return _with_nesting(conn, box)


def _with_nesting(conn: sqlite3.Connection, box: dict) -> dict:
    """The record, plus what it is inside and what is inside it.

    `path` is outermost first, for a breadcrumb; `parent` is its last step.
    """
    path = [_brief(step) for step in store.path_to(conn, box["code"])]
    return {
        **box,
        "path": path,
        "parent": path[-1] if path else None,
        "children": store.children_of(conn, box["code"]),
    }


def _brief(box: dict) -> dict:
    # The page needs a container's room (a nested record inherits it) and
    # whether it is already fragile.
    return {
        key: box[key]
        for key in ("code", "kind", "content_summary", "destination_room_id", "fragile", "size")
    }


@router.patch("/boxes/{code}")
def update_box(
    code: str,
    body: BoxWrite,
    conn: sqlite3.Connection = Depends(get_conn),
    changes: events.Publisher = Depends(get_events),
) -> dict:
    _require(conn, code)
    fields = body.set_fields()
    moving = "parent_code" in fields
    parent_code = fields.pop("parent_code", None)
    was_inside = store.path_to(conn, code)[-1:] if moving else []
    try:
        if moving:
            # First, so a refused move changes nothing else either.
            store.set_parent(conn, code, parent_code)
        box = store.update_box(conn, code, **fields)
    except ValueError as bad:
        raise HTTPException(status_code=422, detail=str(bad)) from bad
    changes.publish(events.BOX_UPDATED, code)
    if moving:
        # Both the old container and the new one.
        for end in {step["code"] for step in was_inside} | ({parent_code} - {None}):
            changes.publish(events.BOX_UPDATED, end)
    return _with_nesting(conn, box)


@router.delete("/boxes/{code}", status_code=204)
def delete_box(
    code: str,
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
    changes: events.Publisher = Depends(get_events),
) -> Response:
    was_inside = store.path_to(conn, code)[-1:]
    try:
        deleted = store.delete_box(conn, config, code)
    except store.NotEmpty as full:
        raise HTTPException(status_code=409, detail=str(full)) from full
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No box {code}")
    changes.publish(events.BOX_DELETED, code)
    for parent in was_inside:
        changes.publish(events.BOX_UPDATED, parent["code"])
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
    """Rename or re-count one item. Renaming an autogenerated one makes it 'manual'."""
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
    # Read before the row goes: events name the box, not the item.
    owner = store.code_for_item(conn, item_id)
    if not store.delete_item(conn, item_id):
        raise HTTPException(status_code=404, detail=f"No item {item_id}")
    changes.publish(events.ITEMS_CHANGED, owner)
    return Response(status_code=204)


@router.get("/boxes/{code}/summary-suggestion")
def suggest_summary(
    code: str,
    conn: sqlite3.Connection = Depends(get_conn),
    config: Config = Depends(get_config),
    phraser: phrasing.Phraser | None = Depends(get_phraser),
) -> dict:
    """A proposed summary of the box's items and whatever is nested in it, at any depth.

    Nothing is written. `source` is "model" when a model phrased the line and
    "assembled" otherwise, which is a normal outcome (see phrasing.py).
    """
    _require_readable(conn, code)
    contents = summarise.contents(store.subtree(conn, code))
    summary, source = phrasing.summary_for(contents, phraser, model=config.summary_model)
    return {"summary": summary, "source": source}


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
    """Matching records, then the containers they are inside (`matched: false`)."""
    return store.search_with_containers(conn, q, limit=limit)
