# Контракт хранения MVP, версия 1

Статус: контракт версии 1. Миграции опубликованы владельцем БД в
`repository_sql` (проверяемая ревизия и результаты — `backend/STATUS.md`).
Бэкенд не копирует их в собственную production-схему и не выполняет DDL при старте.
Движок MySQL 8.0+, InnoDB, текст utf8mb4. Все даты в UTC,
`DATETIME(3)`. Сессии PHP и Python устанавливают UTC.
ID — UUID `CHAR(36)` с ASCII binary collation, генерируются приложениями.
Таймкоды — целые миллисекунды, BIGINT UNSIGNED. Текст — TEXT/MEDIUMTEXT.
Схема принадлежит участнику БД; прямые SQL-запросы — участнику бэкенда.

## Таблицы

Обозначение `?` означает NULL; остальные поля NOT NULL, если не указано иначе.

### lectures

| Поле | Тип / смысл |
|---|---|
| id | UUID, PK |
| title | VARCHAR(200) |
| original_filename | VARCHAR(255), только метаданные, не путь |
| storage_key | VARCHAR(255), UNIQUE, относительный `videos/<uuid>.mp4` или `.webm` |
| mime_type | VARCHAR(100) |
| size_bytes | BIGINT UNSIGNED |
| duration_ms | BIGINT UNSIGNED ?; устанавливается ffprobe |
| status | VARCHAR(20): queued/extracting/transcribing/indexing/summarizing/ready/failed |
| transcript_ready_at | DATETIME(3) ? |
| index_ready_at | DATETIME(3) ? |
| summary_ready_at | DATETIME(3) ? |
| created_at, updated_at | DATETIME(3) |

Availability API: соответствующее `*_ready_at IS NOT NULL`.
Для импорта, пока скачивание не завершено, `size_bytes=0`, а API скрывает
media_url. Имя и storage_key заранее генерируются по UUID, как у MP4-загрузки.
Канонический URL хранится в приватном `storage/imports/<lecture_id>.json`;
очередь по-прежнему находится в MySQL. Это MVP-хранилище источника без изменения
миграций участника БД; его следует сохранять вместе с volume storage.
После ffprobe worker публикует файл и size_bytes только под актуальным lease.
Термины вычисляются из сегментов, чат не сохраняется; новых таблиц не требуется.
`duration_ms`, если задана, должна быть > 0.
Индекс `(created_at, id)` для каталога.

### processing_jobs

| Поле | Тип / смысл |
|---|---|
| id | UUID, PK |
| lecture_id | UUID, FK lectures |
| status | VARCHAR(16): queued/running/succeeded/failed |
| stage | VARCHAR(20): queued/extracting/transcribing/indexing/summarizing/ready |
| attempt | INT UNSIGNED, default 0; счётчик захватов этого задания |
| worker_token | UUID ?; новый случайный токен при каждом захвате |
| heartbeat_at, lease_expires_at | DATETIME(3) ? |
| error_code | VARCHAR(64) ?; безопасный машинный код |
| error_message | VARCHAR(500) ?; безопасное сообщение для пользователя |
| failed_stage | VARCHAR(20) ? |
| started_at, finished_at | DATETIME(3) ? |
| created_at, updated_at | DATETIME(3) |

Индексы: `(status, created_at, id)`, `(status, lease_expires_at)`,
`(lecture_id, created_at, id)`. FK `lecture_id → lectures.id`, ON DELETE CASCADE.
Не более одного queued/running на лекцию — обеспечивается транзакциями,
которые сначала блокируют строку lectures. MySQL не имеет обычного partial
unique index; не предлагать неработающий `UNIQUE ... WHERE`.
Новый retry создаёт новую строку задания; старые задания сохраняются как история.

### transcript_segments

| Поле | Тип / смысл |
|---|---|
| id | UUID, PK |
| lecture_id | UUID, FK lectures |
| position | INT UNSIGNED |
| start_ms, end_ms | BIGINT UNSIGNED |
| text | TEXT |

