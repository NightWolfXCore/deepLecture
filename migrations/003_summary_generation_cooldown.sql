-- Retain the last successful generation time even while a summary is replaced.
ALTER TABLE lectures ADD COLUMN summary_generated_at DATETIME(3) NULL;
UPDATE lectures l JOIN lecture_summaries s ON s.lecture_id=l.id
SET l.summary_generated_at=s.created_at;
