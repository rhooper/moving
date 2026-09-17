-- The box-code counter became per prefix when the code format was made
-- configurable, so it is keyed 'box_code:<prefix>' rather than a single
-- 'box_code' row.
--
-- Without this, an existing database keeps its old 'box_code' row, the new
-- lookup for 'box_code:B' finds nothing and starts again at 1, and the very
-- next box tries to claim a code that is already on a printed label. The
-- UNIQUE constraint turns that into a failed insert rather than a duplicate,
-- so it breaks box creation outright.
--
-- 'B' is the default prefix, which is what every code issued before this
-- migration used.
--
-- A plain UPDATE is safe here: only code from *after* this migration writes
-- the prefixed name, so at this point the two names cannot both exist.

UPDATE counters SET name = 'box_code:B' WHERE name = 'box_code';
