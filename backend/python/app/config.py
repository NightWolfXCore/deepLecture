"""Server-only settings. Never serialize credentials into responses or logs."""
from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    db_host: str = "mysql"
    db_port: int = 3306
    db_name: str = "deeplecture"
    db_user: str = "deeplecture"
    db_password: str = ""
    storage_root: Path = Path("/data")
    model_cache: Path = Path("/models")
    models_offline: bool = False
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_revision: str = "main"
    whisper_model: str = "Systran/faster-whisper-small"
    whisper_revision: str = "main"
    cpu_threads: int = 2
    max_duration_ms: int = 7_200_000
    ffmpeg_timeout: int = 1800
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_auth_mode: str = "bearer"
    llm_model: str = "gpt-4o-mini"
    llm_timeout: float = 90
    llm_allow_http: bool = False
    lease_seconds: int = 120
    heartbeat_seconds: int = 20
    poll_seconds: float = 2

    @classmethod
    def from_env(cls):
        def value(key, default):
            return os.getenv(key, default)
        return cls(
            db_host=value("DB_HOST", "mysql"), db_port=int(value("DB_PORT", "3306")),
            db_name=value("DB_NAME", "deeplecture"), db_user=value("DB_USER", "deeplecture"),
            db_password=value("DB_PASSWORD", ""),
            storage_root=Path(value("STORAGE_ROOT", "/data")),
            model_cache=Path(value("MODEL_CACHE", "/models")),
            models_offline=value("MODELS_OFFLINE", "false").lower() == "true",
            embedding_model=value("EMBEDDING_MODEL", "intfloat/multilingual-e5-small"),
            embedding_revision=value("EMBEDDING_REVISION", "main") or "main",
            whisper_model=value("WHISPER_MODEL", "Systran/faster-whisper-small"),
            whisper_revision=value("WHISPER_REVISION", "main") or "main",
            cpu_threads=max(1, int(value("CPU_THREADS", "2"))),
            max_duration_ms=int(value("MAX_DURATION_MS", "7200000")),
            ffmpeg_timeout=int(value("FFMPEG_TIMEOUT", "1800")),
            llm_base_url=value("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_api_key=value("LLM_API_KEY", ""), llm_model=value("LLM_MODEL", "gpt-4o-mini"),
            llm_auth_mode=value("LLM_AUTH_MODE", "bearer").lower(),
            llm_timeout=float(value("LLM_TIMEOUT", "90")),
            llm_allow_http=value("LLM_ALLOW_HTTP", "false").lower() == "true",
            lease_seconds=int(value("LEASE_SECONDS", "120")),
            heartbeat_seconds=int(value("HEARTBEAT_SECONDS", "20")),
            poll_seconds=float(value("POLL_SECONDS", "2")),
        )
