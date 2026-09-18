"""Box lifecycle operations.

Every function that changes a box keeps the search index and the event log in
step, so callers (the HTTP layer, the CLI) never have to remember to.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import db, search

STATUSES = ("open", "packed", "loaded", "delivered", "unpacked")

# Columns a caller may set directly. Status and location are excluded: they go
# through set_status/set_location so the transition is always recorded.
EDITABLE = (
    "destination_room_id",
    "source_room_id",
    "source_location",
    "content_summary",
    "notes",
    "fragile",
    "open_first",
    "heavy",
    "weight_kg",
    "group_name",
    "group_index",
    "group_total",
)


class UnknownBox(LookupError):
    """No box with that code."""


class InvalidStatus(ValueError):
    """Not one of STATUSES."""


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _record(conn, box_id, kind, *, from_value=None, to_value=None, actor=None, note=None):
    conn.execute(
        """
        INSERT INTO events (box_id, kind, from_value, to_value, actor, note)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (box_id, kind, from_value, to_value, actor, note),
    )


def _require(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    box = get_box(conn, code)
    if box is None:
        raise UnknownBox(code)
    return box


# --- rooms ---------------------------------------------------------------


def create_room(conn: sqlite3.Connection, name: str, **fields) -> dict[str, Any]:
    columns = ["name", *fields]
    placeholders = ", ".join("?" * len(columns))
    conn.execute(
        f"INSERT INTO rooms ({', '.join(columns)}) VALUES ({placeholders})",
        (name, *fields.values()),
    )
    return _row(conn.execute("SELECT * FROM rooms WHERE name = ?", (name,)).fetchone())


def list_rooms(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM rooms ORDER BY sort_order, name").fetchall()
    return [dict(r) for r in rows]


# --- boxes ---------------------------------------------------------------


def create_box(conn: sqlite3.Connection, *, actor: str | None = None, **fields) -> dict[str, Any]:
    """Create a box with the next unused code, indexed and logged."""
    unknown = set(fields) - set(EDITABLE)
    if unknown:
        raise ValueError(f"Not settable at creation: {sorted(unknown)}")

    code = db.next_box_code(conn)
    columns = ["code", *fields]
    placeholders = ", ".join("?" * len(columns))
    cursor = conn.execute(
        f"INSERT INTO boxes ({', '.join(columns)}) VALUES ({placeholders})",
        (code, *fields.values()),
    )
    box_id = cursor.lastrowid
    _record(conn, box_id, "create", to_value=code, actor=actor)
    search.reindex_box(conn, box_id)
    return get_box(conn, code)


def get_box(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    return _row(conn.execute("SELECT * FROM boxes WHERE code = ?", (code,)).fetchone())


def update_box(conn: sqlite3.Connection, code: str, **fields) -> dict[str, Any]:
    box = _require(conn, code)
    unknown = set(fields) - set(EDITABLE)
    if unknown:
        raise ValueError(f"Not editable: {sorted(unknown)} (status and location have own calls)")
    if fields:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        conn.execute(
            f"UPDATE boxes SET {assignments}, updated_at = datetime('now') WHERE id = ?",
            (*fields.values(), box["id"]),
        )
        search.reindex_box(conn, box["id"])
    return get_box(conn, code)


def delete_box(conn: sqlite3.Connection, code: str) -> bool:
    box = get_box(conn, code)
    if box is None:
        return False
    conn.execute("DELETE FROM boxes WHERE id = ?", (box["id"],))
    search.reindex_box(conn, box["id"])  # clears the now-orphaned index row
    return True


def set_status(
    conn: sqlite3.Connection, code: str, status: str, *, actor: str | None = None
) -> dict[str, Any]:
    if status not in STATUSES:
        raise InvalidStatus(f"{status!r} is not one of {', '.join(STATUSES)}")
    box = _require(conn, code)
    if box["status"] == status:
        return box  # no transition, nothing worth logging

    conn.execute(
        "UPDATE boxes SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (status, box["id"]),
    )
    if status == "packed" and box["sealed_at"] is None:
        conn.execute("UPDATE boxes SET sealed_at = datetime('now') WHERE id = ?", (box["id"],))
    _record(conn, box["id"], "status", from_value=box["status"], to_value=status, actor=actor)
    return get_box(conn, code)


def set_location(
    conn: sqlite3.Connection, code: str, location: str | None, *, actor: str | None = None
) -> dict[str, Any]:
    box = _require(conn, code)
    if box["current_location"] == location:
        return box

    conn.execute(
        "UPDATE boxes SET current_location = ?, updated_at = datetime('now') WHERE id = ?",
        (location, box["id"]),
    )
    _record(
        conn,
        box["id"],
        "location",
        from_value=box["current_location"],
        to_value=location,
        actor=actor,
    )
    search.reindex_box(conn, box["id"])
    return get_box(conn, code)


# The photo a list row is drawn with, fetched in the same statement as the
# rows themselves. A list is the one place a per-row follow-up request is
# unaffordable: 200 boxes would mean 200 extra round trips just to find out
# which picture to show, and the client cannot batch them because it does not
# know the ids until the list arrives.
#
# It costs one index seek per returned row against idx_photos_one_cover (the
# partial unique index from migration 0003, whose WHERE clause this predicate
# matches), so the cost is bounded by `limit`, not by the size of the photos
# table -- and it is one statement, so one connection and one round trip.
_COVER = """(
    SELECT id FROM photos
     WHERE photos.box_id = boxes.id AND photos.is_primary = 1
) AS cover_photo_id"""


def list_boxes(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    status: str | None = None,
    room_id: int | None = None,
    location: str | None = None,
    fragile: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Boxes matching every supplied filter.

    When ``q`` is given the results keep search's relevance order; otherwise
    they are newest first.
    """
    where, params = [], []

    if q is not None:
        matched = search.search(conn, q, limit=1000)
        if not matched:
            return []
        where.append(f"id IN ({', '.join('?' * len(matched))})")
        params.extend(matched)
    if status is not None:
        where.append("status = ?")
        params.append(status)
    if room_id is not None:
        where.append("destination_room_id = ?")
        params.append(room_id)
    if location is not None:
        where.append("current_location = ?")
        params.append(location)
    if fragile is not None:
        where.append("fragile = ?")
        params.append(1 if fragile else 0)

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"SELECT *, {_COVER} FROM boxes {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    boxes = [dict(r) for r in rows]

    if q is not None:
        rank = {box_id: position for position, box_id in enumerate(matched)}
        boxes.sort(key=lambda b: rank[b["id"]])
    return boxes


def record_print(
    conn: sqlite3.Connection, code: str, *, actor: str | None = None
) -> dict[str, Any]:
    """Note that a label was printed.

    The count increments rather than resets: labels get lost and boxes get
    re-taped, and knowing a label was printed three times explains why more
    than one label with the same code is in circulation.
    """
    box = _require(conn, code)
    conn.execute(
        """
        UPDATE boxes
           SET label_print_count = label_print_count + 1,
               label_printed_at  = datetime('now')
         WHERE id = ?
        """,
        (box["id"],),
    )
    _record(conn, box["id"], "print", to_value=code, actor=actor)
    return get_box(conn, code)


def code_of(conn: sqlite3.Connection, box_id: int) -> str | None:
    """The code of a box by row id. Callers that only hold an id need it to
    say *which box* changed, since a code is what a client re-fetches by."""
    row = conn.execute("SELECT code FROM boxes WHERE id = ?", (box_id,)).fetchone()
    return row["code"] if row else None


def room_name(conn: sqlite3.Connection, room_id: int | None) -> str | None:
    if room_id is None:
        return None
    row = conn.execute("SELECT name FROM rooms WHERE id = ?", (room_id,)).fetchone()
    return row["name"] if row else None


# --- items ---------------------------------------------------------------


def add_item(conn: sqlite3.Connection, code: str, *, name: str, **fields) -> dict[str, Any]:
    box = _require(conn, code)
    columns = ["box_id", "name", *fields]
    placeholders = ", ".join("?" * len(columns))
    cursor = conn.execute(
        f"INSERT INTO items ({', '.join(columns)}) VALUES ({placeholders})",
        (box["id"], name, *fields.values()),
    )
    search.reindex_box(conn, box["id"])
    return _row(conn.execute("SELECT * FROM items WHERE id = ?", (cursor.lastrowid,)).fetchone())


def list_items(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    box = _require(conn, code)
    rows = conn.execute("SELECT * FROM items WHERE box_id = ? ORDER BY id", (box["id"],))
    return [dict(r) for r in rows]


def code_for_item(conn: sqlite3.Connection, item_id: int) -> str | None:
    """The code of the box an item belongs to, or None if there is no such item."""
    row = conn.execute(
        "SELECT boxes.code FROM items JOIN boxes ON boxes.id = items.box_id WHERE items.id = ?",
        (item_id,),
    ).fetchone()
    return row["code"] if row else None


def delete_item(conn: sqlite3.Connection, item_id: int) -> bool:
    row = conn.execute("SELECT box_id FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return False
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    search.reindex_box(conn, row["box_id"])
    return True


# --- events --------------------------------------------------------------


def events_for(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    box = _require(conn, code)
    rows = conn.execute(
        "SELECT * FROM events WHERE box_id = ? ORDER BY id", (box["id"],)
    ).fetchall()
    return [dict(r) for r in rows]
