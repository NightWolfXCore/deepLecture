# LLM и подготовка офлайн-демонстрации

Внешний API и локальная модель используют один адаптер `/v1/chat/completions`.
Он требует JSON mode, проверяет конспект и привязывает каждую главу к ID
существующего сегмента. Время главы берётся из транскрипта, а не из ответа LLM.
Реальная корректность содержания требует отдельного просмотра результата.

## Локальный Qwen через Ollama

Отдельная установка Ollama на хост не требуется. Compose скачивает готовый
образ `ollama/ollama:0.40.12` и запускает сервер в контейнере. `docker compose
build` собирает наши PHP/Python-сервисы; веса Qwen в образ Ollama не входят.
Один раз скачайте их через `ollama pull`, они сохранятся в `ollama-models`.

Из корня репозитория. Не перезаписывайте уже настроенные файлы:

```sh
cp -n .env.example .env
cp -n infra/llm/ollama.env.example .env.llm
# Задайте свой DB_PASSWORD в .env. .env и .env.llm исключены из Git.
dcl() {
  docker compose --env-file .env --env-file .env.llm \
    -f infra/compose.yml -f infra/compose.ollama.yml "$@"
}
dcl build
dcl up -d ollama
dcl exec ollama ollama pull qwen3.5:4b
dcl exec ollama ollama show qwen3.5:4b
dcl run --rm --no-deps worker python -m app.check_llm
```

Ollama слушает только внутри сети Compose, порт наружу не публикуется.
`LLM_BASE_URL=http://ollama:11434/v1`, `LLM_AUTH_MODE=none` и
`LLM_ALLOW_HTTP=true` — независимые явные настройки. Пустой ключ в обычном
режиме `bearer` по-прежнему отклоняется. Передача фиктивного ключа не нужна.
Healthcheck Ollama подтверждает только работу сервера, не наличие модели:
обязательно выполните `pull`, `show` и реальный `check_llm`.

Шаблон задаёт 16384 токена контекста, один параллельный запрос и 300 секунд
таймаута на попытку. Возможны три попытки при сетевой ошибке/429/5xx.
Контекст включает системный prompt, JSON с русским текстом и до 3000 выходных
токенов. Для Qwen3.5 4B задано `LLM_REASONING_EFFORT=none`: Ollama отключает
thinking и формирует конечный JSON в выделенном лимите. У внешнего API эта
настройка по умолчанию пустая, поле не отправляется. Скорость, память и качество
на CPU ещё нужно измерить на лекции; модель может вернуть некорректный JSON/ID —
такой ответ не публикуется.

Предпочтительная квантизация — Q4_K_M. Фактическую квантизацию и digest тега
`qwen3.5:4b` проверьте через `ollama show` после скачивания и сохраните в отчёте.
Каталог модели: https://ollama.com/library/qwen3.5:4b.
Для уже скачанного GGUF: [проверка квантизации и пошаговый импорт](GGUF.md).
Для Windows: [тот же сценарий в PowerShell](GGUF-WINDOWS.md).
Другой сервер, например llama.cpp с этим GGUF, должен предоставлять тот же
OpenAI-compatible JSON mode. Его URL/модель задаются в `.env.llm`; для сервера
без авторизации используйте явный `none`, для HTTP дополнительно `LLM_ALLOW_HTTP`.
Compose-имя `ollama` относится только к приложенному сервису Ollama.

Если `.env.llm` уже создан для старой модели, измените в нём только
`LLM_MODEL=qwen3.5:4b`, добавьте `LLM_REASONING_EFFORT=none` и, если закрепляли
старый образ, `OLLAMA_IMAGE=ollama/ollama:0.40.12`. Автоматически пользовательские
конфигурации не перезаписываются. Обновление контейнера и модели:

```sh
dcl pull ollama
dcl up -d ollama
dcl exec ollama ollama pull qwen3.5:4b
dcl exec ollama ollama show qwen3.5:4b
dcl up -d --build worker
dcl up -d --build search
dcl exec worker python -m app.check_llm
```

