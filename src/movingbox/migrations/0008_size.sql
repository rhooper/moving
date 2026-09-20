-- How big a container is: small, medium, large or extra large. Optional --
-- most of the time nobody says -- and only meaningful for something that holds
-- contents; the store clears it when a record stops being one.
--
-- No CHECK constraint, like `kind`: the allowed values live in kinds.py, where
-- adding one is an edit rather than a table rebuild.
ALTER TABLE boxes ADD COLUMN size TEXT;
