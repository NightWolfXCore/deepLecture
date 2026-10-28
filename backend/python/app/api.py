"""Private search API. No upload, job execution, credentials, or public exposure."""
from contextlib import asynccontextmanager
from uuid import UUID, uuid4
import logging
import numpy as np
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from starlette.exceptions import HTTPException
from app.config import Settings
from app.pipeline.embed import Embedder
from app.pipeline.errors import PipelineError
from app.repositories.database import SearchRepository
from app.providers.chat import Answerer

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, code, status, message):
        self.code, self.status, self.message = code, status, message


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lecture_id: UUID | None = None
    query: str = Field(min_length=1, max_length=1000, strict=True)
    limit: StrictInt = Field(default=5, ge=1, le=20)

    @field_validator("query", mode="before")
    @classmethod
    def trim_query(cls, value):
        return value.strip() if isinstance(value, str) else value


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000, strict=True)


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lecture_id: UUID
    question: str = Field(min_length=1, max_length=1000, strict=True)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=6)

    @field_validator('question', mode='before')
    @classmethod
    def trim_question(cls, value):
        return value.strip() if isinstance(value, str) else value


def create_app(settings=None, repository=None, embedder=None, answerer=None):
    cfg = settings or Settings.from_env()
    repo = repository or SearchRepository(cfg)
    encoder = embedder or Embedder(cfg)
    chat_model = answerer or Answerer(cfg)

    @asynccontextmanager
    async def lifespan(app):
        app.state.model_ready = False
        try:
            encoder.load()
            app.state.model_ready = True
        except PipelineError as exc:
            log.error("Search startup unavailable: %s", exc.code)
        yield

    app = FastAPI(title="deepLecture internal search", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.model_ready = False

    def error(request, status, code, message, details=None):
        return JSONResponse(status_code=status, content={"error": {"code": code,
            "message": message, "details": details or {}}, "request_id": request.state.request_id})

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # JSON parser errors and field validation use the same safe envelope.
        malformed = any(e["type"] == "json_invalid" for e in exc.errors())
        return error(request, 400 if malformed else 422,
                     "INVALID_JSON" if malformed else "VALIDATION_ERROR", "Некорректные параметры запроса")

    @app.exception_handler(ApiError)
    async def api_error(request, exc):
        return error(request, exc.status, exc.code, exc.message)

    @app.exception_handler(PipelineError)
    async def pipeline_error(request, exc):
        return error(request, 503, "SEARCH_UNAVAILABLE", "Семантический поиск временно недоступен")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(request, exc.status_code, "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR", "Ресурс недоступен")

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        # Never include SQL/provider errors in client responses or normal logs.
        log.error("Search dependency failure type=%s request=%s", type(exc).__name__, request.state.request_id)
        return error(request, 503, "SEARCH_UNAVAILABLE", "Семантический поиск временно недоступен")

    @app.get("/internal/v1/ready")
    def ready():
        if not app.state.model_ready:
            raise ApiError("SEARCH_UNAVAILABLE", 503, "Модель поиска не загружена")
        repo.ping()
        return {"data": {"status": "ok"}}

    def retrieve(lecture_id, query_text, limit):
        if not app.state.model_ready:
            raise ApiError("SEARCH_UNAVAILABLE", 503, "Модель поиска не загружена")
        lecture, rows = repo.index(lecture_id)
        if lecture is None:
            raise ApiError("LECTURE_NOT_FOUND", 404, "Лекция не найдена")
        if lecture["index_ready_at"] is None:
            raise ApiError("LECTURE_NOT_READY", 409, "Индекс лекции ещё не готов")
        if not rows:
            if lecture_id is None:
                return []
            raise ApiError("INDEX_INVALID", 503, "Индекс лекции повреждён или несовместим")
        vectors = []
        for row in rows:
            if (row["model_name"] != encoder.model_name or row["model_revision"] != encoder.revision
                    or row["dimensions"] != 384 or not isinstance(row["embedding"], (bytes, bytearray))
                    or len(row["embedding"]) != 1536):
                raise ApiError("INDEX_INVALID", 503, "Индекс лекции повреждён или несовместим")
            vector = np.frombuffer(row["embedding"], dtype="<f4")
            if not np.isfinite(vector).all() or not np.isclose(np.linalg.norm(vector), 1, atol=1e-4):
                raise ApiError("INDEX_INVALID", 503, "Индекс лекции повреждён или несовместим")
            vectors.append(vector)
        query = np.asarray(encoder.encode_query(query_text), dtype=np.float32)
        if query.shape != (384,) or not np.isfinite(query).all() or not np.isclose(np.linalg.norm(query), 1, atol=1e-4):
            raise PipelineError("EMBEDDING_FAILED", "Некорректный вектор запроса")
        scores = np.clip(np.stack(vectors) @ query, -1, 1)
        ranked = sorted(range(len(rows)), key=lambda i: (-float(scores[i]), rows[i]["start_ms"], rows[i]["chunk_id"]))
        return [{**rows[i], "score": float(scores[i])} for i in ranked[:limit]]

    @app.post("/internal/v1/search")
    def search(payload: SearchInput):
        rows = retrieve(str(payload.lecture_id) if payload.lecture_id else None, payload.query, payload.limit)
        return {"data": [{"chunk_id": row['chunk_id'], "score": row['score']} for row in rows]}

    @app.post('/internal/v1/chat')
    def chat(payload: ChatInput):
        lecture_id = str(payload.lecture_id)
        rows = retrieve(lecture_id, payload.question, 4)
        try:
            result = chat_model.answer(payload.question, [m.model_dump() for m in payload.history], rows)
        except PipelineError:
            raise ApiError('CHAT_UNAVAILABLE', 503, 'Не удалось получить ответ по лекции') from None
        lecture, current = repo.index(lecture_id)
        if (not lecture or lecture['index_ready_at'] is None
                or not {r['chunk_id'] for r in rows}.issubset({r['chunk_id'] for r in current})):
            raise ApiError('LECTURE_NOT_READY', 409, 'Индекс лекции обновился, повторите вопрос')
        return {'data': {'lecture_id': lecture_id, **result}}

    return app


app = create_app()
