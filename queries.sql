-- Киберрывок: согласованные SQL-запросы для backend / Python worker
-- СУБД: MySQL 8.0.16+ (CHECK constraints должны реально поддерживаться).
--
-- Плейсхолдеры %s предназначены для параметризованных запросов PyMySQL.
-- Передавайте значения отдельным аргументом execute(), не форматируйте SQL строкой.
-- Все даты хранятся в UTC. Для соединения установите: SET time_zone = '+00:00';
--
-- Поиск по векторам:
-- chunk_embeddings.embedding — BLOB из 384 float32 little-endian (1536 байт).
-- MySQL в этой схеме не считает cosine similarity самостоятельно: приложение
-- декодирует векторы и считает dot product для нормализованных embedding.
-- Используйте один и тот же model_name + model_revision для query и passages.
-- Векторы из фикстур с model_revision='FIXTURE-UI-ONLY' пригодны только для UI,
-- их нельзя использовать для проверки качества семантического поиска.


-- ---------------------------------------------------------------------------
-- 1. Каталог лекций с курсорной пагинацией.
-- Параметры: (page_size, cursor_created_at, cursor_id)
-- Для первой страницы cursor_created_at и cursor_id передайте NULL.
-- Параметр page_size должен быть ограничен приложением (например, 1..100).
SELECT
    l.id,
    l.title,
    l.original_filename,
    l.mime_type,
    l.size_bytes,
    l.duration_ms,
    l.status,
    l.transcript_ready_at,
    l.index_ready_at,
    l.summary_ready_at,
    l.created_at,
    l.updated_at
FROM lectures AS l
WHERE
    (%s IS NULL OR l.created_at < %s
     OR (l.created_at = %s AND l.id < %s))
ORDER BY l.created_at DESC, l.id DESC
LIMIT %s;


-- ---------------------------------------------------------------------------
-- 2. Метаданные одной лекции.
-- Параметр: lecture_id
SELECT
    id,
    title,
    original_filename,
    storage_key,
    mime_type,
    size_bytes,
    duration_ms,
    status,
    transcript_ready_at,
    index_ready_at,
    summary_ready_at,
    created_at,
    updated_at
FROM lectures
WHERE id = %s;


-- ---------------------------------------------------------------------------
-- 3. Транскрипт лекции в хронологическом порядке.
-- Параметры: (lecture_id, limit, offset)
-- Если нужен полный транскрипт, используйте постраничную загрузку.
SELECT
    s.id,
    s.position,
    s.start_ms,
    s.end_ms,
    s.text
FROM transcript_segments AS s
WHERE s.lecture_id = %s
ORDER BY s.position ASC
LIMIT %s OFFSET %s;


-- ---------------------------------------------------------------------------
-- 4. Чанки лекции вместе с embedding для семантического поиска.
-- Параметры: (lecture_id, model_name, model_revision)
-- Сначала проверьте index_ready_at в той же согласованной read-транзакции,
-- затем выполните этот запрос. Приложение должно проверить, что вернулась
-- ожидаемая модель/ревизия и что embedding имеет 1536 байт.
-- similarity считается приложением, а не этим SQL-запросом.
SELECT
    c.id AS chunk_id,
    c.position,
    c.start_ms,
    c.end_ms,
    c.text,
    c.first_segment_id,
    c.last_segment_id,
    e.model_name,
    e.model_revision,
    e.dimensions,
    e.embedding
FROM search_chunks AS c
JOIN chunk_embeddings AS e
    ON e.chunk_id = c.id
JOIN lectures AS l
    ON l.id = c.lecture_id
WHERE c.lecture_id = %s
  AND l.index_ready_at IS NOT NULL
  AND e.model_name = %s
  AND e.model_revision = %s
ORDER BY c.position ASC;


-- ---------------------------------------------------------------------------
-- 5. Результаты поиска, уже отсортированные приложением по similarity.
-- После вычисления dot product в Python передайте выбранные chunk_id.
-- Параметры: (lecture_id, chunk_id_1, chunk_id_2, ...)
-- IN (...) нельзя параметризовать одним плейсхолдером: создайте нужное число
-- %s placeholders в приложении и значения передайте отдельным tuple/list.
-- Пример запроса для одного результата:
SELECT
    c.id AS chunk_id,
    c.position,
    c.start_ms,
    c.end_ms,
    c.text,
    c.first_segment_id,
    c.last_segment_id,
    l.id AS lecture_id,
    l.title AS lecture_title
FROM search_chunks AS c
JOIN lectures AS l
    ON l.id = c.lecture_id
WHERE c.lecture_id = %s
  AND c.id IN (%s)
  AND l.index_ready_at IS NOT NULL;


