"""Box code format: prefix, separator and number length (``CAM-001``, ``D001``, ``B-0001``).

Stored in the database, so a restored database keeps issuing codes that match
existing labels. Each prefix has its own counter, so returning to an old prefix
resumes it and never reuses a code.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

DEFAULT_PREFIX = "B"
DEFAULT_SEPARATOR = "-"
DEFAULT_DIGITS = 4

MAX_DIGITS = 12
#: Letters and digits only: the code goes in a URL path and is read off a label.
PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9]{1,12}$")
#: Safe in a single URL path segment.
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


def kind_prefix(conn: sqlite3.Connection, kind: str) -> str:
    """The prefix for a kind, falling back to the global one."""
    return _setting(conn, f"code_prefix:{kind}", "") or get_format(conn)["prefix"]


def set_kind_prefix(conn: sqlite3.Connection, kind: str, prefix: str | None) -> None:
    """Give one kind its own prefix, or pass None to return it to the global one."""
    if prefix:
        validate(prefix, get_format(conn)["separator"], get_format(conn)["digits"])
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (f"code_prefix:{kind}", prefix),
        )
    else:
        conn.execute("DELETE FROM settings WHERE key = ?", (f"code_prefix:{kind}",))


def kind_prefixes(conn: sqlite3.Connection) -> dict[str, str]:
    """Only the kinds that have been given a prefix of their own."""
    rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'code_prefix:%'").fetchall()
    return {row["key"].split(":", 1)[1]: row["value"] for row in rows}


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
    """Format one number. Padding is a minimum: 1000 in a 3-digit format is ``1000``."""
    return f"{prefix}{separator}{number:0{digits}d}"