Поддержка параметра reasoning описана в [OpenAI compatibility Ollama](https://github.com/ollama/ollama/blob/v0.40.12/docs/api/openai-compatibility.mdx).
Проверка существования образа и протокола не заменяет реальный inference.

После подготовки схемы БД по инструкции владельца:

```sh
dcl up -d mysql
# Примените миграции на пустой БД. Файлы из repository_sql не входят в backend.
dcl up -d php search worker nginx
```

Не передавайте ключи или локальный LLM URL браузеру. Если локальный сервер
публикуется за пределами закрытой сети, настройте TLS и авторизацию.

## Внешний провайдер

```sh
cp -n infra/llm/external.env.example .env.llm
# В НОВОМ .env.llm задайте HTTPS URL, имя доступной модели и личный ключ.
docker compose --env-file .env --env-file .env.llm -f infra/compose.yml \
  run --rm --no-deps worker python -m app.check_llm
```

Если `.env.llm` уже содержит Ollama-настройки, создайте другое имя, например
`.env.llm-external`, и используйте его в `--env-file`. Шаблон не активирует
бесплатный тариф. Квоты Groq/OpenRouter, доступность моделей и JSON mode не
проверены; сначала проверяйте условия провайдера и реальный smoke-вызов.
Для OpenAI без бюджета этот вариант не нужен. TLS-проверка всегда включена.

## Подготовка весов и образов заранее

При доступном интернете, в том же проекте и с теми же volumes:

```sh
dcl pull mysql nginx ollama
dcl build php search worker
dcl run --rm --no-deps worker python -m app.prepare_models
```

Команда загружает настоящие Whisper и E5. Запишите выведенный immutable SHA
E5 как `EMBEDDING_REVISION` в `.env`; найдите фактический SHA snapshot Whisper
в `/models/models--Systran--faster-whisper-small/snapshots` и закрепите его в
`WHISPER_REVISION`. Одинаковый E5 SHA нужен search и worker; смена модели или
ревизии требует повторной индексации. До отключения сети повторите подготовку
с `MODELS_OFFLINE=true` и реальный `check_llm`.

Нужны образы, Python-зависимости, volumes `models`, `ollama-models`, `storage`,
`mysql-data`, схема БД и локальное видео. Не выполняйте `down -v`: это удаляет
данные. При переносе на другой компьютер заранее перенесите образы и volumes.
Внешние CDN интерфейса также необходимо заменить локальными файлами силами
фронтенда; одно наличие весов не делает всё приложение офлайн.

## Проверка с отключённой внешней сетью

Сначала остановите обычный стек (`dcl down`, **без `-v`**), чтобы Compose
пересоздал сеть с `internal: true`. Запустите те же сохранённые данные:

```sh
dco() {
  docker compose --env-file .env --env-file .env.llm \
    -f infra/compose.yml -f infra/compose.ollama.yml \
    -f infra/compose.offline.yml "$@"
}
dco up -d --no-build --pull never
dco exec worker python -m app.check_llm
curl -f http://127.0.0.1:8080/api/v1/ready
```

Override запрещает HF-загрузки и внешний маршрут контейнеров. На облачном
хосте унаследованный HTTP-прокси также должен быть недоступен из этой сети;
подтвердите это сетевой проверкой. Для полного сценария отключите интернет
хоста/браузера, убедитесь в отсутствии доступа к внешнему HTTPS и выполните
загрузку **нового** локального видео, дождитесь ready, проверьте настоящий
поиск, конспект, главы и плеер. Зафиксируйте latency и таймкоды минимум
трёх фрагментов по речи. Сохранённые ранее ready-записи не проверяют pipeline.

При отсутствующих весах ожидается явная ошибка; скачивания, случайных векторов
и шаблонного конспекта в рабочем режиме нет. Пока эта процедура целиком не
пройдена, офлайн-режим считается подготовленным, но не подтверждённым.
