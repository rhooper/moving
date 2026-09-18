"""Preferences that describe how this move is being done.

In the database with the code format, and for the same reason: they belong to
the data, not to the machine it happens to be running on.
"""

from __future__ import annotations

import sqlite3

#: A box usually wants a label on more than one face.
DEFAULT_LABEL_COPIES = 2
#: Matches what a print request may ask for. Ten is already a lot of tape.
MAX_LABEL_COPIES = 10


def label_copies(conn: sqlite3.Connection) -> int:
    """How many copies of a full label print when the request does not say."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'label_copies'").fetchone()
    try:
        copies = int(row["value"]) if row is not None else DEFAULT_LABEL_COPIES
    except ValueError:
        return DEFAULT_LABEL_COPIES
    return min(max(copies, 1), MAX_LABEL_COPIES)


def set_label_copies(conn: sqlite3.Connection, copies: int) -> int:
    if not 1 <= copies <= MAX_LABEL_COPIES:
        raise ValueError(f"copies must be between 1 and {MAX_LABEL_COPIES}")
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('label_copies', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(copies),),
    )
    return copies
