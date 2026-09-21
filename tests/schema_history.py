"""Rolling a test database back to an older schema version.

A migration test has to start from the schema as it was *before* the migration
it is testing. The runner replays everything above `user_version`, so winding
that number back without undoing the later migrations makes them run a second
time -- "duplicate column name: kind". Adding a migration means adding its undo
step here.
"""

import sqlite3

#: How to undo each migration, newest first. Keyed by the version the step
#: removes: running UNDO[5] takes a database from version 5 to version 4.
UNDO: dict[int, list[str]] = {
    10: [
        "ALTER TABLE ai_jobs DROP COLUMN input_tokens",
        "ALTER TABLE ai_jobs DROP COLUMN output_tokens",
        "ALTER TABLE ai_jobs DROP COLUMN cost_usd",
    ],
    9: [
        "DROP INDEX IF EXISTS idx_boxes_parent",
        "ALTER TABLE boxes DROP COLUMN parent_id",
    ],
    8: [
        "ALTER TABLE boxes DROP COLUMN size",
    ],
    7: [
        "ALTER TABLE ai_jobs DROP COLUMN detail",
    ],
    6: [
        "DROP INDEX IF EXISTS idx_ai_jobs_queue",
        "DROP INDEX IF EXISTS idx_ai_jobs_photo",
        "ALTER TABLE ai_jobs DROP COLUMN photo_id",
        "ALTER TABLE ai_jobs DROP COLUMN started_at",
        "ALTER TABLE ai_jobs DROP COLUMN duration_ms",
        "ALTER TABLE boxes DROP COLUMN summary_source",
    ],
    5: [
        "DROP INDEX IF EXISTS idx_boxes_deleted",
        "ALTER TABLE boxes DROP COLUMN deleted_at",
    ],
    4: [
        # The data repair it performs is not reversible, and does not need to
        # be: each test sets up the rows it wants to see repaired.
        "DROP INDEX IF EXISTS idx_photos_one_cover",
    ],
    3: [
        "DROP INDEX IF EXISTS idx_boxes_kind",
        "ALTER TABLE boxes DROP COLUMN kind",
    ],
    2: [
        "UPDATE counters SET name = 'box_code' WHERE name = 'box_code:B'",
    ],
}


def roll_back_to(conn: sqlite3.Connection, version: int) -> None:
    """Undo every migration above `version`, and set user_version to match."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for step in range(current, version, -1):
        for statement in UNDO.get(step, []):
            conn.execute(statement)
    conn.execute(f"PRAGMA user_version = {version:d}")


def test_every_migration_above_the_first_can_be_undone():
    """Guards the table: a new migration with no undo step breaks the others."""
    from movingbox import db

    missing = [v for v, _ in db._migration_files() if v > 1 and v not in UNDO]

    assert missing == [], (
        f"migrations {missing} have no undo step in schema_history.UNDO, so any "
        f"test rolling back past them will replay them and fail"
    )
