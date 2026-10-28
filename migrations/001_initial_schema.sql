-- Киберрывок: начальная схема БД по database.md, версия 1
-- MySQL 8.0+, InnoDB, utf8mb4.
-- UUID генерируются приложениями. Все даты/время передаются в UTC.
-- Запускайте этот файл один раз на пустой базе данных проекта.

SET NAMES utf8mb4;
SET time_zone = '+00:00';

CREATE TABLE IF NOT EXISTS lectures (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    title VARCHAR(200) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    storage_key VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    size_bytes BIGINT UNSIGNED NOT NULL,
    duration_ms BIGINT UNSIGNED NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    transcript_ready_at DATETIME(3) NULL,
    index_ready_at DATETIME(3) NULL,
    summary_ready_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
        ON UPDATE CURRENT_TIMESTAMP(3),

    PRIMARY KEY (id),
    UNIQUE KEY uq_lectures_storage_key (storage_key),
    KEY idx_lectures_created_id (created_at, id),

    CONSTRAINT chk_lectures_duration_positive
        CHECK (duration_ms IS NULL OR duration_ms > 0),
    CONSTRAINT chk_lectures_status
        CHECK (status IN (
            'queued', 'extracting', 'transcribing', 'indexing',
            'summarizing', 'ready', 'failed'
        ))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS processing_jobs (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    lecture_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    stage VARCHAR(20) NOT NULL DEFAULT 'queued',
    attempt INT UNSIGNED NOT NULL DEFAULT 0,
    worker_token CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    heartbeat_at DATETIME(3) NULL,
    lease_expires_at DATETIME(3) NULL,
    error_code VARCHAR(64) NULL,
    error_message VARCHAR(500) NULL,
    failed_stage VARCHAR(20) NULL,
    started_at DATETIME(3) NULL,
    finished_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
        ON UPDATE CURRENT_TIMESTAMP(3),

    PRIMARY KEY (id),
    KEY idx_jobs_status_created_id (status, created_at, id),
    KEY idx_jobs_status_lease (status, lease_expires_at),
    KEY idx_jobs_lecture_created_id (lecture_id, created_at, id),

    CONSTRAINT fk_jobs_lecture
        FOREIGN KEY (lecture_id) REFERENCES lectures (id)
        ON DELETE CASCADE,
    CONSTRAINT chk_jobs_status
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    CONSTRAINT chk_jobs_stage
        CHECK (stage IN (
            'queued', 'extracting', 'transcribing', 'indexing',
            'summarizing', 'ready'
        )),
    CONSTRAINT chk_jobs_failed_stage
        CHECK (
            failed_stage IS NULL OR failed_stage IN (
                'queued', 'extracting', 'transcribing', 'indexing',
                'summarizing', 'ready'
            )
        )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS transcript_segments (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    lecture_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    position INT UNSIGNED NOT NULL,
    start_ms BIGINT UNSIGNED NOT NULL,
    end_ms BIGINT UNSIGNED NOT NULL,
    text TEXT NOT NULL,

    PRIMARY KEY (id),
    UNIQUE KEY uq_segments_lecture_position (lecture_id, position),
    KEY idx_segments_lecture_start (lecture_id, start_ms),

    CONSTRAINT fk_segments_lecture
        FOREIGN KEY (lecture_id) REFERENCES lectures (id)
        ON DELETE CASCADE,
    CONSTRAINT chk_segments_time
        CHECK (end_ms > start_ms),
    CONSTRAINT chk_segments_text_nonempty
        CHECK (CHAR_LENGTH(TRIM(text)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS search_chunks (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    lecture_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    position INT UNSIGNED NOT NULL,
    start_ms BIGINT UNSIGNED NOT NULL,
    end_ms BIGINT UNSIGNED NOT NULL,
    text TEXT NOT NULL,
    first_segment_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    last_segment_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,

    PRIMARY KEY (id),
    UNIQUE KEY uq_chunks_lecture_position (lecture_id, position),
    KEY idx_chunks_lecture_start (lecture_id, start_ms),
    KEY idx_chunks_first_segment (first_segment_id),
    KEY idx_chunks_last_segment (last_segment_id),

    CONSTRAINT fk_chunks_lecture
        FOREIGN KEY (lecture_id) REFERENCES lectures (id)
        ON DELETE CASCADE,
    CONSTRAINT fk_chunks_first_segment
        FOREIGN KEY (first_segment_id) REFERENCES transcript_segments (id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_chunks_last_segment
        FOREIGN KEY (last_segment_id) REFERENCES transcript_segments (id)
        ON DELETE RESTRICT,
    CONSTRAINT chk_chunks_time
        CHECK (end_ms > start_ms),
    CONSTRAINT chk_chunks_text_nonempty
        CHECK (CHAR_LENGTH(TRIM(text)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    model_name VARCHAR(128) NOT NULL DEFAULT 'intfloat/multilingual-e5-small',
    model_revision VARCHAR(128) NOT NULL,
    dimensions SMALLINT UNSIGNED NOT NULL DEFAULT 384,
    embedding BLOB NOT NULL,

    PRIMARY KEY (chunk_id),

    CONSTRAINT fk_embeddings_chunk
        FOREIGN KEY (chunk_id) REFERENCES search_chunks (id)
        ON DELETE CASCADE,
    CONSTRAINT chk_embeddings_dimensions
        CHECK (dimensions = 384),
    CONSTRAINT chk_embeddings_length
        CHECK (OCTET_LENGTH(embedding) = dimensions * 4)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS lecture_summaries (
    lecture_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    text MEDIUMTEXT NOT NULL,
    provider VARCHAR(64) NOT NULL,
    model_name VARCHAR(128) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    PRIMARY KEY (lecture_id),

    CONSTRAINT fk_summaries_lecture
        FOREIGN KEY (lecture_id) REFERENCES lectures (id)
        ON DELETE CASCADE,
    CONSTRAINT chk_summaries_text_nonempty
        CHECK (CHAR_LENGTH(TRIM(text)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS lecture_chapters (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    lecture_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    position INT UNSIGNED NOT NULL,
    title VARCHAR(200) NOT NULL,
    level TINYINT UNSIGNED NOT NULL,
    start_segment_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,

    PRIMARY KEY (id),
    UNIQUE KEY uq_chapters_lecture_position (lecture_id, position),
    KEY idx_chapters_start_segment (start_segment_id),

    CONSTRAINT fk_chapters_lecture
        FOREIGN KEY (lecture_id) REFERENCES lectures (id)
        ON DELETE CASCADE,
    CONSTRAINT fk_chapters_start_segment
        FOREIGN KEY (start_segment_id) REFERENCES transcript_segments (id)
        ON DELETE RESTRICT,
    CONSTRAINT chk_chapters_level
        CHECK (level IN (1, 2)),
    CONSTRAINT chk_chapters_title_nonempty
        CHECK (CHAR_LENGTH(TRIM(title)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Важно:
-- 1) Не более одного queued/running задания на лекцию обеспечивается
--    транзакциями приложения с SELECT ... FOR UPDATE по строке lectures.
--    Обычный UNIQUE-индекс здесь не решает задачу корректно.
-- 2) При очистке результатов соблюдайте порядок:
--    lecture_chapters -> lecture_summaries -> chunk_embeddings
--    -> search_chunks -> transcript_segments.
-- 3) При удалении самой лекции сначала удаляйте зависимые данные в этом
--    порядке, затем строку lectures: связи search_chunks/chapters с сегментами
--    имеют ON DELETE RESTRICT.
-- 4) Проверки принадлежности сегментов той же лекции и порядка сегментов
--    выполняются worker в транзакции, как описано в database.md.
