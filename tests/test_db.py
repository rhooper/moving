"""Schema, migration runner, and box-code allocation."""

import sqlite3
from contextlib import closing

import pytest

from movingbox import db


def test_fresh_database_is_migrated_to_latest_version(tmp_path):
    with closing(db.connect(tmp_path / "fresh.db")) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert db.SCHEMA_VERSION > 0


def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "twice.db"
    with closing(db.connect(path)) as first:
        first.execute("INSERT INTO rooms (name) VALUES ('Kitchen')")

    with closing(db.connect(path)) as second:  # must not re-apply, must not raise
        assert second.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        # Re-running 0001 would have dropped this row along with the table.
        assert second.execute("SELECT count(*) FROM rooms").fetchone()[0] == 1


def test_expected_tables_exist(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"rooms", "boxes", "items", "photos", "events", "ai_jobs", "settings"} <= names


def test_deleting_a_box_cascades_to_its_items(conn):
    conn.execute("INSERT INTO boxes (code) VALUES ('B-0001')")
    box_id = conn.execute("SELECT id FROM boxes").fetchone()[0]
    conn.execute("INSERT INTO items (box_id, name) VALUES (?, 'kettle')", (box_id,))

    conn.execute("DELETE FROM boxes WHERE id = ?", (box_id,))

    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 0


def test_item_referencing_a_missing_box_is_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO items (box_id, name) VALUES (999, 'ghost')")


def test_box_status_is_constrained_to_the_lifecycle(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO boxes (code, status) VALUES ('B-0001', 'teleporting')")


def test_box_codes_are_sequential(conn):
    assert db.next_box_code(conn) == "B-0001"
    assert db.next_box_code(conn) == "B-0002"
    assert db.next_box_code(conn) == "B-0003"


def test_box_codes_are_never_reused_after_a_box_is_deleted(conn):
    first = db.next_box_code(conn)
    conn.execute("INSERT INTO boxes (code) VALUES (?)", (first,))
    conn.execute("DELETE FROM boxes WHERE code = ?", (first,))

    # Reissuing B-0001 would point an already-printed label at a different box.
    assert db.next_box_code(conn) != first
