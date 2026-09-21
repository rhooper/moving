"""Backups. The move data is irreplaceable and lives on one machine."""

import sqlite3
from contextlib import closing

import pytest

from movingbox import backup, db, store


@pytest.fixture
def populated(config):
    conn = db.connect(config.db_path)
    store.create_room(conn, "Kitchen")
    store.create_box(conn, content_summary="pots and pans")
    conn.close()
    return config


def test_a_backup_is_a_readable_database_with_the_same_rows(populated):
    written = backup.create(populated)

    with closing(sqlite3.connect(written)) as copy:
        codes = [r[0] for r in copy.execute("SELECT code FROM boxes")]
    assert codes == ["B-0001"]


def test_the_backup_filename_carries_a_timestamp(populated):
    written = backup.create(populated)

    assert written.name.startswith("moving-")
    assert written.suffix == ".db"
    assert any(char.isdigit() for char in written.stem)


def test_a_backup_can_be_taken_while_the_database_is_open_and_written(populated):
    # The service runs under launchd and holds a connection permanently, so a
    # backup that needs exclusive access would never run.
    conn = db.connect(populated.db_path)
    store.create_box(conn, content_summary="mid-backup write")

    written = backup.create(populated)

    with closing(sqlite3.connect(written)) as copy:
        assert copy.execute("SELECT count(*) FROM boxes").fetchone()[0] == 2
    conn.close()


def test_the_backup_is_not_a_wal_dependent_fragment(populated):
    # Copying moving.db with `cp` while WAL is active can miss committed rows
    # that still live in the -wal file. The backup must stand alone.
    conn = db.connect(populated.db_path)
    store.create_box(conn, content_summary="committed but maybe still in wal")

    written = backup.create(populated)
    conn.close()

    # No sidecar files, and the copy reads correctly on its own.
    assert not written.with_suffix(".db-wal").exists()
    with closing(sqlite3.connect(written)) as copy:
        assert copy.execute("SELECT count(*) FROM boxes").fetchone()[0] == 2


def test_a_backup_can_be_opened_read_only(populated):
    # A backup inherits WAL mode page-for-page, and a read-only connection
    # cannot open a WAL database that has no -shm file -- so restoring from a
    # mounted snapshot or a synced folder would fail.
    written = backup.create(populated)

    with closing(sqlite3.connect(f"file:{written}?mode=ro", uri=True)) as copy:
        assert copy.execute("SELECT count(*) FROM boxes").fetchone()[0] == 1


def test_a_backup_is_a_single_self_contained_file(populated):
    written = backup.create(populated)

    with closing(sqlite3.connect(written)) as copy:
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] != "wal"


def test_backups_accumulate_under_the_retention_limit(populated):
    for _ in range(3):
        backup.create(populated)

    assert len(backup.existing(populated)) == 3


def test_old_backups_are_pruned_to_the_keep_limit(populated):
    for _ in range(5):
        backup.create(populated, keep=3)

    kept = backup.existing(populated)
    assert len(kept) == 3


def test_pruning_keeps_the_newest(populated):
    first = backup.create(populated, keep=2)
    backup.create(populated, keep=2)
    third = backup.create(populated, keep=2)

    kept = backup.existing(populated)
    assert third in kept
    assert first not in kept


def test_photos_are_copied_alongside_the_database(populated):
    populated.photo_dir.mkdir(parents=True, exist_ok=True)
    (populated.photo_dir / "b1.jpg").write_bytes(b"not really a jpeg")

    written = backup.create(populated)

    copied = written.parent / f"{written.stem}-photos" / "b1.jpg"
    assert copied.read_bytes() == b"not really a jpeg"


def test_a_missing_photo_directory_is_not_an_error(populated):
    assert not populated.photo_dir.exists()

    backup.create(populated)  # must not raise


def test_verify_rejects_a_corrupt_backup(populated, tmp_path):
    broken = tmp_path / "moving-broken.db"
    broken.write_bytes(b"this is not a database")

    assert backup.verify(broken) is False


def test_verify_accepts_a_real_backup(populated):
    assert backup.verify(backup.create(populated)) is True


def test_create_verifies_before_pruning_anything(populated, monkeypatch):
    # A corrupt new backup must never be the reason older good ones are deleted.
    good = backup.create(populated, keep=1)
    monkeypatch.setattr(backup, "verify", lambda path: False)

    with pytest.raises(backup.BackupFailed):
        backup.create(populated, keep=1)

    assert good in backup.existing(populated)
