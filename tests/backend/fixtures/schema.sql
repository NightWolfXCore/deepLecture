-- Reference schema for backend integration tests only, not production migrations.
CREATE TABLE IF NOT EXISTS lectures (
 id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
 title VARCHAR(200) NOT NULL,
 original_filename VARCHAR(255) NOT NULL,
 storage_key VARCHAR(255) NOT NULL UNIQUE,
 mime_type VARCHAR(100) NOT NULL,
 size_bytes BIGINT UNSIGNED NOT NULL,
 duration_ms BIGINT UNSIGNED NULL,
 status VARCHAR(20) NOT NULL,
 transcript_ready_at DATETIME(3) NULL,
 index_ready_at DATETIME(3) NULL,
 summary_ready_at DATETIME(3) NULL,
 created_at DATETIME(3) NOT NULL,
 updated_at DATETIME(3) NOT NULL,
 INDEX lectures_catalog (created_at, id),
 CHECK (duration_ms IS NULL OR duration_ms > 0),
 CHECK (status IN ('queued','extracting','transcribing','indexing','summarizing','ready','failed'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS processing_jobs (
 id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
 lecture_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 status VARCHAR(16) NOT NULL,
 stage VARCHAR(20) NOT NULL,
 attempt INT UNSIGNED NOT NULL DEFAULT 0,
 worker_token CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
 heartbeat_at DATETIME(3) NULL,
 lease_expires_at DATETIME(3) NULL,
 error_code VARCHAR(64) NULL,
 error_message VARCHAR(500) NULL,
 failed_stage VARCHAR(20) NULL,
 started_at DATETIME(3) NULL,
 finished_at DATETIME(3) NULL,
 created_at DATETIME(3) NOT NULL,
 updated_at DATETIME(3) NOT NULL,
 INDEX jobs_queue (status, created_at, id),
 INDEX jobs_expiration (status, lease_expires_at),
 INDEX jobs_lecture (lecture_id, created_at, id),
 CONSTRAINT jobs_lecture_fk FOREIGN KEY (lecture_id) REFERENCES lectures(id) ON DELETE CASCADE,
 CHECK (status IN ('queued','running','succeeded','failed')),
 CHECK (stage IN ('queued','extracting','transcribing','indexing','summarizing','ready'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS transcript_segments (
 id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
 lecture_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 position INT UNSIGNED NOT NULL,
 start_ms BIGINT UNSIGNED NOT NULL,
 end_ms BIGINT UNSIGNED NOT NULL,
 text TEXT NOT NULL,
 UNIQUE KEY segments_position (lecture_id, position),
 INDEX segments_time (lecture_id, start_ms),
 CONSTRAINT segments_lecture_fk FOREIGN KEY (lecture_id) REFERENCES lectures(id) ON DELETE CASCADE,
 CHECK (end_ms > start_ms),
 CHECK (CHAR_LENGTH(TRIM(text)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS search_chunks (
 id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
 lecture_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 position INT UNSIGNED NOT NULL,
 start_ms BIGINT UNSIGNED NOT NULL,
 end_ms BIGINT UNSIGNED NOT NULL,
 text TEXT NOT NULL,
 first_segment_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 last_segment_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 UNIQUE KEY chunks_position (lecture_id, position),
 INDEX chunks_time (lecture_id, start_ms),
 CONSTRAINT chunks_lecture_fk FOREIGN KEY (lecture_id) REFERENCES lectures(id) ON DELETE CASCADE,
 CONSTRAINT chunks_first_fk FOREIGN KEY (first_segment_id) REFERENCES transcript_segments(id) ON DELETE RESTRICT,
 CONSTRAINT chunks_last_fk FOREIGN KEY (last_segment_id) REFERENCES transcript_segments(id) ON DELETE RESTRICT,
 CHECK (end_ms > start_ms)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS chunk_embeddings (
 chunk_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
 model_name VARCHAR(128) NOT NULL,
 model_revision VARCHAR(128) NOT NULL,
 dimensions SMALLINT UNSIGNED NOT NULL,
 embedding BLOB NOT NULL,
 CONSTRAINT embeddings_chunk_fk FOREIGN KEY (chunk_id) REFERENCES search_chunks(id) ON DELETE CASCADE,
 CHECK (dimensions = 384),
 CHECK (OCTET_LENGTH(embedding) = dimensions * 4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS lecture_summaries (
 lecture_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
 text MEDIUMTEXT NOT NULL,
 provider VARCHAR(64) NOT NULL,
 model_name VARCHAR(128) NOT NULL,
 created_at DATETIME(3) NOT NULL,
 CONSTRAINT summaries_lecture_fk FOREIGN KEY (lecture_id) REFERENCES lectures(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS lecture_chapters (
 id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
 lecture_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 position INT UNSIGNED NOT NULL,
 title VARCHAR(200) NOT NULL,
 level TINYINT UNSIGNED NOT NULL,
 start_segment_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 UNIQUE KEY chapters_position (lecture_id, position),
 CONSTRAINT chapters_lecture_fk FOREIGN KEY (lecture_id) REFERENCES lectures(id) ON DELETE CASCADE,
 CONSTRAINT chapters_segment_fk FOREIGN KEY (start_segment_id) REFERENCES transcript_segments(id) ON DELETE RESTRICT,
 CHECK (level IN (1,2))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
