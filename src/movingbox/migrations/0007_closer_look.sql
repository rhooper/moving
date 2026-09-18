-- A photo can be read twice: quickly when it is uploaded, and again, more
-- carefully and more slowly, when somebody asks for a closer look.
--
-- Which kind a job was has to be recorded on the job. It cannot be worked out
-- from the model name afterwards: the two models are configuration, and
-- configuration changes.
ALTER TABLE ai_jobs ADD COLUMN detail INTEGER NOT NULL DEFAULT 0;
