"""Backups.

* SQLite's online backup API, not a file copy: in WAL mode a copy of
  `moving.db` can miss committed rows still in the `-wal` sidecar.
* A new backup is verified before anything is pruned, so a bad backup can
  never be the reason the good ones were deleted.
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .config import Config

#: Keep this many: a fortnight of nightlies.
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
        # Not read-only: a read-only connection cannot open a WAL database
        # with no -shm file.
        with closing(sqlite3.connect(path)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return False
            # Intact but not this application's database is not a backup.
            conn.execute("SELECT count(*) FROM boxes").fetchone()
        return True
    except sqlite3.DatabaseError:
        return False


def create(config: Config, *, keep: int = DEFAULT_KEEP) -> Path:
    """Write a verified backup and prune older ones. Returns the new file."""
    target_dir = directory(config)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Microseconds, so backups in the same second still sort; pruning relies on it.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%f")
    target = target_dir / f"moving-{stamp}.db"

    # Read-write, though nothing is written: `mode=ro` cannot open a WAL
    # database with no -shm file, i.e. whenever the service is not running.
    with closing(sqlite3.connect(config.db_path)) as source:
        with closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
            # The copy inherits WAL mode; a rollback journal makes it one
            # standalone file.
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
