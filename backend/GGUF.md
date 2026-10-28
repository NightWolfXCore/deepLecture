# Локальный Qwen3 4B Q4 GGUF

Используйте полный файл Qwen3 4B GGUF, предпочтительно Q4_K_M.
Поместите его в `storage/llm/Qwen3-4B-Q4_K_M.gguf`, вне Git и Docker build-контекста.
Файл подключается к Ollama как `/imports/Qwen3-4B-Q4_K_M.gguf` только для чтения.
`infra/llm/Modelfile.gguf` задаёт путь и контекст 16384 токена.

```text
docker compose up -d ollama
docker compose exec ollama ollama create Qwen3-4B-Q4_K_M -f /config/Modelfile
docker compose exec ollama ollama show Qwen3-4B-Q4_K_M
docker compose run --rm --no-deps worker python -m app.check_llm
```

Создайте `.env` по [корневой инструкции](../README.md#запуск) до этих команд.
`LLM_MODEL=Qwen3-4B-Q4_K_M` должен совпадать с именем импорта.
Ollama отдельно на хост устанавливать не нужно. `ollama pull` для импортируемого
GGUF не нужен. Копирования файла недостаточно: обязателен `ollama create`.
Импорт не квантизует модель. Квантизацию проверяйте через `ollama show`,
а не по размеру файла. Не подходят mmproj, адаптеры и отдельные split-shards.

После изменения `.env` пересоздайте приложения:

```text
docker compose up -d --force-recreate search worker
```

Настоящий импорт и inference требуют файла пользователя и достаточной RAM.
Их успешность подтверждается `show`, `check_llm` и обработкой реального видео.
