"""Preferences for this move, stored in the database with the code format."""

from __future__ import annotations

import sqlite3

from . import kinds

#: Matches what a print request may ask for.
MAX_LABEL_COPIES = 10


def label_copies(conn: sqlite3.Connection, kind: str) -> int:
    """How many copies of a full label print for this kind, when nobody says.

    kinds.py holds the defaults; a settings row overrides one.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (f"label_copies:{kind}",)
    ).fetchone()
    try:
        copies = int(row["value"]) if row is not None else kinds.default_copies(kind)
    except ValueError:
        return kinds.default_copies(kind)
    return min(max(copies, 1), MAX_LABEL_COPIES)


def set_label_copies(conn: sqlite3.Connection, kind: str, copies: int) -> int:
    kinds.check(kind)
    if not 1 <= copies <= MAX_LABEL_COPIES:
        raise ValueError(f"copies must be between 1 and {MAX_LABEL_COPIES}")
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (f"label_copies:{kind}", str(copies)),
    )
    return copies
