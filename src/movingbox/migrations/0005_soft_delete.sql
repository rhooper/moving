-- Deleting is reversible.
--
-- Mid-move an accidental delete on a phone is a real prospect, and a permanent
-- one is unrecoverable. So a delete stamps `deleted_at` and keeps everything --
-- rows, items, photos and their files -- and a second, explicit purge is what
-- actually removes them.
--
-- Nothing existing has been deleted, so every current row gets NULL.

ALTER TABLE boxes ADD COLUMN deleted_at TEXT;

-- Every ordinary query filters on this, including the listing that backs the
-- box list and search.
CREATE INDEX idx_boxes_deleted ON boxes(deleted_at);