-- ---------------------------------------------------------------------------
-- 6. Главы лекции с временем начала из соответствующего сегмента.
-- Параметр: lecture_id
SELECT
    ch.id,
    ch.position,
    ch.title,
    ch.level,
    ch.start_segment_id,
    s.start_ms
FROM lecture_chapters AS ch
JOIN transcript_segments AS s
    ON s.id = ch.start_segment_id
WHERE ch.lecture_id = %s
ORDER BY ch.position ASC;


-- ---------------------------------------------------------------------------
-- 7. Конспект лекции.
-- Параметр: lecture_id
SELECT
    sm.lecture_id,
    sm.text,
    sm.provider,
    sm.model_name,
    sm.created_at
FROM lecture_summaries AS sm
JOIN lectures AS l
    ON l.id = sm.lecture_id
WHERE sm.lecture_id = %s
  AND l.summary_ready_at IS NOT NULL;


-- ---------------------------------------------------------------------------
-- 8. Текущий статус обработки и последнее задание.
-- Параметр: lecture_id
SELECT
    l.id AS lecture_id,
    l.status AS lecture_status,
    l.transcript_ready_at,
    l.index_ready_at,
    l.summary_ready_at,
    j.id AS job_id,
    j.status AS job_status,
    j.stage,
    j.attempt,
    j.heartbeat_at,
    j.lease_expires_at,
    j.error_code,
    j.error_message,
    j.failed_stage,
    j.started_at,
    j.finished_at,
    j.created_at AS job_created_at
FROM lectures AS l
LEFT JOIN processing_jobs AS j
    ON j.lecture_id = l.id
WHERE l.id = %s
ORDER BY j.created_at DESC, j.id DESC
LIMIT 1;


-- ---------------------------------------------------------------------------
-- 9. История всех заданий по лекции.
-- Параметры: (lecture_id, limit)
SELECT
    id,
    status,
    stage,
    attempt,
    error_code,
    error_message,
    failed_stage,
    started_at,
    finished_at,
    created_at,
    updated_at
FROM processing_jobs
WHERE lecture_id = %s
ORDER BY created_at DESC, id DESC
LIMIT %s;


-- ---------------------------------------------------------------------------
-- 10. Проверка готовности индекса и загрузка чанков в одной согласованной
-- read-транзакции (пример для backend):
--
-- START TRANSACTION WITH CONSISTENT SNAPSHOT;
-- SELECT index_ready_at FROM lectures WHERE id = %s;
-- Если index_ready_at IS NULL — завершить транзакцию и вернуть состояние
-- "индекс ещё не готов".
-- Иначе выполнить запрос №4 до COMMIT.
-- Если поиск уже вернул chunk_id, но при последующем чтении его нет из-за
-- конкурентного retry/переиндексации, приложение должно обработать это как
-- конфликт/устаревший результат (например, HTTP 409), а не выдавать неверные
-- ссылки на чанки.


-- ---------------------------------------------------------------------------
-- 11. Безопасный шаблон retry для лекции в статусе failed.
-- Это шаблон транзакции для backend, а не один автономный запрос.
-- Новый UUID задания передаётся как :new_job_id через параметризацию драйвера.
-- В MySQL/PyMySQL фактически используйте %s вместо :new_job_id.
--
-- START TRANSACTION;
-- SELECT status FROM lectures WHERE id = %s FOR UPDATE;
-- Проверить, что строка существует и status = 'failed'.
-- SELECT COUNT(*) FROM processing_jobs
--   WHERE lecture_id = %s AND status IN ('queued', 'running');
-- Если число не равно 0 — ROLLBACK и отказать в retry.
--
-- Очистка производных данных — только в таком порядке:
-- DELETE FROM lecture_chapters WHERE lecture_id = %s;
-- DELETE FROM lecture_summaries WHERE lecture_id = %s;
-- DELETE e FROM chunk_embeddings AS e
--   JOIN search_chunks AS c ON c.id = e.chunk_id
--   WHERE c.lecture_id = %s;
-- DELETE FROM search_chunks WHERE lecture_id = %s;
-- DELETE FROM transcript_segments WHERE lecture_id = %s;
-- UPDATE lectures
--   SET transcript_ready_at = NULL,
--       index_ready_at = NULL,
--       summary_ready_at = NULL
--   WHERE id = %s;
-- INSERT INTO processing_jobs (id, lecture_id, status, stage)
--   VALUES (%s, %s, 'queued', 'queued');
-- UPDATE lectures SET status = 'queued' WHERE id = %s;
-- COMMIT;
--
-- Важно: приложение должно соблюдать порядок блокировок
-- lectures -> processing_jobs и повторять транзакцию ограниченное число раз
-- при deadlock. Новая попытка создаёт новую строку processing_jobs, история
-- старых заданий сохраняется.
