"""Transactional MySQL access, queue leases, and fenced stage publication."""
from __future__ import annotations

from contextlib import contextmanager
import time
from typing import Any, Callable
from uuid import uuid4

import numpy as np
import pymysql


class RepositoryError(Exception):
    def __init__(self, code: str, status: int, message: str):
        super().__init__(message)
        self.code, self.status, self.message = code, status, message


class LeaseLost(RuntimeError):
    """The result belongs to an expired or replaced worker and must be discarded."""


class Database:
    def __init__(self, settings: Any):
        self.settings = settings

    @contextmanager
    def connection(self):
        s = self.settings
        connection = pymysql.connect(
            host=s.db_host, port=s.db_port, user=s.db_user,
            password=s.db_password, database=s.db_name,
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
            autocommit=False, connect_timeout=5, read_timeout=15,
            write_timeout=15, init_command="SET time_zone = '+00:00'",
        )
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def write(self, action: Callable):
        """Retry only complete, short SQL transactions, never model/network calls."""
        for attempt in range(3):
            try:
                with self.connection() as connection:
                    with connection.cursor() as cursor:
                        result = action(cursor)
                    connection.commit()
                    return result
            except pymysql.err.OperationalError as error:
                if error.args[0] not in (1205, 1213) or attempt == 2:
                    raise
                time.sleep(0.05 * (attempt + 1))


class SearchRepository(Database):
    def ping(self) -> None:
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

    health = ping

    def index(self, lecture_id: str | None) -> tuple[dict | None, list[dict]]:
        # Explicit consistent snapshot also works if server default isolation differs.
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            if lecture_id is not None:
                cursor.execute("SELECT id, index_ready_at FROM lectures WHERE id=%s", (lecture_id,))
                lecture = cursor.fetchone()
            else:
                lecture = {"index_ready_at": True}
            if not lecture or lecture["index_ready_at"] is None:
                return lecture, []
            cursor.execute(
                "SELECT c.id AS chunk_id, c.lecture_id, c.text, c.start_ms, c.end_ms, e.embedding, e.model_name, "
                "e.model_revision, e.dimensions FROM search_chunks c "
                "JOIN lectures l ON l.id=c.lecture_id "
                "LEFT JOIN chunk_embeddings e ON e.chunk_id=c.id "
                "WHERE l.index_ready_at IS NOT NULL " + ("AND c.lecture_id=%s " if lecture_id is not None else "") +
                "ORDER BY c.lecture_id,c.position", (lecture_id,) if lecture_id is not None else (),
            )
            return lecture, list(cursor.fetchall())

    get_search_index = index


Repository = SearchRepository


