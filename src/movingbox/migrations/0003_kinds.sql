-- What a labelled record is: a container, or a loose thing.
--
-- Everything that gets a label needs a code, a QR, a destination, a status and
-- a location; only some of them have contents. A `kind` on the existing record
-- keeps codes, scanning, search, events, the manifest and the export working
-- unchanged, where a second table would have needed all of them duplicated.
--
-- Every row that existed before this is a box, which is what the default and
-- the backfill both say.

ALTER TABLE boxes ADD COLUMN kind TEXT NOT NULL DEFAULT 'box';

-- SQLite cannot add a CHECK constraint to an existing table, and rebuilding
-- boxes would mean dropping and recreating every foreign key pointing at it.
-- The allowed set is enforced in movingbox.kinds instead, which is also where
-- the per-kind behaviour lives, so there is one place to change.
UPDATE boxes SET kind = 'box' WHERE kind IS NULL OR kind = '';

CREATE INDEX idx_boxes_kind ON boxes(kind);
