"""Full-text search across boxes and everything hanging off them.

Call :func:`reindex_box` after any mutation touching a box, its items or its
photos. There are no triggers: the indexed text spans four tables.
"""

from __future__ import annotations

import re
import sqlite3

# One box's index row, from the box, its items, photos and rooms.
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
WHERE b.id = ? AND b.deleted_at IS NULL
"""

_COLUMNS = "code, summary, notes, items, photo_captions, rooms, location"

# FTS5 treats -, ", *, NEAR, OR and friends as syntax, so user text is split
# into words and each is quoted: 'B-0042' must match, not raise.
_WORD = re.compile(r"\w+", re.UNICODE)


def _match_expression(query: str) -> str | None:
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Returns ``None`` when the input contains nothing searchable.
    """
    words = _WORD.findall(query)
    if not words:
        return None
    # Quoted, so a literal, never an operator; the last word matches as a prefix.
    quoted = [f'"{word}"' for word in words[:-1]]
    quoted.append(f'"{words[-1]}"*')
    return " AND ".join(quoted)


def reindex_box(conn: sqlite3.Connection, box_id: int) -> None:
    """Refresh one box's index row. Safe to call for a box that no longer exists."""
    conn.execute("DELETE FROM box_fts WHERE rowid = ?", (box_id,))
    row = conn.execute(_GATHER, (box_id,)).fetchone()
    if row is None:
        # Gone or binned; a restore reindexes it.
        return
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