class JobRepository(Database):
    def __init__(self, settings: Any, lease_seconds: int = 120):
        super().__init__(settings)
        if lease_seconds < 3:
            raise ValueError("lease_seconds must be at least 3")
        self.lease_seconds = lease_seconds

    @staticmethod
    def _clear(cursor, lecture_id: str):
        cursor.execute("DELETE FROM lecture_chapters WHERE lecture_id=%s", (lecture_id,))
        cursor.execute("DELETE FROM lecture_summaries WHERE lecture_id=%s", (lecture_id,))
        cursor.execute(
            "DELETE e FROM chunk_embeddings e JOIN search_chunks c ON c.id=e.chunk_id "
            "WHERE c.lecture_id=%s", (lecture_id,),
        )
        cursor.execute("DELETE FROM search_chunks WHERE lecture_id=%s", (lecture_id,))
        cursor.execute("DELETE FROM transcript_segments WHERE lecture_id=%s", (lecture_id,))
        cursor.execute(
            "UPDATE lectures SET transcript_ready_at=NULL,index_ready_at=NULL,"
            "summary_ready_at=NULL,updated_at=UTC_TIMESTAMP(3) WHERE id=%s", (lecture_id,),
        )

    @staticmethod
    def _lock(cursor, job: dict):
        cursor.execute("SELECT id FROM lectures WHERE id=%s FOR UPDATE", (job["lecture_id"],))
        if not cursor.fetchone():
            raise LeaseLost("Lecture no longer exists")
        cursor.execute(
            "SELECT *, (lease_expires_at > UTC_TIMESTAMP(3)) AS lease_valid "
            "FROM processing_jobs WHERE id=%s FOR UPDATE", (job["id"],),
        )
        current = cursor.fetchone()
        if (not current or current["status"] != "running" or not current["lease_valid"]
                or current["worker_token"] != job["worker_token"]
                or current["lecture_id"] != job["lecture_id"]):
            raise LeaseLost("Worker no longer owns this job")
        return current

    def claim(self) -> dict | None:
        # Candidate discovery is deliberately unlocked. Always recheck under locks.
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,lecture_id FROM processing_jobs WHERE status='queued' OR "
                "(status='running' AND lease_expires_at <= UTC_TIMESTAMP(3)) "
                "ORDER BY created_at,id LIMIT 32"
            )
            candidates = cursor.fetchall()
        for candidate in candidates:
            def acquire(cursor):
                cursor.execute("SELECT * FROM lectures WHERE id=%s FOR UPDATE", (candidate["lecture_id"],))
                lecture = cursor.fetchone()
                if not lecture:
                    return None
                cursor.execute(
                    "SELECT *, (lease_expires_at <= UTC_TIMESTAMP(3)) AS expired "
                    "FROM processing_jobs WHERE id=%s FOR UPDATE", (candidate["id"],),
                )
                current = cursor.fetchone()
                if not current or not (current["status"] == "queued" or
                        (current["status"] == "running" and current["expired"])):
                    return None
                if current["attempt"] >= 3:
                    cursor.execute(
                        "UPDATE processing_jobs SET status='failed',failed_stage=stage,"
                        "error_code='WORKER_LEASE_EXPIRED',error_message=%s,finished_at=UTC_TIMESTAMP(3),"
                        "updated_at=UTC_TIMESTAMP(3) WHERE id=%s",
                        ("Обработка прервана после трёх попыток", current["id"]),
                    )
                    cursor.execute("UPDATE lectures SET status='failed',updated_at=UTC_TIMESTAMP(3) WHERE id=%s", (lecture["id"],))
                    return None
                # Recovery always reprocesses from the original video.
                self._clear(cursor, lecture["id"])
                token = str(uuid4())
                cursor.execute(
                    "UPDATE processing_jobs SET status='running',stage='extracting',attempt=attempt+1,"
                    "worker_token=%s,heartbeat_at=UTC_TIMESTAMP(3),"
                    "lease_expires_at=TIMESTAMPADD(SECOND,%s,UTC_TIMESTAMP(3)),"
                    "started_at=COALESCE(started_at,UTC_TIMESTAMP(3)),finished_at=NULL,"
                    "error_code=NULL,error_message=NULL,failed_stage=NULL,updated_at=UTC_TIMESTAMP(3) WHERE id=%s",
                    (token, self.lease_seconds, current["id"]),
                )
                cursor.execute("UPDATE lectures SET status='extracting',updated_at=UTC_TIMESTAMP(3) WHERE id=%s", (lecture["id"],))
                return {**current, "worker_token": token, "stage": "extracting", "status": "running",
                        "attempt": current["attempt"] + 1, "storage_key": lecture["storage_key"]}
            result = self.write(acquire)
            if result:
                return result
        return None

    def heartbeat(self, job: dict):
        def action(cursor):
            self._lock(cursor, job)
            cursor.execute(
                "UPDATE processing_jobs SET heartbeat_at=UTC_TIMESTAMP(3),"
                "lease_expires_at=TIMESTAMPADD(SECOND,%s,UTC_TIMESTAMP(3)),"
                "updated_at=UTC_TIMESTAMP(3) WHERE id=%s", (self.lease_seconds, job["id"]),
            )
        self.write(action)

    @staticmethod
    def _stage(cursor, job: dict, stage: str):
        cursor.execute("UPDATE processing_jobs SET stage=%s,updated_at=UTC_TIMESTAMP(3) WHERE id=%s", (stage, job["id"]))
        cursor.execute("UPDATE lectures SET status=%s,updated_at=UTC_TIMESTAMP(3) WHERE id=%s", (stage, job["lecture_id"]))

    def audio_ready(self, job: dict, duration_ms: int):
        if duration_ms <= 0:
            raise ValueError("Invalid duration")
        def action(cursor):
            self._lock(cursor, job)
            cursor.execute("UPDATE lectures SET duration_ms=%s WHERE id=%s", (duration_ms, job["lecture_id"]))
            self._stage(cursor, job, "transcribing")
        self.write(action)

    def import_ready(self, job: dict, staged_path, destination):
        """Publish one token-scoped download while holding the lease fencing lock."""
        size = staged_path.stat().st_size
        if size <= 0:
            raise ValueError("Empty import")
        def action(cursor):
            self._lock(cursor, job)
            # Rename is local/atomic, not a network/ML operation in a transaction.
            # A retried SQL deadlock may follow a successful rename.
            if staged_path.exists():
                staged_path.replace(destination)
                destination.chmod(0o644)
            cursor.execute("UPDATE lectures SET size_bytes=%s,mime_type='video/mp4',updated_at=UTC_TIMESTAMP(3) WHERE id=%s", (size, job['lecture_id']))
        self.write(action)

    def transcript_ready(self, job: dict, segments: list[dict]):
        if not segments:
            raise ValueError("Empty transcript")
        for i, segment in enumerate(segments):
            if segment["position"] != i or not segment["text"].strip() or not (0 <= segment["start_ms"] < segment["end_ms"]):
                raise ValueError("Invalid transcript")
        def action(cursor):
            self._lock(cursor, job)
            cursor.execute("SELECT duration_ms FROM lectures WHERE id=%s", (job["lecture_id"],))
            duration_ms = cursor.fetchone()["duration_ms"]
            if any(s["end_ms"] > duration_ms for s in segments):
                raise ValueError("Transcript outside video")
            cursor.executemany(
                "INSERT INTO transcript_segments (id,lecture_id,position,start_ms,end_ms,text) VALUES (%s,%s,%s,%s,%s,%s)",
                [(s["id"], job["lecture_id"], s["position"], s["start_ms"], s["end_ms"], s["text"]) for s in segments],
            )
            cursor.execute("UPDATE lectures SET transcript_ready_at=UTC_TIMESTAMP(3) WHERE id=%s", (job["lecture_id"],))
            self._stage(cursor, job, "indexing")
        self.write(action)

    def index_ready(self, job: dict, chunks: list[dict], vectors: np.ndarray, model_name: str, revision: str):
        vectors = np.asarray(vectors, dtype="<f4")
        if (not chunks or vectors.shape != (len(chunks), 384) or not np.isfinite(vectors).all()
                or not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-4)):
            raise ValueError("Invalid embedding matrix")
        if not model_name or not revision:
            raise ValueError("Missing embedding identity")
        def action(cursor):
            self._lock(cursor, job)
            cursor.execute("SELECT id,position,start_ms,end_ms FROM transcript_segments WHERE lecture_id=%s", (job["lecture_id"],))
            segments = {s["id"]: s for s in cursor.fetchall()}
            for i, chunk in enumerate(chunks):
                first, last = segments.get(chunk["first_segment_id"]), segments.get(chunk["last_segment_id"])
                if (not first or not last or first["position"] > last["position"] or chunk["position"] != i
                        or chunk["start_ms"] != first["start_ms"] or chunk["end_ms"] != last["end_ms"]
                        or not chunk["text"].strip()):
                    raise ValueError("Invalid chunk references")
            cursor.executemany(
                "INSERT INTO search_chunks (id,lecture_id,position,start_ms,end_ms,text,first_segment_id,last_segment_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                [(c["id"],job["lecture_id"],c["position"],c["start_ms"],c["end_ms"],c["text"],c["first_segment_id"],c["last_segment_id"]) for c in chunks],
            )
            cursor.executemany(
                "INSERT INTO chunk_embeddings (chunk_id,model_name,model_revision,dimensions,embedding) VALUES (%s,%s,%s,384,%s)",
                [(c["id"],model_name,revision,v.tobytes()) for c,v in zip(chunks,vectors)],
            )
            cursor.execute("UPDATE lectures SET index_ready_at=UTC_TIMESTAMP(3) WHERE id=%s", (job["lecture_id"],))
            self._stage(cursor, job, "summarizing")
        self.write(action)

    def summary_ready(self, job: dict, summary: dict):
        if not isinstance(summary.get("text"), str) or not summary["text"].strip() or not summary.get("chapters"):
            raise ValueError("Invalid summary")
        def action(cursor):
            self._lock(cursor, job)
            cursor.execute("SELECT id,start_ms FROM transcript_segments WHERE lecture_id=%s", (job["lecture_id"],))
            segments = {s["id"]: s for s in cursor.fetchall()}
            previous_time, has_section = -1, False
            for i, chapter in enumerate(summary["chapters"]):
                segment = segments.get(chapter["start_segment_id"])
                if (not segment or chapter["position"] != i or chapter["level"] not in (1,2)
                        or not chapter["title"].strip() or len(chapter["title"]) > 200
                        or segment["start_ms"] < previous_time or (chapter["level"] == 2 and not has_section)):
                    raise ValueError("Invalid chapter")
                previous_time = segment["start_ms"]
                has_section = has_section or chapter["level"] == 1
            cursor.execute(
                "INSERT INTO lecture_summaries (lecture_id,text,provider,model_name,created_at) VALUES (%s,%s,%s,%s,UTC_TIMESTAMP(3))",
                (job["lecture_id"],summary["text"],summary["provider"],summary["model_name"]),
            )
            cursor.executemany(
                "INSERT INTO lecture_chapters (id,lecture_id,position,title,level,start_segment_id) VALUES (%s,%s,%s,%s,%s,%s)",
                [(c["id"],job["lecture_id"],c["position"],c["title"],c["level"],c["start_segment_id"]) for c in summary["chapters"]],
            )
            cursor.execute("UPDATE lectures SET summary_ready_at=UTC_TIMESTAMP(3) WHERE id=%s", (job["lecture_id"],))
            self._stage(cursor, job, "ready")
            cursor.execute("UPDATE processing_jobs SET status='succeeded',finished_at=UTC_TIMESTAMP(3),updated_at=UTC_TIMESTAMP(3) WHERE id=%s", (job["id"],))
        self.write(action)

    def fail(self, job: dict, stage: str, code: str, message: str):
        def action(cursor):
            self._lock(cursor, job)
            cursor.execute(
                "UPDATE processing_jobs SET status='failed',failed_stage=%s,error_code=%s,error_message=%s,"
                "finished_at=UTC_TIMESTAMP(3),updated_at=UTC_TIMESTAMP(3) WHERE id=%s",
                (stage, code[:64], message[:500], job["id"]),
            )
            cursor.execute("UPDATE lectures SET status='failed',updated_at=UTC_TIMESTAMP(3) WHERE id=%s", (job["lecture_id"],))
        self.write(action)
