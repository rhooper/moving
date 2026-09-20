-- What a reading cost, and who actually did it.
--
-- A hybrid setup has two models that can answer one job. `provider` and
-- `model` already existed but were written at *queue* time, naming what would
-- be tried first; the worker now writes back what actually answered, so a
-- wrong item is traceable to the model that produced it.
--
-- The three new columns are the budget. A move was funded at $20-30 and a
-- budget nobody can see is a budget that gets exceeded, so the running total
-- is summed from here and shown in Settings -- and enforced: past the cap the
-- cloud tier is not offered and reading falls back to the local model.
--
-- NULL, not 0, for every job that already exists. All of them were local and
-- cost nothing, but "nothing was recorded" and "it was free" are different
-- facts and only one of them is true here. SUM ignores NULL, so the running
-- total is unaffected either way.
ALTER TABLE ai_jobs ADD COLUMN input_tokens INTEGER;
ALTER TABLE ai_jobs ADD COLUMN output_tokens INTEGER;
ALTER TABLE ai_jobs ADD COLUMN cost_usd REAL;
