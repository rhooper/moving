"""Backups.

The move data is irreplaceable, exists on one machine, and is being written to
during the exact weeks it matters most. Two things follow:

* Backups use SQLite's **online backup API**, not a file copy. The service runs
  under launchd and holds a connection open permanently, and in WAL mode a
  plain `cp` of `moving.db` can miss committed rows that still live in the
  `-wal` sidecar. The API produces a standalone, consistent database.
* A new backup is **verified before anything is pruned**. Otherwise a corrupt
  backup becomes the reason the good ones were deleted, turning a bad backup
  into actual data loss.
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .config import Config

#: Keep this many. A fortnight of nightlies covers "I broke it last week".
DEFAULT_KEEP = 14


class BackupFailed(RuntimeError):
    """The backup was written but did not verify."""


def directory(config: Config) -> Path:
    return config.backup_dir or config.db_path.parent / "backups"


def existing(config: Config) -> list[Path]:
    """Backups, newest first."""
    found = sorted(directory(config).glob("moving-*.db"), reverse=True)
    return found


def verify(path: Path) -> bool:
    """Whether ``path`` is a readable database that passes an integrity check."""
    if not path.is_file():
        return False
    try:
        # Not opened read-only: see the journal_mode note in create(). A
        # read-only connection cannot open a WAL database that has no -shm
        # file, which is exactly the state a fresh backup is in.
        with closing(sqlite3.connect(path)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return False
            # Intact but not this application's database is not a useful backup.
            conn.execute("SELECT count(*) FROM boxes").fetchone()
        return True
    except sqlite3.DatabaseError:
        return False


def create(config: Config, *, keep: int = DEFAULT_KEEP) -> Path:
    """Write a verified backup and prune older ones. Returns the new file."""
    target_dir = directory(config)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Microseconds in the name so several backups in one second still sort
    # correctly, which is what pruning relies on.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%f")
    target = target_dir / f"moving-{stamp}.db"

    # The source is opened read-write even though the backup API never writes
    # to it. A read-only connection cannot open a WAL database that has no
    # -shm file, so `mode=ro` would fail precisely when the service is *not*
    # running -- the case where a manual backup is most likely.
    with closing(sqlite3.connect(config.db_path)) as source:
        with closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
            # The backup API copies the file page for page, header included,
            # so a backup of a WAL database is itself in WAL mode. That makes
            # the artefact awkward: it cannot be opened read-only, and copying
            # it elsewhere without its sidecars can lose data. Switching to a
            # rollback journal makes the backup one genuinely standalone file.
            destination.execute("PRAGMA journal_mode = DELETE")

    if not verify(target):
        target.unlink(missing_ok=True)
        raise BackupFailed(f"{target.name} did not verify; older backups left untouched")

    if config.photo_dir.is_dir():
        shutil.copytree(config.photo_dir, target_dir / f"{target.stem}-photos")

    for stale in existing(config)[keep:]:
        stale.unlink(missing_ok=True)
        shutil.rmtree(stale.parent / f"{stale.stem}-photos", ignore_errors=True)

    return target
