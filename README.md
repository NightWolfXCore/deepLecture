# deepLecture

Семантический поиск по видеолекциям: PHP API, Python/FastAPI и фоновая обработка.

- [Запуск и устройство бэкенда](backend/README.md)
- [Архитектура](architecture/architecture.md)
- [HTTP API](contracts/http-api.md)
- [Контракт MySQL](contracts/database.md)

Разработка бэкенда и отдельный набор автотестов находятся в ветке `backend`.
Порядок переноса реализации в `main` без тестового коммита описан в инструкции.
Для работы с настоящими лекциями нужны доступ к моделям Hugging Face,
согласованная схема MySQL и ключ OpenAI-совместимого LLM API.
