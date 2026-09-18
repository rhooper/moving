-- Exactly one cover photo per box, enforced by the schema.
--
-- `is_primary` has existed since 0001 but nothing ever guaranteed the "one"
-- part: it was set on the first photo uploaded, and a delete promoted the
-- lowest-numbered survivor whether or not the deleted photo was the cover. So
-- a live database can hold boxes with two flagged photos and boxes with none.
-- Now that the cover is a choice a person makes and a picture they see on
-- every list row, both are visible faults: a blank row, or a row whose
-- picture depends on which query happened to run.
--
-- The repair has to come first -- CREATE UNIQUE INDEX fails against rows that
-- already violate it, and a migration that fails leaves the database at the
-- old user_version and retries on the next connection, forever.

-- Two covers: keep the oldest, which is the one that was the cover before any
-- delete started promoting extras alongside it.
UPDATE photos SET is_primary = 0
 WHERE is_primary = 1
   AND id NOT IN (SELECT min(id) FROM photos WHERE is_primary = 1 GROUP BY box_id);

-- Photos but no cover: promote the oldest. Nothing else ever would, so the
-- box's row would stay blank for as long as those photos exist.
UPDATE photos SET is_primary = 1
 WHERE id IN (
     SELECT min(id) FROM photos
      WHERE box_id NOT IN (SELECT box_id FROM photos WHERE is_primary = 1)
      GROUP BY box_id
 );

-- Partial, so it constrains only the flagged rows and leaves the unflagged
-- ones unconstrained -- a plain UNIQUE(box_id, is_primary) would allow one
-- cover but also only one non-cover photo per box.
--
-- It doubles as the lookup behind the cover id on every list row: the list
-- query's `photos.box_id = ? AND is_primary = 1` implies this index's own
-- WHERE clause, so SQLite satisfies it as a seek on a covering index (the
-- photo id it wants is the index's own rowid) without reading the table.
--
-- IF NOT EXISTS so the whole file is re-runnable, like the two UPDATEs above.
-- The migration runner never replays a file, but tests construct old-shaped
-- databases by winding user_version back over a real one, and a migration
-- that cannot survive being applied twice fails the whole connection rather
-- than the one statement.
CREATE UNIQUE INDEX IF NOT EXISTS idx_photos_one_cover ON photos(box_id) WHERE is_primary = 1;
