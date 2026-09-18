"""Upgrading a database that predates per-prefix code counters.

The live database had a single 'box_code' counter at 3 with B-0001..B-0003
already printed onto tape. Without the migration the new lookup for
'box_code:B' finds nothing, restarts at 1, and the next box tries to claim
B-0001 -- a code that is already stuck to a different box.
"""

import sqlite3
from contextlib import closing

import pytest

from movingbox import db, store

from .schema_history import roll_back_to


def a_v1_database(path):
    """A database as it looked before 0002: one global counter, no `kind`.

    Every later migration has to be undone, not just 0002. Setting
    user_version back to 1 makes the runner replay all of them, and a replayed
    0003 fails with "duplicate column name: kind" if its column is still
    there — which is a fault in this helper, not in the migration.
    """
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
