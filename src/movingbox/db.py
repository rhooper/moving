"""SQLite connection handling and the migration runner.

Migrations are numbered ``NNNN_name.sql`` files in ``migrations/``, applied in
order against ``PRAGMA user_version``. There is no Alembic: the schema is small
and the database has a single writer.

**Never edit a migration that has been applied** -- add a new one.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _migration_files() -> list[tuple[int, Path]]:
    """Every migration as ``(version, path)``, ordered by version."""
    found = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        prefix = path.name.split("_", 1)[0]
        if not prefix.isdigit():
            raise ValueError(f"Migration {path.name} must start with a number, e.g. 0001_init.sql")
        found.append((int(prefix), path))
    found.sort()
    versions = [v for v, _ in found]
    if len(set(versions)) != len(versions):
        raise ValueError(f"Duplicate migration version among {versions}")
    return found


SCHEMA_VERSION = max((version for version, _ in _migration_files()), default=0)


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations. Returns the resulting schema version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, path in _migration_files():
        if version <= current:
            continue
        conn.executescript(path.read_text())
        # PRAGMA cannot be parameterised; version is an int parsed from the filename.
        conn.execute(f"PRAGMA user_version = {version:d}")
        current = version
    return current


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) a migrated database with foreign keys enforced."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None -> autocommit; executescript and the PRAGMAs below
    # both require not being inside an implicit transaction.
    #
    # check_same_thread=False because a single HTTP request does not stay on
    # one thread. FastAPI runs a sync generator dependency's __enter__ through
    # run_in_threadpool, the endpoint body through the threadpool again, and
    # __exit__ under a *separate* CapacityLimiter, so opening, using and
    # closing a connection can happen on three different workers. The default
    # check rejects that, which took the live service down with 500s the
    # moment the phone issued its four parallel requests.
    #
    # This is safe here, not merely convenient: each request gets its own
    # connection (never shared between requests), and the three phases are
    # awaited in order, so no connection is ever touched by two threads at
    # once -- which is all SQLite's multi-thread mode requires.
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def next_box_code(conn: sqlite3.Connection) -> str:
    """Allocate the next never-before-used box code, e.g. ``B-0042``.

    Codes come from a monotonic counter rather than ``boxes.id`` so that
    deleting a box does not free its code: a reused code would send an
    already-printed label to the wrong box.
    """
    row = conn.execute(
        """
        INSERT INTO counters (name, value) VALUES ('box_code', 1)
            ON CONFLICT(name) DO UPDATE SET value = value + 1
            RETURNING value
        """
    ).fetchone()
    return f"B-{row[0]:04d}"
