-- The history log records every change, and outlives the record it is about.
--
-- Until now `events` logged only create, delete, restore, status, location and
-- print, and `ON DELETE CASCADE` erased a record's history when it was purged.
-- Rebuilt so that:
--
--   * `box_code` names the record on every row. Codes are never reused (a
--     monotonic counter), so it identifies a purged record for good;
--     `box_id` becomes NULL on purge instead of taking the row with it.
--   * `field` says what an edit changed (a column, or an item's name/qty),
--     and `ref` which part of the record it was about ('item:12', 'photo:105').
--   * ids are never reused (AUTOINCREMENT), as for photos in 0011.

BEGIN;

CREATE TABLE events_new (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id     INTEGER REFERENCES boxes(id) ON DELETE SET NULL,
    box_code   TEXT,
    kind       TEXT    NOT NULL,
    field      TEXT,
    ref        TEXT,
    from_value TEXT,
    to_value   TEXT,
    actor      TEXT,
    note       TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO events_new
    (id, box_id, box_code, kind, from_value, to_value, actor, note, created_at)
SELECT events.id, events.box_id, boxes.code, events.kind, events.from_value,
       events.to_value, events.actor, events.note, events.created_at
  FROM events LEFT JOIN boxes ON boxes.id = events.box_id;

DROP TABLE events;
ALTER TABLE events_new RENAME TO events;

CREATE INDEX idx_events_box ON events(box_id, created_at);
CREATE INDEX idx_events_code ON events(box_code, id);

COMMIT;
