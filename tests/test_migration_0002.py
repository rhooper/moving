"""Upgrading a database that predates per-prefix code counters.

Before 0002 there was one global 'box_code' counter. Without the migration the
lookup for 'box_code:B' finds nothing, restarts at 1, and the next box claims a
code already stuck to a different box.
"""

import sqlite3
from contextlib import closing

import pytest

from movingbox import db, store

from .schema_history import roll_back_to


def a_v1_database(path):
    """A database as it looked before 0002: one global counter, no `kind`."""
    conn = db.connect(path)
    for _ in range(3):
        store.create_box(conn)
    roll_back_to(conn, 1)
    conn.close()


def test_the_old_counter_is_carried_over(tmp_path):
    path = tmp_path / "old.db"
    a_v1_database(path)

    with closing(db.connect(path)) as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM counters")]

    assert names == ["box_code:B"]


def test_the_next_box_does_not_reuse_a_printed_code(tmp_path):
    path = tmp_path / "old.db"
    a_v1_database(path)

    with closing(db.connect(path)) as conn:
        assert store.create_box(conn)["code"] == "B-0004"


def test_creating_a_box_after_upgrading_does_not_raise(tmp_path):
    # Without the migration this is an IntegrityError on the UNIQUE code.
    path = tmp_path / "old.db"
    a_v1_database(path)

    with closing(db.connect(path)) as conn:
        try:
            store.create_box(conn)
        except sqlite3.IntegrityError as clash:  # pragma: no cover
            pytest.fail(f"code collision after upgrade: {clash}")


def test_a_fresh_database_is_unaffected(tmp_path):
    with closing(db.connect(tmp_path / "fresh.db")) as conn:
        assert store.create_box(conn)["code"] == "B-0001"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
