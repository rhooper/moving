-- A photo id is never reused.
--
-- `id INTEGER PRIMARY KEY` alone hands out max(id) + 1, so deleting the newest
-- photo and taking another gave the new one the deleted one's id -- and so its
-- URLs, which browsers cache for a year. A phone showed B-0057's new photo as
-- the one just deleted from B-0056 (photo 102, 2026-09-26). AUTOINCREMENT
-- keeps the highest id ever issued in sqlite_sequence and never goes below it.
--
-- SQLite cannot add AUTOINCREMENT to a table, so it is rebuilt: same columns,
-- same rows and ids, same indexes. Nothing references photos(id) by foreign
-- key (ai_jobs.photo_id deliberately has none), so the drop cascades nowhere.
-- Ids reused before this migration are handled by the `k` in photo URLs.

BEGIN;

CREATE TABLE photos_new (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id         INTEGER NOT NULL REFERENCES boxes(id) ON DELETE CASCADE,
    filename       TEXT    NOT NULL,
    thumb_filename TEXT,
    width          INTEGER,
    height         INTEGER,
    bytes          INTEGER,
    sha256         TEXT    NOT NULL,
    caption        TEXT,
    is_primary     INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    taken_at       TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Re-uploading the same shot (a retried upload) must not duplicate it.
    UNIQUE (box_id, sha256)
);

INSERT INTO photos_new
    (id, box_id, filename, thumb_filename, width, height, bytes, sha256,
     caption, is_primary, taken_at, created_at)
SELECT id, box_id, filename, thumb_filename, width, height, bytes, sha256,
       caption, is_primary, taken_at, created_at
  FROM photos;

DROP TABLE photos;
ALTER TABLE photos_new RENAME TO photos;

CREATE INDEX idx_photos_box ON photos(box_id);
CREATE UNIQUE INDEX idx_photos_one_cover ON photos(box_id) WHERE is_primary = 1;

COMMIT;
