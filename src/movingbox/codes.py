"""Box code format: prefix, separator and number length.

``CAM-001``, ``D001``, ``Z06-001``, ``B-0001`` are all expressible.

The format lives in the ``settings`` table, not in the environment. Codes
belong to the data: a database restored onto another machine has to keep
issuing codes that match the labels already stuck to boxes, and an env var
would not travel with it.

Each prefix carries its own counter, so switching to ``CAM`` starts at
``CAM-001`` rather than continuing some global count -- while switching *back*
to a previous prefix resumes where it left off, because a reused code would
point an already-printed label at a different box.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

DEFAULT_PREFIX = "B"
DEFAULT_SEPARATOR = "-"
DEFAULT_DIGITS = 4

MAX_DIGITS = 12
#: Letters and digits only: the code goes in a URL path and is read back off a
#: label, so 'Z06' is fine but punctuation is not.
PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9]{1,12}$")
#: A separator must survive being a URL path segment and being read off tape.
#: A slash would split the segment the scanner reads; a space would break the
#: URL entirely.
ALLOWED_SEPARATORS = {"", "-", "_", "."}


def _setting(conn: sqlite3.Connection, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else default


def get_format(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "prefix": _setting(conn, "code_prefix", DEFAULT_PREFIX),
        "separator": _setting(conn, "code_separator", DEFAULT_SEPARATOR),
        "digits": int(_setting(conn, "code_digits", str(DEFAULT_DIGITS))),
    }


def validate(prefix: str, separator: str, digits: int) -> None:
    if not PREFIX_PATTERN.match(prefix or ""):
        raise ValueError(f"prefix {prefix!r} must be 1-12 letters or digits, e.g. B, CAM, Z06")
    if separator not in ALLOWED_SEPARATORS:
        allowed = ", ".join(repr(s) for s in sorted(ALLOWED_SEPARATORS))
        raise ValueError(f"separator {separator!r} must be one of {allowed}")
    if not 1 <= int(digits) <= MAX_DIGITS:
        raise ValueError(f"digits must be between 1 and {MAX_DIGITS}, not {digits}")


def set_format(
    conn: sqlite3.Connection, *, prefix: str, separator: str, digits: int
) -> dict[str, Any]:
    """Set the format for codes issued from now on. Existing codes are untouched."""
    validate(prefix, separator, digits)
    for key, value in (
        ("code_prefix", prefix),
        ("code_separator", separator),
        ("code_digits", str(int(digits))),
    ):
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    return get_format(conn)


def counter_name(prefix: str) -> str:
    return f"box_code:{prefix}"


def set_sequence(conn: sqlite3.Connection, number: int) -> int:
    """Make the next code issued for the current prefix use ``number``."""
    if number < 1:
        raise ValueError(f"the next number must be 1 or more, not {number}")
    name = counter_name(get_format(conn)["prefix"])
    # The counter holds the last-issued value, so store one below the next.
    conn.execute(
        "INSERT INTO counters (name, value) VALUES (?, ?) "
        "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
        (name, number - 1),
    )
    return number


def render(number: int, *, prefix: str, separator: str, digits: int) -> str:
    """Format one number. Padding is a minimum, never a ceiling.

    Box 1000 in a 3-digit format is ``1000``, not ``000``.
    """
    return f"{prefix}{separator}{number:0{digits}d}"
