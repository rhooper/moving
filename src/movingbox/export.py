"""Exports, so the data outlives the application.

Rooms are written as names, not ids, and items are nested inside their box. An
export that still needs the database to be interpretable is not an export.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

CSV_COLUMNS = [
    "code",
    "kind",
    "size",
    "destination_room",
    "status",
    "current_location",
    "source_location",
    "content_summary",
    "items",
    "fragile",
    "open_first",
    "heavy",
    "weight_kg",
    "group_index",
    "group_total",
    "photo_count",
    "notes",
    "created_at",
    "sealed_at",
]

#: Boxes with no destination still have to appear, or they go missing from the
#: count the movers are working to.
UNASSIGNED = "Unassigned"


def _boxes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT b.*,
               dest.name AS destination_room,
               src.name  AS source_room
          FROM boxes b
          LEFT JOIN rooms dest ON dest.id = b.destination_room_id
          LEFT JOIN rooms src  ON src.id  = b.source_room_id
         WHERE b.deleted_at IS NULL
         ORDER BY b.id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _children(conn: sqlite3.Connection, table: str, box_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(f"SELECT * FROM {table} WHERE box_id = ? ORDER BY id", (box_id,))
    return [dict(row) for row in rows]


def snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """The whole database as plain nested data."""
    boxes = []
    for box in _boxes(conn):
        box_id = box.pop("id")
        box["items"] = _children(conn, "items", box_id)
        box["photos"] = _children(conn, "photos", box_id)
        box["events"] = _children(conn, "events", box_id)
        boxes.append(box)

    return {
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "box_count": len(boxes),
        "rooms": [dict(r) for r in conn.execute("SELECT * FROM rooms ORDER BY sort_order, name")],
        "boxes": boxes,
    }


def to_json(conn: sqlite3.Connection) -> str:
    return json.dumps(snapshot(conn), indent=2, ensure_ascii=False)


def _item_summary(items: list[dict[str, Any]]) -> str:
    """Items as one readable cell: 'cafetiere x2; kettle'."""
    parts = []
    for item in items:
        qty = item.get("qty") or 1
        parts.append(f"{item['name']} x{qty}" if qty > 1 else item["name"])
    return "; ".join(parts)


def to_csv(conn: sqlite3.Connection) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()

    for box in snapshot(conn)["boxes"]:
        writer.writerow(
            {
                **box,
                "items": _item_summary(box["items"]),
                "photo_count": len(box["photos"]),
            }
        )
    return buffer.getvalue()


def manifest(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Box counts and known weight per destination room, for the movers."""
    groups: dict[str, dict[str, Any]] = {}
    for box in _boxes(conn):
        room = box["destination_room"] or UNASSIGNED
        group = groups.setdefault(
            room, {"room": room, "count": 0, "weight_kg": 0.0, "unweighed": 0, "codes": []}
        )
        group["count"] += 1
        group["codes"].append(box["code"])
        if box["weight_kg"]:
            group["weight_kg"] += box["weight_kg"]
        else:
            group["unweighed"] += 1

    # Unassigned last: it is a to-do list, not a destination.
    return sorted(groups.values(), key=lambda g: (g["room"] == UNASSIGNED, g["room"]))


def manifest_totals(groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Totals across a manifest, including how much of it is unknown.

    ``weight_kg`` is the sum of *known* weights. Reporting it without
    ``unweighed`` invites someone to load-plan from a number that silently
    omits most of the boxes.
    """
    return {
        "boxes": sum(g["count"] for g in groups),
        "weight_kg": round(sum(g["weight_kg"] for g in groups), 1),
        "unweighed": sum(g["unweighed"] for g in groups),
    }
