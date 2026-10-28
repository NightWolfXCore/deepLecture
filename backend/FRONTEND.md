# Подключение ветки frontend

Проверяемая версия: `dba72f67a879ab125d92383bee1dc8003a41ad55`.
Ветка содержит HTML/JS/CSS в корне; стандартный Compose не включает эти файлы.
Настройка участника `js/config.js` по умолчанию включает mock-режим.

Отдельный read-only checkout позволяет подключить UI без merge веток и
без изменения исходников участника:

```sh
git fetch origin frontend
git worktree add --detach /tmp/deeplecture-frontend \
  dba72f67a879ab125d92383bee1dc8003a41ad55
export FRONTEND_ROOT=/tmp/deeplecture-frontend
docker compose --env-file .env --env-file .env.llm \
  -f infra/compose.yml -f infra/compose.ollama.yml \
  -f infra/compose.frontend.yml up -d --build
```

Укажите новый свободный путь для worktree, если пример уже существует.
Для Windows/PowerShell задайте абсолютный FRONTEND_ROOT соответствующей командой
своей оболочки. Подготовьте схему БД и веса по [README.md](README.md) и [LLM.md](LLM.md).

Override монтирует файлы в Nginx и подменяет только отдаваемый `js/config.js`
на `infra/frontend-api-config.js`: `USE_MOCK=false`, `/api/v1`, один origin.
Исходный файл участника на диске не меняется. В интерфейсе должен исчезнуть
значок демо-режима; сетевые запросы должны идти в PHP API. Имена JSON-полей
и маршрутов клиента соответствуют HTTP-контракту версии 1.

Пока Bootstrap и Google Fonts в HTML загружаются из внешних CDN. Для полного
офлайн-режима участнику фронтенда нужно разместить эти ресурсы локально.
При использовании offline override добавляйте его последним. Один HTTP smoke
не проверяет воспроизведение, перемотку, DOM и синхронность субтитров: их следует
проверить в браузере на настоящей лекции перед защитой.
