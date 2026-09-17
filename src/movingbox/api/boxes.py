"""Box, item, event and search endpoints."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .. import store
from .app import get_conn
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
def create_box(body: BoxWrite, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return store.create_box(conn, **body.set_fields())


@router.get("/boxes/{code}")
def get_box(code: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return _require(conn, code)


@router.patch("/boxes/{code}")
def update_box(code: str, body: BoxWrite, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    _require(conn, code)
    return store.update_box(conn, code, **body.set_fields())


@router.delete("/boxes/{code}", status_code=204)
def delete_box(code: str, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    if not store.delete_box(conn, code):
        raise HTTPException(status_code=404, detail=f"No box {code}")
    return Response(status_code=204)


@router.post("/boxes/{code}/status")
def set_status(code: str, body: StatusChange, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    _require(conn, code)
    return store.set_status(conn, code, body.status, actor=body.actor)


@router.post("/boxes/{code}/location")
def set_location(
    code: str, body: LocationChange, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    _require(conn, code)
    return store.set_location(conn, code, body.current_location, actor=body.actor)


@router.get("/boxes/{code}/items")
def list_items(code: str, conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    _require(conn, code)
    return store.list_items(conn, code)


@router.post("/boxes/{code}/items", status_code=201)
def add_item(code: str, body: ItemCreate, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    _require(conn, code)
    return store.add_item(conn, code, **body.model_dump())


@router.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    if not store.delete_item(conn, item_id):
        raise HTTPException(status_code=404, detail=f"No item {item_id}")
    return Response(status_code=204)


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
