# deepLecture Backend

PHP 8.3 API + Python 3.11 FastAPI + отдельный worker + MySQL 8.4 + FFmpeg.
Реализация следует `contracts/http-api.md` и `contracts/database.md`.
Фронтенд и production-миграции БД разрабатываются другими участниками.

## Быстрый запуск

Из корня репозитория, Docker Engine + Compose v2:

```sh
cp .env.example .env
# Укажите свой локальный DB_PASSWORD и настройки LLM в .env.
docker compose --env-file .env -f infra/compose.yml build
docker compose --env-file .env -f infra/compose.yml up -d mysql
```

Примените миграции участника БД согласно контракту. В этой ветке нет чужой
production-системы миграций; приложение намеренно не выполняет DDL при старте.
Для временной локальной разработки до получения миграций можно явно применить
справочную схему из тестовой части ветки на **новую пустую БД**:

```sh
docker compose --env-file .env -f infra/compose.yml exec -T mysql sh -c \
 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -u"$MYSQL_USER" "$MYSQL_DATABASE"' \
 < tests/backend/fixtures/schema.sql
```

Не применяйте этот файл поверх существующей БД и не используйте как замену
согласованным production-миграциям. После подготовки схемы:

```sh
docker compose --env-file .env -f infra/compose.yml up -d php search worker nginx
curl -f http://127.0.0.1:8080/api/v1/health
curl -f http://127.0.0.1:8080/api/v1/ready
```

`health` проверяет PHP; `ready` — соединение с БД и загруженную модель поиска.
Это не подтверждение готовности конспектов: LLM проверяется реальным заданием.
API доступен только на loopback; для удалённого доступа нужен собственный
reverse proxy с TLS и контролем доступа. У MVP нет пользовательской авторизации.

Первая загрузка моделей может занять несколько минут и требует места на диске.
Веса остаются в volume `models`. Видео — `storage`, MySQL — `mysql-data`.
`docker compose ... down` сохраняет данные; `down -v` удаляет их, для рабочей
среды эту команду без намерения удалить данные не используйте.
Live-процессы после перезапуска среды нужно запустить повторно.

## Конфигурация

PHP: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `STORAGE_ROOT`,
`PYTHON_API_URL`, `MAX_UPLOAD_BYTES` (500 MiB).

Python: те же DB-параметры, `MODEL_CACHE`, `EMBEDDING_MODEL`,
`EMBEDDING_REVISION`, `WHISPER_MODEL`, `WHISPER_REVISION`, `CPU_THREADS`,
`MAX_DURATION_MS`, `FFMPEG_TIMEOUT`, `LEASE_SECONDS`, `HEARTBEAT_SECONDS`,
`POLL_SECONDS`. Значения по умолчанию описаны в `python/app/config.py`.
В Compose общие настройки передаются явно; дополнительные переменные при
необходимости добавляйте в environment соответствующего сервиса.

Конспекты: `LLM_BASE_URL` (HTTPS URL с `/v1`), `LLM_MODEL`, `LLM_API_KEY`.
Адаптер использует OpenAI-compatible `/chat/completions` и JSON response format.
Другой провайдер должен поддерживать этот протокол; произвольный API без адаптера
не заработает. `LLM_ALLOW_HTTP` допускается только в изолированных локальных тестах.
В production Compose он не включён. Ключи нельзя коммитить или передавать фронтенду.

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
возвращает 409. Отсутствие LLM-ключа приводит к failed на summarizing, но готовые
транскрипт и индекс сохраняются. Повтор через `POST /lectures/{id}/retry`
атомарно сбрасывает производные данные и запускает всё заново.

`python -m app.worker --once` выполняет не более одной попытки; exit 0 означает
завершение попытки, **а не успешное создание конспекта**. Проверяйте состояние
лекции и `availability`. В логах используются безопасные коды без ключей и SQL.

## Тесты: только ветка backend

Основной воспроизводимый прогон без оплачиваемых API и загрузки весов:

```sh
sh tests/backend/run.sh
```

Тестовый Compose использует отдельный project `deeplecture-test`, БД
`deeplecture_test` и собственные volumes, которые удаляются после прогона.
Производственные services не переводятся в mock-режим: тестовая подмена модели
подключается отдельным модулем только в test override.
Все команды и ограничения — в `tests/backend/README.md`.

Локально Python unit-тесты (Python 3.11/3.12, FFmpeg в PATH):

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r backend/python/requirements.txt -r tests/backend/requirements.txt
PYTHONPATH=backend/python python -m pytest -q tests/backend/python
```

Без `RUN_MYSQL_TESTS=1` DB-тесты будут явно пропущены. Их нужно отдельно
выполнить на изолированной БД `_test`; интеграционный launcher делает это.
PHP unit: `php tests/backend/php/unit.php` (PHP с mbstring, curl, pdo_mysql).
Полные локальные ML-зависимости: `requirements-ml.txt`, CPU torch отдельно по
команде из Python Dockerfile. Для HTTP/unit тестов они не требуются.

Автотесты проверяют API, транзакции, lease, обработку ошибок, формат векторов
и FFmpeg. Подмены ASR/E5/LLM проверяют обработку данных, но не качество моделей.
Для итоговой приёмки нужен реальный ролик с речью и минимум пять запросов-
перефразирований с заранее указанными ожидаемыми фрагментами.

## Перенос в main без тестов

Ветка `main` этой работой не изменяется. Реализация и тесты сохраняются отдельными
коммитами: runtime-коммит `feat: implement PHP and Python lecture backend`,
затем тестовый `test: add isolated backend validation suite`.
При интеграции в main переносите только runtime-коммит (и необходимые контракты
из dev), а не сливайте всю backend-ветку. Тестовый коммит не cherry-pick'ать.
Runtime Compose не ссылается на тестовые файлы: production-схему предоставляет
участник БД. Раздел с временной справочной схемой выше относится только к backend.

Git не исключает файлы из merge по `.gitignore`: полный merge backend перенесёт
и тесты. Перед публикацией проверяйте состав изменения. CI и тесты можно
продолжать выполнять в backend независимо от состава main.
