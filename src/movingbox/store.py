"""Box lifecycle operations.

Every function that changes a box keeps the search index and the event log in
step, so callers never have to. Nothing here publishes change events: the CLI
shares this module and cannot reach a connected phone.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from . import db, kinds, renditions, search

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime
    from .config import Config

STATUSES = ("open", "packed", "loaded", "delivered", "unpacked")

# Columns a caller may set directly. Status and location are excluded: they go
# through set_status/set_location so the transition is always recorded.
EDITABLE = (
    "kind",
    "destination_room_id",
    "source_room_id",
    "source_location",
    "content_summary",
    "size",
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


class NotDeleted(ValueError):
    """Purging applies only to something already in the bin."""


class NotEmpty(ValueError):
    """A container with things still inside it cannot be deleted: they would be stranded."""


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


def create_box(
    conn: sqlite3.Connection,
    *,
    actor: str | None = None,
    parent_code: str | None = None,
    **fields,
) -> dict[str, Any]:
    """Create a box with the next unused code, indexed and logged.

    `parent_code` is checked first, so a refused parent does not use up a code.
    """
    parent_id = _parent_for(conn, None, parent_code) if parent_code is not None else None
    if parent_id is not None:
        fields = {**fields, "parent_id": parent_id}
    unknown = set(fields) - set(EDITABLE) - {"parent_id"}
    if unknown:
        raise ValueError(f"Not settable at creation: {sorted(unknown)}")

    kinds.check(fields.get("kind", kinds.DEFAULT))
    kinds.check_size(fields.get("size"), fields.get("kind", kinds.DEFAULT))
    code = db.next_box_code(conn, kind=fields.get("kind", kinds.DEFAULT))
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


def get_box(
    conn: sqlite3.Connection, code: str, *, include_deleted: bool = False
) -> dict[str, Any] | None:
    """The record, or None. Binned records count as gone unless `include_deleted`."""
    clause = "" if include_deleted else " AND deleted_at IS NULL"
    return _row(conn.execute(f"SELECT * FROM boxes WHERE code = ?{clause}", (code,)).fetchone())


def update_box(conn: sqlite3.Connection, code: str, **fields) -> dict[str, Any]:
    box = _require(conn, code)
    unknown = set(fields) - set(EDITABLE)
    if unknown:
        raise ValueError(f"Not editable: {sorted(unknown)} (status and location have own calls)")
    if "kind" in fields:
        kinds.check(fields["kind"])
    becoming = fields.get("kind", box["kind"])
    if not kinds.holds_contents(becoming) and children_of(conn, code):
        raise ValueError(
            f"{code} has things inside it, so it has to stay a container. Move them out first."
        )
    if "size" in fields:
        kinds.check_size(fields["size"], becoming)
    elif not kinds.holds_contents(becoming) and box["size"] is not None:
        # A single thing has no size, and its page no longer shows the picker.
        fields = {**fields, "size": None}
    if fields:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        # A summary written here is a person's; photo analysis will not
        # rewrite it (an empty one is anyone's to fill -- see analysis.py).
        if "content_summary" in fields:
            assignments += ", summary_source = 'manual'"
        conn.execute(
            f"UPDATE boxes SET {assignments}, updated_at = datetime('now') WHERE id = ?",
            (*fields.values(), box["id"]),
        )
        search.reindex_box(conn, box["id"])
    return get_box(conn, code)


def delete_box(
    conn: sqlite3.Connection, config: Config, code: str, *, actor: str | None = None
) -> bool:
    """Put a record in the bin. Reversible; destroys nothing.

    `config` is unused; it keeps the signature the same as `purge_box`.
    """
    box = get_box(conn, code)
    if box is None:
        return False
    inside = children_of(conn, code)
    if inside:
        raise NotEmpty(
            f"{code} has {len(inside)} thing{'' if len(inside) == 1 else 's'} inside it "
            f"({', '.join(c['code'] for c in inside[:5])}{'...' if len(inside) > 5 else ''}). "
            f"Move them out or delete them first."
        )

    conn.execute(
        "UPDATE boxes SET deleted_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
        (box["id"],),
    )
    # reindex_box sees the row as deleted and drops it from search.
    search.reindex_box(conn, box["id"])
    _record(conn, box["id"], "delete", to_value=code, actor=actor)
    return True


def restore_box(
    conn: sqlite3.Connection, code: str, *, actor: str | None = None
) -> dict[str, Any] | None:
    """Take a record back out of the bin."""
    box = get_box(conn, code, include_deleted=True)
    if box is None:
        return None
    if box["deleted_at"] is not None:
        conn.execute(
            "UPDATE boxes SET deleted_at = NULL, updated_at = datetime('now') WHERE id = ?",
            (box["id"],),
        )
        search.reindex_box(conn, box["id"])
        _record(conn, box["id"], "restore", to_value=code, actor=actor)
    return get_box(conn, code)


def deleted_boxes(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    """What is in the bin, most recently deleted first."""
    rows = conn.execute(
        "SELECT * FROM boxes WHERE deleted_at IS NOT NULL "
        "ORDER BY deleted_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def purge_box(conn: sqlite3.Connection, config: Config, code: str) -> bool:
    """Destroy a record for good, with its contents, events and photo files.

    Only for something already binned. The photo rows cascade; the files on
    disk are removed here, since nothing would point at them afterwards.
    """
    box = get_box(conn, code, include_deleted=True)
    if box is None:
        return False
    if box["deleted_at"] is None:
        raise NotDeleted(f"{code} has not been deleted; delete it before purging")

    # Collect the filenames first: after the delete the rows are gone.
    doomed = conn.execute(
        "SELECT filename, thumb_filename FROM photos WHERE box_id = ?", (box["id"],)
    ).fetchall()

    conn.execute("DELETE FROM boxes WHERE id = ?", (box["id"],))
    search.reindex_box(conn, box["id"])  # clears the now-orphaned index row

    # After the row is gone, so a file already missing cannot leave the record behind.
    for row in doomed:
        for name in (row["filename"], row["thumb_filename"]):
            if name:
                (config.photo_dir / name).unlink(missing_ok=True)
        # Strips are not in the table; only their full image's name finds them.
        for strip in renditions.strip_files(config.photo_dir, row["filename"]):
            strip.unlink(missing_ok=True)
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


#: The photo a list row is drawn with, in the same statement as the rows. The
#: predicate matches the partial index idx_photos_one_cover, so it is one index
#: seek per returned row.
_COVER = """(
    SELECT id FROM photos
     WHERE photos.box_id = boxes.id AND photos.is_primary = 1
) AS cover_photo_id"""

#: How many things are directly inside, not counting anything in the bin. An
#: index seek per row (idx_boxes_parent), bounded by the page size.
_INSIDE = """(
    SELECT COUNT(*) FROM boxes AS inner_box
     WHERE inner_box.parent_id = boxes.id AND inner_box.deleted_at IS NULL
) AS child_count"""

#: The code of what it is inside, so a search result can say where it is.
_PARENT = """(
    SELECT outer_box.code FROM boxes AS outer_box WHERE outer_box.id = boxes.parent_id
) AS parent_code"""


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
    where, params = ["deleted_at IS NULL"], []

    if q is not None:
        matched = search.search(conn, q, limit=1000)
        if not matched:
            return []
        where.append(f"id IN ({', '.join('?' * len(matched))})")
        params.extend(matched)
    else:
        # Browsing shows the top level only; searching looks everywhere.
        where.append("parent_id IS NULL")
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

    clause = f"WHERE {' AND '.join(where)}"
    rows = conn.execute(
        f"SELECT *, {_COVER}, {_INSIDE}, {_PARENT} FROM boxes {clause} "
        f"ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    boxes = [dict(r) for r in rows]

    if q is not None:
        rank = {box_id: position for position, box_id in enumerate(matched)}
        boxes.sort(key=lambda b: rank[b["id"]])
    return boxes


def record_print(
    conn: sqlite3.Connection, code: str, *, copies: int = 1, actor: str | None = None
) -> dict[str, Any]:
    """Note that a label was printed. Counts labels in circulation, not button presses."""
    box = _require(conn, code)
    conn.execute(
        """
        UPDATE boxes
           SET label_print_count = label_print_count + ?,
               label_printed_at  = datetime('now')
         WHERE id = ?
        """,
        (copies, box["id"]),
    )
    _record(conn, box["id"], "print", to_value=code, actor=actor)
    return get_box(conn, code)


def code_of(conn: sqlite3.Connection, box_id: int) -> str | None:
    """The code of a box by row id."""
    row = conn.execute("SELECT code FROM boxes WHERE id = ?", (box_id,)).fetchone()
    return row["code"] if row else None


def has_contents(conn: sqlite3.Connection, code: str) -> bool:
    """Whether the record says enough about itself to be worth a label.

    A container needs a summary, items or something nested inside it; a single
    thing needs a name, and its items are ignored.
    """
    box = get_box(conn, code)
    if box is None:
        return False
    named = bool((box.get("content_summary") or "").strip())
    if not kinds.holds_contents(box.get("kind") or kinds.DEFAULT):
        return named
    return named or bool(list_items(conn, code)) or bool(children_of(conn, code))


def going_to(conn: sqlite3.Connection, box: dict[str, Any]) -> int | None:
    """The room a record is going to, as a label should say it.

    The nearest container with a room decides, over the record's own, which is
    kept for when it is taken out.
    """
    for step in reversed(path_to(conn, box["code"])):
        if step["destination_room_id"] is not None:
            return step["destination_room_id"]
    return box["destination_room_id"]


def room_name(conn: sqlite3.Connection, room_id: int | None) -> str | None:
    if room_id is None:
        return None
    row = conn.execute("SELECT name FROM rooms WHERE id = ?", (room_id,)).fetchone()
    return row["name"] if row else None


# --- nesting ---------------------------------------------------------------
#
# A record may be inside one other record. `parent_id` has no foreign key (see
# migration 0009), so everything that keeps it honest is here.


def _parent_for(conn: sqlite3.Connection, code: str | None, parent_code: str | None) -> int | None:
    """The id to store for `parent_code`, or raise ValueError saying why not.

    `code` is the record being moved, or None for one that does not exist yet.
    """
    if parent_code is None:
        return None
    parent = get_box(conn, parent_code)
    if parent is None:
        raise ValueError(f"there is no {parent_code} to put it inside")
    if not kinds.holds_contents(parent["kind"] or kinds.DEFAULT):
        raise ValueError(
            f"{parent_code} is a {kinds.label_for(parent['kind']).lower()}, not a container: "
            f"nothing can go inside it"
        )
    if code is not None:
        if parent_code == code:
            raise ValueError(f"{code} cannot go inside itself")
        # Refuse a cycle: the would-be parent must not already be inside `code`.
        if code in [step["code"] for step in [*path_to(conn, parent_code), parent]]:
            raise ValueError(
                f"{parent_code} is already inside {code}, so {code} cannot go inside it"
            )
    return parent["id"]


def set_parent(conn: sqlite3.Connection, code: str, parent_code: str | None) -> dict[str, Any]:
    """Put a record inside a container, or (with None) take it out."""
    box = _require(conn, code)
    parent_id = _parent_for(conn, code, parent_code)
    conn.execute(
        "UPDATE boxes SET parent_id = ?, updated_at = datetime('now') WHERE id = ?",
        (parent_id, box["id"]),
    )
    return get_box(conn, code)


def children_of(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    """What is directly inside, oldest first, not binned, shaped like list rows."""
    box = get_box(conn, code, include_deleted=True)
    if box is None:
        return []
    rows = conn.execute(
        f"SELECT *, {_COVER}, {_INSIDE}, {_PARENT} FROM boxes "
        f"WHERE parent_id = ? AND deleted_at IS NULL ORDER BY id",
        (box["id"],),
    ).fetchall()
    return [dict(r) for r in rows]


#: How deep a walk of nested records goes: a guard against a cycle in the data,
#: which the store already refuses to create.
MAX_DEPTH = 6


def subtree(
    conn: sqlite3.Connection, code: str, *, max_depth: int = MAX_DEPTH
) -> list[dict[str, Any]]:
    """A record and everything nested inside it, with the items of each.

    Nearest first (the record at depth 0), the order a summary spends its room
    in. Two queries whatever the shape of the tree.
    """
    root = conn.execute(
        "SELECT id FROM boxes WHERE code = ? AND deleted_at IS NULL", (code,)
    ).fetchone()
    if root is None:
        return []

    rows = conn.execute(
        """
        WITH RECURSIVE tree(id, depth) AS (
            SELECT id, 0 FROM boxes WHERE id = ?
             UNION ALL
            SELECT boxes.id, tree.depth + 1
              FROM boxes JOIN tree ON boxes.parent_id = tree.id
             WHERE boxes.deleted_at IS NULL AND tree.depth < ?
        )
        SELECT boxes.id, boxes.parent_id, boxes.code, boxes.kind, boxes.size,
               boxes.content_summary, boxes.summary_source, tree.depth
          FROM tree JOIN boxes ON boxes.id = tree.id
         ORDER BY tree.depth, boxes.id
        """,
        (root["id"], max_depth),
    ).fetchall()
    # The walk stops at a binned container, taking what is inside it too.

    nodes = [{**dict(row), "items": []} for row in rows]
    by_id = {node["id"]: node for node in nodes}

    placeholders = ", ".join("?" * len(by_id))
    for item in conn.execute(
        f"SELECT * FROM items WHERE box_id IN ({placeholders}) ORDER BY box_id, id",
        tuple(by_id),
    ):
        by_id[item["box_id"]]["items"].append(dict(item))

    return nodes


def path_to(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    """The containers a record is inside, outermost first. Empty at top level.

    Stops at a repeat, so a cycle in the data cannot hang a request.
    """
    box = get_box(conn, code, include_deleted=True)
    steps: list[dict[str, Any]] = []
    seen = set()
    while box is not None and box["parent_id"] is not None and box["parent_id"] not in seen:
        seen.add(box["parent_id"])
        row = conn.execute("SELECT * FROM boxes WHERE id = ?", (box["parent_id"],)).fetchone()
        box = dict(row) if row else None
        if box is not None:
            steps.append(box)
    return list(reversed(steps))


def search_with_containers(
    conn: sqlite3.Connection,
    q: str,
    *,
    limit: int = 100,
    max_depth: int = MAX_DEPTH,
) -> list[dict[str, Any]]:
    """Search results, then the containers they are inside, so the page can group them.

    Every row carries:

    ``matched``
        True for a result, False for a container included as context. A
        container that is also a result appears once, as a result.
    ``ancestry``
        The codes of the containers it is inside, outermost first.

    Matches come first, in relevance order. The walk up stops at a binned
    container, truncating the chain there.
    """
    matches = list_boxes(conn, q=q, limit=limit)
    if not matches:
        return []

    ids = [row["id"] for row in matches]
    placeholders = ", ".join("?" * len(ids))
    # UNION dedupes shared ancestors; `depth` still bounds a cycle, whose rows
    # differ in depth and so survive the dedupe.
    walked = conn.execute(
        f"""
        WITH RECURSIVE up(id, parent_id, depth) AS (
            SELECT id, parent_id, 0 FROM boxes WHERE id IN ({placeholders})
             UNION
            SELECT outer_box.id, outer_box.parent_id, up.depth + 1
              FROM boxes AS outer_box JOIN up ON outer_box.id = up.parent_id
             WHERE outer_box.deleted_at IS NULL AND up.depth < ?
        )
        SELECT up.id, up.parent_id, boxes.code
          FROM up JOIN boxes ON boxes.id = up.id
        """,
        (*ids, max_depth),
    ).fetchall()
    step = {row["id"]: (row["code"], row["parent_id"]) for row in walked}

    def ancestry(box_id: int) -> list[str]:
        chain: list[str] = []
        seen: set[int] = set()
        parent = step[box_id][1]
        while parent is not None and parent in step and parent not in seen:
            seen.add(parent)
            chain.append(step[parent][0])
            parent = step[parent][1]
        return list(reversed(chain))

    found = {row["id"] for row in matches}
    context = sorted(set(step) - found)
    rows: list[dict[str, Any]] = []
    if context:
        marks = ", ".join("?" * len(context))
        rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT *, {_COVER}, {_INSIDE}, {_PARENT} FROM boxes "
                f"WHERE id IN ({marks}) AND deleted_at IS NULL ORDER BY id",
                tuple(context),
            )
        ]

    return [
        {**row, "matched": row["id"] in found, "ancestry": ancestry(row["id"])}
        for row in (*matches, *rows)
    ]


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
    # Readable in the bin, to help decide whether to restore it.
    box = get_box(conn, code, include_deleted=True)
    if box is None:
        raise UnknownBox(code)
    rows = conn.execute("SELECT * FROM items WHERE box_id = ? ORDER BY id", (box["id"],))
    return [dict(r) for r in rows]


def code_for_item(conn: sqlite3.Connection, item_id: int) -> str | None:
    """The code of the box an item belongs to, or None if there is no such item."""
    row = conn.execute(
        "SELECT boxes.code FROM items JOIN boxes ON boxes.id = items.box_id WHERE items.id = ?",
        (item_id,),
    ).fetchone()
    return row["code"] if row else None


def update_item(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    name: str | None = None,
    qty: int | None = None,
    keep_source: bool = False,
) -> dict[str, Any] | None:
    """Rename or re-count an item. None if there is no such item.

    Any change makes the item a person's ('manual'), which photo analysis then
    leaves alone. `keep_source` is for the analysis merge raising its own count.
    """
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return None

    changes: dict[str, Any] = {}
    if name is not None:
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValueError("an item needs a name")
        changes["name"] = cleaned
    if qty is not None:
        changes["qty"] = qty
    if changes and not keep_source:
        changes["source"] = "manual"

    if changes:
        assignments = ", ".join(f"{column} = ?" for column in changes)
        conn.execute(f"UPDATE items SET {assignments} WHERE id = ?", (*changes.values(), item_id))
        search.reindex_box(conn, row["box_id"])
    return _row(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())


def delete_item(conn: sqlite3.Connection, item_id: int) -> bool:
    row = conn.execute("SELECT box_id FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return False
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    search.reindex_box(conn, row["box_id"])
    return True


# --- events --------------------------------------------------------------


def events_for(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    # Readable in the bin: the timeline records the deletion itself.
    box = get_box(conn, code, include_deleted=True)
    if box is None:
        raise UnknownBox(code)
    rows = conn.execute(
        "SELECT * FROM events WHERE box_id = ? ORDER BY id", (box["id"],)
    ).fetchall()
    return [dict(r) for r in rows]
