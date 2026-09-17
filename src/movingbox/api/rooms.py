"""Room endpoints. Rooms are both sources ('Basement shelf 3') and destinations."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from .. import store
from .app import get_conn
from .schemas import RoomCreate

router = APIRouter(prefix="/api", tags=["rooms"])


@router.get("/rooms")
def list_rooms(conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    return store.list_rooms(conn)


@router.post("/rooms", status_code=201)
def create_room(body: RoomCreate, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    fields = body.model_dump()
    name = fields.pop("name")
    try:
        return store.create_room(conn, name, **fields)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=f"Room {name!r} already exists") from exc
