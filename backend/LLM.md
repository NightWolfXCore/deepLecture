# LLM: локальный Qwen3 4B и внешний API

Основной сценарий — Qwen3 4B Q4 GGUF в `storage/llm/Qwen3-4B-Q4_K_M.gguf`.
Полный запуск обычными командами `docker compose`: [README](../README.md#запуск).
`.env` создаётся автоматически при старте MySQL/Ollama, если его нет;
существующий файл сохраняется. [Импорт GGUF](GGUF.md).

Локальный сервер Ollama работает внутри Compose без отдельной установки.
`LLM_BASE_URL=http://ollama:11434/v1`, `LLM_MODEL=Qwen3-4B-Q4_K_M`,
`LLM_AUTH_MODE=none`, `LLM_ALLOW_HTTP=true`, `LLM_REASONING_EFFORT=none`.
API использует `/chat/completions` и JSON mode. Главы привязываются к реальным
ID сегментов; придуманные моделью ID отклоняются. Для чата источники также
проверяются. Фактическую точность ответов нужно оценить на лекции.

```text
docker compose exec ollama ollama show Qwen3-4B-Q4_K_M
docker compose run --rm --no-deps worker python -m app.check_llm
```

`show` проверяет наличие и метаданные; `check_llm` делает настоящий запрос.
Для Q4_K_M нужны именно соответствующие исходные веса, импорт не квантизует.
Лимит контекста 16384 токена. Скорость/память на целевом CPU не измерены.

## Внешний OpenAI-compatible API

В существующем `.env` задайте `LLM_BASE_URL=https://.../v1`, `LLM_MODEL`,
`LLM_API_KEY`, `LLM_AUTH_MODE=bearer`, `LLM_ALLOW_HTTP=false` и пустой
`LLM_REASONING_EFFORT`. Шаблон: [external.env.example](../infra/llm/external.env.example).
Ключ не коммитится и не передаётся браузеру. Нужны поддержка JSON mode,
доступная модель и квота провайдера; бесплатные тарифы не подтверждены.
После изменения настроек:

```text
docker compose up -d --force-recreate search worker
docker compose run --rm --no-deps worker python -m app.check_llm
```

## Подготовка к офлайн-работе

Заранее соберите все образы, примените миграции, импортируйте GGUF и выполните
`python -m app.prepare_models` через worker. Сохраните volumes MySQL, storage,
Whisper/E5 и Ollama. Зафиксируйте реальные snapshot SHA E5/Whisper в `.env`;
одинаковая ревизия E5 нужна search и worker. В `.env` задайте `MODELS_OFFLINE=true`
и пересоздайте search/worker. Это запрещает HF-загрузки отсутствующих весов.
Bootstrap поставляется локально. Для полной проверки отключите интернет хоста,
загрузите новое локальное видео и проверьте распознавание, поиск, конспект и чат.
Пока такой сценарий целиком не пройден, офлайн-работа остаётся неподтверждённой.
Не используйте `down -v`: эта команда удалит сохранённые данные и модели.