UNIQUE `(lecture_id, position)`, индекс `(lecture_id, start_ms)`.
FK lectures ON DELETE CASCADE. `end_ms > start_ms`, текст непустой.
Проверку выхода за `duration_ms` выполняет worker; небольшой выход ASR
за длительность округляется/ограничивается перед записью.

### search_chunks

| Поле | Тип / смысл |
|---|---|
| id | UUID, PK |
| lecture_id | UUID, FK lectures |
| position | INT UNSIGNED |
| start_ms, end_ms | BIGINT UNSIGNED |
| text | TEXT |
| first_segment_id, last_segment_id | UUID, FK transcript_segments |

UNIQUE `(lecture_id, position)`, индекс `(lecture_id, start_ms)`.
FK lectures ON DELETE CASCADE; FK сегментов RESTRICT.
Оба сегмента принадлежат той же лекции; first.position <= last.position.
Это проверяется worker в транзакции: обычные FK не проверяют равенство lecture_id.
`end_ms > start_ms`. Время охватывает исходные сегменты; chunks могут перекрываться.
Удаление производных данных выполняется в указанном ниже порядке.

### chunk_embeddings

| Поле | Тип / смысл |
|---|---|
| chunk_id | UUID, PK и FK search_chunks, ON DELETE CASCADE |
| model_name | VARCHAR(128), `intfloat/multilingual-e5-small` |
| model_revision | VARCHAR(128), точная зафиксированная ревизия модели |
| dimensions | SMALLINT UNSIGNED, 384 |
| embedding | BLOB, ровно 1536 байт |

Формат: 384 нормализованных числа IEEE 754 float32 little-endian, подряд,
без JSON и без заголовка. Python: `numpy.asarray(vector, dtype='<f4').tobytes()`;
чтение: `numpy.frombuffer(blob, dtype='<f4')`.
CHECK dimensions = 384 и OCTET_LENGTH(embedding) = dimensions * 4.
Worker проверяет конечность значений и норму до записи; FastAPI валидирует
формат, ревизию и размерность перед поиском. Несовместимый индекс даёт ошибку,
а не молчаливое смешивание разных моделей.

Вектор запроса нормализован так же. Dot product равен cosine similarity.
Поиск читает chunks через lecture_id и JOIN embeddings; MySQL сам не считает
близость. Замена модели требует переиндексации всей выбранной лекции.

### lecture_summaries

| Поле | Тип / смысл |
|---|---|
| lecture_id | UUID, PK и FK lectures, ON DELETE CASCADE |
| text | MEDIUMTEXT, обычный текст |
| provider | VARCHAR(64), имя адаптера, без секрета |
| model_name | VARCHAR(128) |
| created_at | DATETIME(3) |

### lecture_chapters

| Поле | Тип / смысл |
|---|---|
| id | UUID, PK |
| lecture_id | UUID, FK lectures, ON DELETE CASCADE |
| position | INT UNSIGNED |
| title | VARCHAR(200) |
| level | TINYINT UNSIGNED, 1 или 2 |
| start_segment_id | UUID, FK transcript_segments, RESTRICT |

UNIQUE `(lecture_id, position)`. `start_ms` не дублируется: читается JOIN
по `start_segment_id`. Принадлежность сегмента лекции, порядок времени и
правильное чередование уровней проверяет worker перед транзакцией публикации.

## Кто читает и пишет

| Компонент | Чтение | Запись |
|---|---|---|
| PHP | Все таблицы для публичного API | lectures + processing_jobs при загрузке/повторе; удаление производных данных при retry |
| Python FastAPI | lectures, search_chunks, chunk_embeddings | Нет |
| Python worker | Все необходимые таблицы | Статусы, lease, сегменты, chunks, embeddings, конспекты, главы |
| Nginx / браузер | Нет доступа к БД | Нет |

Миграционная роль отдельно от runtime-ролей. Runtime не выполняет DDL.
Для PHP, worker и read-only FastAPI рекомендуются отдельные учётные данные.

## Конкурентность и публикация

- Все изменения для одной лекции сначала блокируют её строку
  `SELECT ... FOR UPDATE`, затем строку задания. Единый порядок блокировок
  уменьшает риск deadlock; при deadlock вся короткая транзакция повторяется
  ограниченное число раз.
