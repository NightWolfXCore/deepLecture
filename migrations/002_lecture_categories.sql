-- Add subject categories without changing existing lecture IDs or derived data.
ALTER TABLE lectures
    ADD COLUMN category VARCHAR(32) CHARACTER SET ascii NOT NULL DEFAULT 'other',
    ADD KEY idx_lectures_category_created (category, created_at, id),
    ADD CONSTRAINT chk_lectures_category CHECK (category IN ('mathematics','russian','physics','informatics','chemistry','biology','history','geography','literature','foreign_languages','other'));
