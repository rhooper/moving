"""Full-text search across boxes and everything hanging off them.

The FTS index is maintained by calling :func:`reindex_box` after any mutation
that touches a box, its items or its photos -- deliberately not by SQL triggers.
The indexed text is assembled from four tables, and a plain function is far
easier to test and to reason about than a web of triggers.
"""

from __future__ import annotations

import re
import sqlite3

# The row for a box, assembled from the box itself plus its items, photos and
# rooms. Kept as one query so reindexing a box is a single round trip.
_GATHER = """
SELECT
    b.code,
    coalesce(b.content_summary, ''),
    coalesce(b.notes, ''),
    coalesce((SELECT group_concat(i.name, ' ')    FROM items  i WHERE i.box_id  = b.id), ''),
    coalesce((SELECT group_concat(p.caption, ' ') FROM photos p WHERE p.box_id  = b.id), ''),
    coalesce((SELECT group_concat(r.name, ' ')    FROM rooms  r
              WHERE r.id IN (b.destination_room_id, b.source_room_id)), ''),
    coalesce(b.current_location, '') || ' ' || coalesce(b.source_location, '')
FROM boxes b
WHERE b.id = ?
"""

_COLUMNS = "code, summary, notes, items, photo_captions, rooms, location"

# FTS5 treats -, ", *, NEAR, OR and friends as syntax. Everything a user types
# goes through this, so a scanned 'B-0042' or a typed 'pots & pans' is matched
# as literal text rather than blowing up or silently matching nothing.
_WORD = re.compile(r"\w+", re.UNICODE)


def _match_expression(query: str) -> str | None:
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Returns ``None`` when the input contains nothing searchable.
    """
    words = _WORD.findall(query)
    if not words:
        return None
    # Every word quoted (so it is a literal, never an operator); the final word
    # gets a prefix match so search feels responsive as you type.
    quoted = [f'"{word}"' for word in words[:-1]]
    quoted.append(f'"{words[-1]}"*')
    return " AND ".join(quoted)


def reindex_box(conn: sqlite3.Connection, box_id: int) -> None:
    """Refresh one box's index row. Safe to call for a box that no longer exists."""
    conn.execute("DELETE FROM box_fts WHERE rowid = ?", (box_id,))
    row = conn.execute(_GATHER, (box_id,)).fetchone()
    if row is None:
        return  # deleted; removing the stale row above is the whole job
    placeholders = ", ".join("?" * (len(_COLUMNS.split(", ")) + 1))
    conn.execute(
        f"INSERT INTO box_fts (rowid, {_COLUMNS}) VALUES ({placeholders})",
        (box_id, *row),
    )


def reindex_all(conn: sqlite3.Connection) -> int:
    """Rebuild the entire index from scratch. Returns the number of boxes indexed."""
    conn.execute("DELETE FROM box_fts")
    box_ids = [r[0] for r in conn.execute("SELECT id FROM boxes ORDER BY id")]
    for box_id in box_ids:
        reindex_box(conn, box_id)
    return len(box_ids)


def search(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[int]:
    """Box ids matching ``query``, best match first."""
    expression = _match_expression(query)
    if expression is None:
        return []
    rows = conn.execute(
        "SELECT rowid FROM box_fts WHERE box_fts MATCH ? ORDER BY rank LIMIT ?",
        (expression, limit),
    ).fetchall()
    return [row[0] for row in rows]
