-- Photo analysis runs in the background, one job per photo.
--
-- ai_jobs already recorded a drafting run against a box. A job now belongs to
-- a *photo*, so its result stays with the picture it came from, and it is timed
-- so the next one can be estimated.
--
-- photo_id carries no REFERENCES clause on purpose: SQLite refuses to DROP a
-- column that is part of a foreign key, and the migration tests roll schemas
-- back by dropping. storage.delete_photo removes a photo's jobs instead, and a
-- purged box still takes its jobs with it through ai_jobs.box_id.
ALTER TABLE ai_jobs ADD COLUMN photo_id INTEGER;
ALTER TABLE ai_jobs ADD COLUMN started_at TEXT;
ALTER TABLE ai_jobs ADD COLUMN duration_ms INTEGER;

-- The worker's two questions: what is next, and what happened to this photo.
CREATE INDEX idx_ai_jobs_queue ON ai_jobs(status, id);
CREATE INDEX idx_ai_jobs_photo ON ai_jobs(photo_id);

-- Whose words the summary is. The model may rewrite a summary it wrote, or
-- fill an empty one; it may never touch one a person typed. Every existing
-- summary is treated as a person's -- some were accepted from a draft, but
-- accepting one was a decision, and guessing otherwise risks overwriting it.
ALTER TABLE boxes ADD COLUMN summary_source TEXT NOT NULL DEFAULT 'manual'
    CHECK (summary_source IN ('manual', 'auto'));
