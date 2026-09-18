"""Upgrading a database that predates kinds.

Everything recorded before this feature was a box, and the labels for those are
already on tape. The upgrade must not change what any of them is.
"""

from contextlib import closing

from movingbox import db, store


def a_v2_database(path):
    """A database as it looked before 0003: no `kind` column."""
    conn = db.connect(path)
    for _ in range(3):
        store.create_box(conn, content_summary="pots")
    conn.execute("DROP INDEX IF EXISTS idx_boxes_kind")
    conn.execute("ALTER TABLE boxes DROP COLUMN kind")
    conn.execute("PRAGMA user_version = 2")
    conn.close()


def test_existing_records_become_boxes(tmp_path):
    path = tmp_path / "old.db"
    a_v2_database(path)

    with closing(db.connect(path)) as conn:
        kinds = [r[0] for r in conn.execute("SELECT kind FROM boxes ORDER BY id")]

    assert kinds == ["box", "box", "box"]


def test_upgrading_does_not_disturb_existing_codes(tmp_path):
    # These are on printed tape; the upgrade must not renumber anything.
    path = tmp_path / "old.db"
    a_v2_database(path)

    with closing(db.connect(path)) as conn:
        codes = [r[0] for r in conn.execute("SELECT code FROM boxes ORDER BY id")]

    assert codes == ["B-0001", "B-0002", "B-0003"]


def test_the_next_record_still_follows_on(tmp_path):
    path = tmp_path / "old.db"
    a_v2_database(path)

    with closing(db.connect(path)) as conn:
        assert store.create_box(conn)["code"] == "B-0004"


def test_an_upgraded_database_can_hold_a_loose_item(tmp_path):
    path = tmp_path / "old.db"
    a_v2_database(path)

    with closing(db.connect(path)) as conn:
        item = store.create_box(conn, kind="item", content_summary="Bicycle")

    assert item["kind"] == "item"


def test_a_fresh_database_is_unaffected(tmp_path):
    with closing(db.connect(tmp_path / "fresh.db")) as conn:
        assert store.create_box(conn)["kind"] == "box"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
