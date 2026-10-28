# Локальный GGUF: Windows и PowerShell

Команды ниже выполняются в **PowerShell** из корня deepLecture. Bash/Git Bash
не требуется. Docker Desktop должен быть запущен в режиме **Linux containers**
(обычно через WSL2). Это режим Docker; сам терминал остаётся PowerShell.
Отдельно устанавливать Ollama на Windows не нужно.

## 1. Проверить Docker и подготовить файлы

Откройте PowerShell в папке проекта и проверьте:

```powershell
docker version
docker compose version

New-Item -ItemType Directory -Force -Path .\storage\llm | Out-Null

if (-not (Test-Path -LiteralPath .\.env)) {
    Copy-Item -LiteralPath .\.env.example -Destination .\.env
}
if (-not (Test-Path -LiteralPath .\.env.llm)) {
    Copy-Item -LiteralPath .\infra\llm\ollama.env.example -Destination .\.env.llm
}
```

Существующие `.env` не перезаписываются. Скопируйте через Проводник ваш целый
основной GGUF-файл в `storage\llm\model.gguf`. Это не должен быть `mmproj.gguf`
или одна часть split-модели. Включите показ расширений файлов в Проводнике,
чтобы случайно не получить `model.gguf.gguf`.

Проверьте наличие файла:

```powershell
Get-Item -LiteralPath .\storage\llm\model.gguf
```

`storage` уже исключена из Git и Docker build-контекста. Имя `model.gguf`
ничего не утверждает о квантизации. Переименование не преобразует веса.

## 2. Настроить локальную модель

Откройте конфигурации:

```powershell
notepad .env
notepad .env.llm
```

В `.env` задайте свой `DB_PASSWORD`. В `.env.llm` измените соответствующие
поля, сохранив остальные настройки:

```dotenv
LLM_BASE_URL=http://ollama:11434/v1
LLM_MODEL=deeplecture-qwen3.5:4b
LLM_API_KEY=
LLM_AUTH_MODE=none
LLM_REASONING_EFFORT=none
LLM_ALLOW_HTTP=true
LLM_TIMEOUT=300
OLLAMA_IMAGE=ollama/ollama:0.40.12
OLLAMA_CONTEXT_LENGTH=16384
```

Файл должен называться `.env.llm`, не `.env.llm.txt`. Не заменяйте
`http://ollama:11434` на `localhost`: это адрес Ollama для worker внутри Docker.

## 3. Создать команду PowerShell и импортировать GGUF

Вставьте функцию целиком в то же окно PowerShell:

```powershell
function dgg {
    $composeArgs = @(
        'compose',
        '--env-file', '.env',
        '--env-file', '.env.llm',
        '-f', 'infra/compose.yml',
        '-f', 'infra/compose.ollama.yml',
        '-f', 'infra/compose.gguf.yml'
    )
    & docker @composeArgs @args
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose завершился с кодом $LASTEXITCODE"
    }
}
```

Это обычная функция текущего терминала; изменение ExecutionPolicy не требуется.
Если откроете новое окно PowerShell, вставьте функцию заново. Продолжайте
выполнять команды из корня проекта:

```powershell
dgg up -d ollama
dgg exec ollama ollama --version
dgg exec ollama ollama create deeplecture-qwen3.5:4b -f /config/Modelfile
dgg exec ollama ollama show deeplecture-qwen3.5:4b
```

`/config/Modelfile` и `/imports/model.gguf` — пути **внутри Linux-контейнера**.
На Windows их не нужно заменять на пути с диском C:. Compose сам монтирует
`storage\llm` в `/imports`, а подготовленный Modelfile — в `/config/Modelfile`.

В выводе `ollama show` найдите **quantization**: например, Q4_K_M, Q8_0 или F16.
GGUF — контейнер, расширение не определяет квантизацию. Импорт её сохраняет,
F16 автоматически в Q4_K_M не превращается. Дополнительно проверьте архитектуру
и число параметров: локальное имя `:4b` само по себе их не подтверждает.
Для локального имени `deeplecture-qwen3.5:4b` не нужен `ollama pull` весов.

## 4. Проверить вызов модели приложением

После успешного импорта:

```powershell
dgg build worker
dgg run --rm --no-deps worker python -m app.check_llm
```

Проверка не требует БД: она вызывает настоящую модель через OpenAI-compatible
API, проверяет JSON и ID глав. После успеха на уже подготовленном стеке:

```powershell
dgg up -d worker
```

Для полного pipeline отдельно нужны схема MySQL и веса Whisper/E5. Этот
сценарий подключения GGUF сам по себе их не подготавливает. Модель хранится
в Docker-томе `ollama-models`; `down` его сохраняет, `down -v` удаляет.

Docker Desktop должен иметь доступ к папке проекта. Для файла на другом диске
можно задать каталог в текущем PowerShell, например
`$env:GGUF_DIR = 'D:/Models/Qwen'`; внутри него ожидается `model.gguf`.
Для стандартного расположения в проекте задавать GGUF_DIR не требуется.

PowerShell-команды подготовлены для Windows, но не выполнялись на Windows-хосте
в нашей Linux-среде. Реальный импорт и inference требуют вашего GGUF-файла.
