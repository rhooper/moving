-- Things inside things: a bag in a box in a crate, to any depth.
--
-- One nullable pointer at the record this one is inside. NULL is top level,
-- which is what every existing record is.
--
-- No REFERENCES clause, for the reason given in 0006: SQLite will not DROP a
-- column that is part of a foreign key, and the migration tests roll schemas
-- back by dropping. The store keeps it honest instead -- a parent must exist,
-- be a container, not be in the bin, and not be inside the thing being moved --
-- and it refuses to delete a container that still has things inside, so a
-- pointer is never left dangling.
ALTER TABLE boxes ADD COLUMN parent_id INTEGER;

-- "What is inside this?" is asked on every record page, and "how many?" for
-- every row of the list.
CREATE INDEX idx_boxes_parent ON boxes(parent_id);
