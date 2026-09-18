"""Upgrading a database whose photos predate a box having exactly one cover.

Before this migration ``is_primary`` was set on the first photo uploaded and
never touched again except by a delete, which promoted the lowest-numbered
survivor whether or not the deleted photo was the cover. A live database can
therefore hold boxes with two flagged photos and boxes with none -- both of
which the unique index added here would reject on its way in, so the migration
has to repair the rows before it can create the index.
"""

import io
import sqlite3
from contextlib import closing

import pytest
from PIL import Image

from movingbox import db, storage, store

from .schema_history import roll_back_to


def a_jpeg(colour) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (600, 400), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def messy(config, tmp_path):
    """A database as 0002 could leave it: one box with two covers, one with none."""
    conn = db.connect(config.db_path)
    two = store.create_box(conn, content_summary="two covers")["code"]
    none = store.create_box(conn, content_summary="no cover")["code"]
    photos = {
        "two": [
            storage.save_photo(conn, config, two, a_jpeg(shade), filename=f"{n}.jpg")["id"]
            for n, shade in enumerate([(1, 2, 3), (9, 9, 9)])
        ],
        "none": [
            storage.save_photo(conn, config, none, a_jpeg(shade), filename=f"{n}.jpg")["id"]
            for n, shade in enumerate([(30, 60, 90), (90, 60, 30)])
        ],
    }
    # Undo this migration first: recreating the broken state below means
    # putting two covers on one box, which the index it adds forbids.
    roll_back_to(conn, 3)
    conn.execute(
        "UPDATE photos SET is_primary = 1 WHERE box_id IN (SELECT id FROM boxes WHERE code = ?)",
        (two,),
    )
    conn.execute(
        "UPDATE photos SET is_primary = 0 WHERE box_id IN (SELECT id FROM boxes WHERE code = ?)",
        (none,),
    )
    conn.close()
    return photos


def covers(conn, code):
    rows = conn.execute(
        """
        SELECT photos.id FROM photos JOIN boxes ON boxes.id = photos.box_id
         WHERE boxes.code = ? AND photos.is_primary = 1 ORDER BY photos.id
        """,
        (code,),
    )
    return [r[0] for r in rows]


def test_a_box_with_two_covers_keeps_the_older_one(config, messy):
    with closing(db.connect(config.db_path)) as conn:
        assert covers(conn, "B-0001") == [messy["two"][0]]


def test_a_box_with_photos_but_no_cover_gets_one(config, messy):
    # Otherwise its row in the list stays blank forever: it has a photo, and
    # nothing would ever promote it.
    with closing(db.connect(config.db_path)) as conn:
        assert covers(conn, "B-0002") == [messy["none"][0]]


def test_the_repaired_database_will_not_take_a_second_cover(config, messy):
    with closing(db.connect(config.db_path)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE photos SET is_primary = 1 WHERE id = ?", (messy["two"][1],))


def test_a_box_with_no_photos_at_all_is_left_alone(config, tmp_path):
    with closing(db.connect(config.db_path)) as conn:
        store.create_box(conn)

        assert conn.execute("SELECT count(*) FROM photos").fetchone()[0] == 0


def test_a_fresh_database_is_at_the_new_version(config):
    with closing(db.connect(config.db_path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert db.SCHEMA_VERSION >= 3
