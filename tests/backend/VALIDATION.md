# Результаты локальной проверки

## 28 октября 2026: локальный LLM и интеграция веток

- После обновления на Qwen3.5 4B: `SKIP_BUILD=1 sh tests/backend/run.sh` —
  **82 Python/HTTP-теста без пропусков + 17 PHP-проверок**, exit 0.
  Настоящие MySQL, PHP-FPM, Nginx, FastAPI, FFmpeg; ASR/E5/LLM — явные подмены.
- Тот же набор повторён на production-миграции владельца БД из
  `repository_sql@6d73df762931f0d955f6567c1d912da495752307`:
  **82 + 17**, без пропусков, exit 0.
  Сначала на пустой `deeplecture_test` выполнен настоящий `migrate.py`, затем
  повторный запуск (`пропуск 001_initial_schema.sql`) и `--status` (`[x]`).
  Это проверяет и мигратор, и совместимость реальных запросов PHP/Python.
  Миграции не изменялись; использован временный read-only checkout.
- Дополнительно запущен `tests/test_db.py` владельца: он сообщает 11/11;
  проверка seed-фикстур внутри `test_fixtures_consistent` пропущена, поскольку
  seeds не загружались. Поэтому результат не подтверждает корректность seeds.
- Production Python image собран; `pip check` и импорты torch,
  sentence-transformers, faster-whisper, app.worker/app.api прошли.
  Проверка импортов не выполняет inference. CA использован через build secret;
  сессионный сертификат больше не дописывается в постоянный слой образа.
- `docker compose ... config` проверен для Ollama + offline overrides:
  `qwen3.5:4b`, `reasoning_effort=none`, `LLM_AUTH_MODE=none`, явный HTTP,
  cache-only модели, `internal: true` и отсутствие опубликованного порта Ollama.
  Docker manifest `ollama/ollama:0.40.12` существует. Исходники этой версии
  подтверждают поддержку `reasoning_effort`; выполнение Qwen не проверено.
- При финальном fetch появился UI в
  `frontend@dba72f67a879ab125d92383bee1dc8003a41ad55`.
  Через `compose.frontend.yml` проверена отдача HTML/JS реальным Nginx,
  `USE_MOCK=false` и выполнение настоящего `RealApi` против PHP/MySQL:
  каталог и ошибки карточки/транскрипта/конспекта/поиска.
  `node tests/backend/frontend_api_smoke.mjs` — exit 0.
  Это проверка клиента и API, без DOM, XHR upload и воспроизведения видео.

Первый новый прогон остановился из-за недоступного MySQL SQL bind-файла
(права checkout). После исправления прав прошло 52 Python-теста, но HTTP client
не достиг локального health endpoint: унаследованный proxy перехватывал адреса
Compose. Исправлено: прямой локальный HTTP в тесте (`trust_env=False`) и
добавление внутренних имён в NO_PROXY без отключения внешнего proxy.
После этих исправлений оба полных прогона выше прошли. Сохранилось одно
предупреждение Starlette о deprecated AnyIO alias; ошибок/пропусков нет.

Воспроизведение интеграции с владельцем БД:

```sh
mkdir -p /tmp/deeplecture-db-integration
git archive 6d73df762931f0d955f6567c1d912da495752307 \
  | tar -x -C /tmp/deeplecture-db-integration
# Только в отдельном Compose-проекте deeplecture-test и БД deeplecture_test:
# 1. Инициализировать MySQL с пустым TEST_SCHEMA_FILE вместо fixture SQL.
# 2. Через test-runner с /tmp/deeplecture-db-integration:/db:ro выполнить:
#    python /db/migrate.py (два раза), затем --status, tests/test_db.py.
# 3. На той же БД, до её удаления: SKIP_BUILD=1 sh tests/backend/run.sh.
```

Для запуска только backend-набора непосредственно с SQL-файлом миграции
есть команда с `TEST_SCHEMA_FILE` в [README.md](README.md). Это отдельный
способ проверки SQL; он сам по себе не проверяет мигратор.

**Не подтверждено:** реальный Whisper, смысловая релевантность настоящего E5,
реальная Qwen3.5/внешняя LLM, квантизация Q4_K_M, производительность на лекции,
полное приложение без интернета, поведение UI в браузере,
импорт YouTube/Vimeo (ещё не реализован).
Запросы к Hugging Face и registry.ollama.ai получили CONNECT 403; LLM-ключа нет.
Тесты reasoning/auth используют MockTransport и не доказывают работу модели.
Полная сверка требований — [backend/STATUS.md](../../backend/STATUS.md).

## Предыдущая проверка, 26 октября 2026

Проверено в облачной среде 26 октября 2026 года.

- PHP 8.3 FPM image собран; PHP source lint пройден, 17 unit assertions пройдены.
- Production Python image собран; nonroot UID 1000 импортирует torch 2.6.0+cpu,
  sentence-transformers 3.4.1, faster-whisper 1.1.1, app.worker и app.api.
  `pip check` не обнаружил конфликтов зависимостей; владельцы audio/videos проверены.
- `SKIP_BUILD=1 sh tests/backend/run.sh`: 67 pytest-тестов пройдены без пропусков.
- Повторный запуск с пустыми test volumes также прошёл: 67 pytest + 17 PHP.
- Использованы настоящие MySQL 8.4, Nginx 1.27, PHP-FPM и production FastAPI
  с явно внедрённой тестовой моделью, настоящие FFmpeg/ffprobe.
- Worker проверен на реальной MySQL: конкурентный захват, lease recovery,
  fencing старого токена, атомарная публикация и полный pipeline с тестовыми
  адаптерами ASR/E5/LLM. Проверена ошибка LLM при сохранении готового поиска.
- HTTP проверяет MP4/WebM, MIME/размер, каталог, таймкоды, конспект, поиск,
  ошибки, конкурентный retry, медиа Range/HEAD и несовместимую ревизию векторов.

В облачной сети сборка использовала явные proxy build args, DNS mapping proxy
и build secret с доверенным CA bundle. Проверка TLS/подписей не отключалась.
Из-за Docker VFS кэш сборки пришлось очистить; после этого тестовый launcher
повторно проверен на заранее собранных образах. Обычная команда без SKIP_BUILD
настроена на сборку этих же Dockerfile, но полный автоматический build+test
в этой proxy-среде одной командой не проверен.

Ограничения: реальный inference Whisper/E5 и качество семантических результатов
не проверены: Hugging Face возвращает CONNECT 403. Настоящий вызов LLM не
выполнялся: credential не предоставлен. Успешные тесты используют подмены
и не означают, что эти внешние операции уже работают.
Production-миграции остаются в зоне ответственности участника БД;
использована только справочная тестовая схема, соответствующая контракту.

При прогоне были предупреждения библиотек о deprecated AnyIO alias и
недоступном read-only каталоге pytest cache. Для последнего указан writable
cache_dir в pytest.ini; предупреждения не скрываются и не меняют exit status.
