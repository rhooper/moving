"""Migration 0011: photo ids are never reused.

Tested from a database at version 10 with photos in it, the path the live
database takes.
"""

import sqlite3

import pytest

from movingbox import db

from .schema_history import roll_back_to


@pytest.fixture
def old(config):
    conn = db.connect(config.db_path)
    roll_back_to(conn, 10)
    conn.execute("INSERT INTO boxes (code) VALUES ('B-0001')")
    for n, primary in ((1, 1), (2, 0), (3, 0)):
        conn.execute(
            "INSERT INTO photos (box_id, filename, sha256, is_primary, caption)"
            " VALUES (1, ?, ?, ?, ?)",
            (f"p{n}.jpg", f"{n}" * 64, primary, f"shot {n}"),
        )
    conn.execute("DELETE FROM photos WHERE id = 3")
    conn.close()
    return config


def add(conn, n):
    return conn.execute(
        "INSERT INTO photos (box_id, filename, sha256) VALUES (1, ?, ?)",
        (f"p{n}.jpg", f"{n}" * 64),
    ).lastrowid


def test_rows_and_ids_survive(old):
    conn = db.connect(old.db_path)
    try:
        rows = conn.execute("SELECT id, filename, is_primary, caption FROM photos ORDER BY id")
        assert [tuple(r) for r in rows] == [
            (1, "p1.jpg", 1, "shot 1"),
            (2, "p2.jpg", 0, "shot 2"),
        ]
    finally:
        conn.close()


def test_after_it_a_deleted_newest_id_is_never_handed_out_again(old):
    conn = db.connect(old.db_path)
    try:
        # 3 was deleted before the migration, which cannot know it had existed;
        # the `k` in photo URLs covers ids reused that way.
        assert add(conn, 4) == 3
        conn.execute("DELETE FROM photos WHERE id = 3")

        assert add(conn, 5) == 4
    finally:
        conn.close()


def test_the_indexes_are_back(old):
    conn = db.connect(old.db_path)
    try:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'photos'"
            )
        }
        assert {"idx_photos_box", "idx_photos_one_cover"} <= names
        # One cover per box still holds.
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            conn.execute(
                "INSERT INTO photos (box_id, filename, sha256, is_primary)"
                " VALUES (1, 'x.jpg', ?, 1)",
                ("9" * 64,),
            )
    finally:
        conn.close()