- Worker выбирает кандидата на queued/просроченное running без долгой блокировки,
  затем в короткой транзакции блокирует lectures и processing_jobs, повторно
  проверяет статус/срок и назначает новый worker_token и lease. Потерявший
  гонку worker выбирает другое задание. SKIP LOCKED необязателен для MVP.
- Начальный lease 120 секунд; heartbeat каждые 20 секунд независимо от длительного
  вызова модели. Если heartbeat нельзя поддерживать, worker не должен продолжать
  публикацию после истечения lease.
- Каждая запись результата проверяет текущий worker_token, status=running и
  непросроченный lease внутри транзакции с блокировкой задания. Устаревший worker
  не публикует данные и не меняет статус. Артефакты аудио привязаны к token,
  чтобы старый процесс не перезаписал файл нового.
- При повторном захвате просроченного running новый worker в транзакции
  очищает производные данные и readiness и начинает с extracting.
  Максимум три захвата одного задания; затем failed с кодом WORKER_LEASE_EXPIRED.
  Пользовательский retry создаёт отдельное новое задание.
- Сегменты и transcript_ready_at публикуются в одной транзакции. Chunks,
  embeddings и index_ready_at — в одной транзакции. Конспект, главы и
  summary_ready_at — в одной транзакции. Читатель никогда не видит готовый
  флаг при неполном наборе данных. Сетевые/ML-операции выполняются до транзакции.
- PHP загружает chunks для search-ответа и проверяет index_ready_at в одной
  согласованной read-транзакции. Если chunks из ответа Python отсутствуют
  после конкурентного retry, возвращается 409. ID chunks при новой обработке
  всегда новые, поэтому старый индекс не может незаметно совпасть с новым.
- Сброс производных данных: chapters → summaries → embeddings → chunks → segments,
  затем обнуление readiness. FK не отключаются. Исходное видео остаётся.
- Retry: блокировка lectures, проверка failed и отсутствия queued/running,
  сброс результатов, INSERT нового job, status=queued, COMMIT.
- Ошибка этапа: job.status=failed, lecture.status=failed, безопасный error_code,
  failed_stage и finished_at. Уже опубликованные этапы сохраняются до retry.

## Что должен предоставить участник БД

1. Нумерованные миграции создания таблиц, FK, CHECK и индексов.
2. Команду применения на пустой MySQL и безопасный повторный запуск мигратора.
3. Фикстуры: queued, ready и failed на summarizing, согласованные с HTTP-контрактом.
   Тестовый готовый индекс должен содержать реальные совместимые эмбеддинги,
   либо быть явно помечен как UI-only: случайные векторы не проверяют поиск.
4. Проверки ограничений, каскадов, порядка очистки, транзакционного retry,
   двух конкурентных захватов одного задания и отказа устаревшему worker_token.
5. Согласованный с бэкендом набор параметризованных примеров SELECT/JOIN для
   каталога, сегментов, поиска и глав. Реализация repository-слоя остаётся у бэкенда.

Внутренние имена SQL и внешние JSON-поля намеренно совпадают там, где это возможно.
Секретов и персональных данных в миграциях и seeds быть не должно.

## Дополнения dev: категории и ограничение конспекта

Новые неизменяемые миграции `002_lecture_categories.sql` и
`003_summary_generation_cooldown.sql` добавляют в lectures:
- category VARCHAR(32) NOT NULL DEFAULT 'other', CHECK и индекс каталога;
- summary_generated_at DATETIME(3) NULL — последний успешный конспект (UTC).

003 переносит созданные ранее конспекты из lecture_summaries.created_at.
Удаление конспекта перед повтором не сбрасывает summary_generated_at.
Поле обновляется в той же fenced-транзакции, что и публикация конспекта.
Задание со stage=summarizing при готовых транскрипте и индексе выполняет
только LLM; lease recovery такого задания сохраняет сегменты и векторы.
Существующие SQL-миграции не изменялись; исходная ветка участника БД не менялась.
