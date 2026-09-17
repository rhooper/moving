"""Settings the panel can change.

Everything here is global and lives in the database rather than the
environment, because it describes the data: a database restored onto another
machine has to keep issuing codes that match the labels already on boxes.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from .. import codes
from .app import get_conn
from .schemas import CodeFormat, NextNumber

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _described(conn: sqlite3.Connection) -> dict:
    shape = codes.get_format(conn)
    return {**shape, "example": codes.render(1, **shape)}


@router.get("/code-format")
def get_code_format(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return _described(conn)


@router.put("/code-format")
def set_code_format(body: CodeFormat, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    try:
        codes.set_format(conn, prefix=body.prefix, separator=body.separator, digits=body.digits)
    except ValueError as bad:
        # 422 with the reason, so the panel can show what is actually wrong
        # rather than "invalid".
        raise HTTPException(status_code=422, detail=str(bad)) from bad
    return _described(conn)


@router.put("/next-number")
def set_next_number(body: NextNumber, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    shape = codes.get_format(conn)
    candidate = codes.render(body.number, **shape)

    # Winding the counter back onto a code that already exists would put two
    # boxes behind one printed label -- the single thing the counter exists to
    # prevent. Setting it by hand must not be a way around that.
    if conn.execute("SELECT 1 FROM boxes WHERE code = ?", (candidate,)).fetchone():
        raise HTTPException(
            status_code=409,
            detail=f"{candidate} already exists; pick a number that has not been used.",
        )

    try:
        codes.set_sequence(conn, body.number)
    except ValueError as bad:
        raise HTTPException(status_code=422, detail=str(bad)) from bad
    return {"next": candidate}
