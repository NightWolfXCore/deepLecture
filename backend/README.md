# deepLecture Backend

PHP 8.3 API + Python 3.11 FastAPI + отдельный worker + MySQL 8.4 + FFmpeg.
Реализация следует `contracts/http-api.md` и `contracts/database.md`.
Фронтенд и production-миграции БД разрабатываются другими участниками.

Реализованы API нового frontend: импорт YouTube/Vimeo, поиск по всем лекциям
(без lecture_id), термины и чат с источниками. Формат — контракт HTTP v1.1.
LLM-настройки нужны и search, и worker: при смене модели пересоздавайте оба
сервиса. Импорт требует новых зависимостей `yt-dlp[default]`, FFmpeg и Deno
(в Dockerfile — 2.5.6); одного изменения `.env` недостаточно, образ пересобирается.
При нативном запуске установите Deno в PATH. Приватные видео/cookies/DRM
не поддерживаются; доступ к платформам и их CDN нужен отдельно.

Если storage volume создан старой версией, подготовьте каталоги перед запуском

```sh
docker compose --env-file .env -f infra/compose.yml run --rm --no-deps --user root php sh -c 'mkdir -p /data/imports /data/downloads; chown 33:33 /data/videos /data/imports; chown 1000:1000 /data/downloads; chmod 2775 /data/videos; chmod 755 /data/imports /data/downloads'
```

В новом volume каталоги и группа worker создаются образами автоматически.
URL хранится в приватном `storage/imports/<UUID>.json`; временные скачивания
изолированы по worker_token. Вместе с БД сохраняйте volume storage.

## Быстрый запуск

Полный запуск объединённого приложения, production-миграции и импорт Qwen3 4B
GGUF описаны в [корневом README](../README.md#запуск). Используйте эту инструкцию
для `dev` и `main`: отдельный checkout frontend и справочная тестовая схема не нужны.

## Конфигурация

PHP: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `STORAGE_ROOT`,
`PYTHON_API_URL`, `MAX_UPLOAD_BYTES` (500 MiB).

Python: те же DB-параметры, `MODEL_CACHE`, `EMBEDDING_MODEL`,
`EMBEDDING_REVISION`, `WHISPER_MODEL`, `WHISPER_REVISION`, `CPU_THREADS`,
`MAX_DURATION_MS`, `FFMPEG_TIMEOUT`, `LEASE_SECONDS`, `HEARTBEAT_SECONDS`,
`POLL_SECONDS`. Значения по умолчанию описаны в `python/app/config.py`.
В Compose общие настройки передаются явно; дополнительные переменные при
необходимости добавляйте в environment соответствующего сервиса.

Конспекты: `LLM_BASE_URL` (URL с `/v1`), `LLM_MODEL`, `LLM_API_KEY`,
`LLM_AUTH_MODE` (`bearer` по умолчанию, `none` — явное отключение авторизации),
`LLM_TIMEOUT`, `LLM_ALLOW_HTTP` (`false` по умолчанию),
`LLM_REASONING_EFFORT` (пусто по умолчанию; `none` для локального Qwen3 4B).
Адаптер использует OpenAI-compatible `/chat/completions` и JSON response format.
Другой провайдер должен поддерживать этот протокол; произвольный API без адаптера
не заработает. HTTP допускается явным `LLM_ALLOW_HTTP=true` для локального
сервера в закрытой сети. Compose передаёт эту настройку, по умолчанию она выключена.
При `bearer` пустой ключ остаётся ошибкой. При `none` заголовок Authorization
не отправляется. Ключи нельзя коммитить или передавать фронтенду.

Шаблоны внешнего API и локального Qwen, команды подготовки весов, реальная
проверка LLM и сценарий офлайн-запуска: [LLM.md](LLM.md).
`MODELS_OFFLINE=true` запрещает загрузчикам ASR/E5 скачивать отсутствующие
веса; нужны уже заполненный `models` и закреплённые ревизии.
Проверенное состояние проекта и пробелы по ТЗ: [STATUS.md](STATUS.md).
Подключение актуального UI без изменения ветки участника: [FRONTEND.md](FRONTEND.md).

Для скачивания весов нужны `huggingface.co` и фактические CDN-домены, на которые
перенаправляет Hugging Face (например, `cas-bridge.xethub.hf.co`). Для конспектов —
домен выбранного LLM API. Разрешайте только необходимые домены в настройках сети.
При ограниченной сети разрешения должны быть применены к работающей среде;
наличие конфигурации само по себе не доказывает доступ.

При пустой ревизии загрузчик разрешает `main` в фактический immutable SHA snapshot
и сохраняет SHA рядом с каждым вектором. Для воспроизводимости задайте одинаковый
точный SHA в `EMBEDDING_REVISION` search/worker и закрепите `WHISPER_REVISION`.
Уже созданный индекс с другой ревизией отклоняется; нужна повторная обработка.
Нельзя подменять эмбеддинги случайными векторами в рабочем приложении.

## Проверка вручную

```sh
curl -f -F 'title=Демонстрационная лекция' -F 'file=@lecture.mp4' \
 http://127.0.0.1:8080/api/v1/lectures
# Подставьте ID из ответа:
curl -f http://127.0.0.1:8080/api/v1/lectures/LECTURE_UUID
curl -f http://127.0.0.1:8080/api/v1/lectures/LECTURE_UUID/transcript
curl -f -H 'Content-Type: application/json' \
 -d '{"lecture_id":"LECTURE_UUID","query":"Какова главная идея?","limit":5}' \
 http://127.0.0.1:8080/api/v1/search
curl -f http://127.0.0.1:8080/api/v1/lectures/LECTURE_UUID/summary
```

Ожидается сначала 202, затем переходы queued → extracting → transcribing →
indexing → summarizing → ready. До готовности соответствующего этапа его API
возвращает 409. Отсутствие LLM-ключа в режиме `bearer` приводит к failed на
summarizing, но готовые
транскрипт и индекс сохраняются. Повтор через `POST /lectures/{id}/retry`
атомарно сбрасывает производные данные и запускает всё заново.

`python -m app.worker --once` выполняет не более одной попытки; exit 0 означает
завершение попытки, **а не успешное создание конспекта**. Проверяйте состояние
лекции и `availability`. В логах используются безопасные коды без ключей и SQL.

## Тесты

Тестовый набор и инструкции доступны в [ветке backend](https://github.com/NightWolfXCore/deepLecture/tree/backend/tests/backend).
В dev они сохраняются отдельно от runtime-изменений; в main тестов нет.
Их подмены моделей подтверждают протоколы и обработку данных, а не качество ML.

## Ветки и тесты

Объединённое приложение публикуется в `main` по прямому запросу пользователя.
Тестовые файлы и workflow остаются в ветках разработки, в итоговом дереве
`main` их нет. Результаты проверок: [RELEASE.md](RELEASE.md).
