"""Preferences that describe how this move is being done.

In the database with the code format, and for the same reason: they belong to
the data, not to the machine it happens to be running on.
"""

from __future__ import annotations

import sqlite3

from . import kinds

#: Matches what a print request may ask for. Ten is already a lot of tape.
MAX_LABEL_COPIES = 10


def label_copies(conn: sqlite3.Connection, kind: str) -> int:
    """How many copies of a full label print for this kind, when nobody says.

    Per kind, because one number cannot be right for a crate and a lamp: the
    things that get stacked want a label on more than one face, the rest want
    one. kinds.py holds the defaults; a row here overrides one. (There was a
    single global number for two days. Nobody ever set it.)
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
