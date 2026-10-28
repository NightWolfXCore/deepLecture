"""Worker orchestration tests and opt-in real MySQL lease/concurrency tests.

Doubles in orchestration tests check control flow, not ASR/search quality.
MySQL tests use a disposable database whose name must end in `_test`.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from app.config import Settings
from app.pipeline.errors import PipelineError
from app.repositories.database import JobRepository, LeaseLost, SearchRepository
from app.worker import Heartbeat, Worker, video_path


class FakeRepository:
    lease_seconds = 120

    def __init__(self):
        self.calls = []
        self.job = {"id": str(uuid4()), "lecture_id": str(uuid4()), "worker_token": str(uuid4()),
                    "storage_key": "videos/test.mp4"}

    def claim(self):
        return self.job

    def heartbeat(self, job):
        self.calls.append("heartbeat")

    def audio_ready(self, *args):
        self.calls.append("audio")

    def transcript_ready(self, *args):
        self.calls.append("transcript")

    def index_ready(self, *args):
        self.calls.append("index")

    def summary_ready(self, *args):
        self.calls.append("summary")

    def fail(self, job, stage, code, message):
        self.calls.append(("failed", stage, code))


def make_worker(tmp_path, repository=None, summarizer=None):
    (tmp_path / "videos").mkdir(exist_ok=True)
    (tmp_path / "videos/test.mp4").write_bytes(b"test-orchestration-only")
    segment = {"id": str(uuid4()), "position": 0, "start_ms": 0, "end_ms": 1000, "text": "A lecture"}

    class Tokenizer:
        def num_special_tokens_to_add(self, pair=False):
            return 2

        def encode(self, text, **kwargs):
            return list(range(len(text.split())))

        def decode(self, tokens, **kwargs):
            return "text"

    class Embedder:
        model_name = "test-double"
        revision = "test-double"
        tokenizer = Tokenizer()

        def load(self):
            pass

        def encode_passages(self, texts):
            result = np.zeros((len(texts), 384), dtype="<f4")
            result[:, 0] = 1
            return result

    return Worker(
        SimpleNamespace(storage_root=tmp_path), repository=repository or FakeRepository(),
        audio=SimpleNamespace(extract=lambda *_: 1000),
        transcriber=SimpleNamespace(transcribe=lambda *_: [segment]), embedder=Embedder(),
        summarizer=summarizer or SimpleNamespace(summarize=lambda *_: {"text": "summary"}),
    )


def test_worker_publishes_in_order_and_removes_audio(tmp_path):
    worker = make_worker(tmp_path)
    assert worker.run_once() is True
    assert worker.repository.calls == ["audio", "transcript", "index", "summary"]
    assert list((tmp_path / "audio").iterdir()) == []


def test_llm_failure_does_not_clear_index(tmp_path):
    def fail(*_):
        raise PipelineError("LLM_UNAVAILABLE", "Сервис конспектов недоступен")
    worker = make_worker(tmp_path, summarizer=SimpleNamespace(summarize=fail))
    worker.run_once()
    assert worker.repository.calls == ["audio", "transcript", "index", ("failed", "summarizing", "LLM_UNAVAILABLE")]


def test_stale_worker_cannot_record_failure_or_summary(tmp_path):
    repository = FakeRepository()
    def lost(*_):
        raise LeaseLost()
    repository.index_ready = lost
    worker = make_worker(tmp_path, repository=repository)
    worker.run_once()
    assert repository.calls == ["audio", "transcript"]


def test_heartbeat_failure_marks_ownership_uncertain():
    repository = FakeRepository()
    def fail(*_):
        raise RuntimeError("database offline")
    repository.heartbeat = fail
    with Heartbeat(repository, repository.job, 0.01) as heartbeat:
        assert heartbeat.lost.wait(1)
        with pytest.raises(LeaseLost):
            heartbeat.check()


def test_db_storage_key_cannot_escape_storage(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("private")
    with pytest.raises(PipelineError):
        video_path(tmp_path, "secret.txt")
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos/link.mp4").symlink_to(outside)
    with pytest.raises(PipelineError):
        video_path(tmp_path, "videos/link.mp4")


@pytest.fixture
def mysql_job():
    if os.getenv("RUN_MYSQL_TESTS") != "1":
        pytest.skip("Set RUN_MYSQL_TESTS=1 for real MySQL concurrency tests")
    settings = Settings.from_env()
    if not settings.db_name.endswith("_test"):
        pytest.fail("MySQL tests require a disposable DB_NAME ending in _test")
    repository = JobRepository(settings, lease_seconds=120)
    lecture_id, job_id = str(uuid4()), str(uuid4())
    def create(cursor):
        cursor.execute(
            "INSERT INTO lectures (id,title,original_filename,storage_key,mime_type,size_bytes,status,created_at,updated_at) "
            "VALUES (%s,'Test','test.mp4',%s,'video/mp4',1,'queued',UTC_TIMESTAMP(3),UTC_TIMESTAMP(3))",
            (lecture_id, f"videos/{lecture_id}.mp4"),
        )
        cursor.execute(
            "INSERT INTO processing_jobs (id,lecture_id,status,stage,attempt,created_at,updated_at) "
            "VALUES (%s,%s,'queued','queued',0,UTC_TIMESTAMP(3),UTC_TIMESTAMP(3))", (job_id, lecture_id),
        )
    repository.write(create)
    yield repository, lecture_id, job_id
    def cleanup(cursor):
        repository._clear(cursor, lecture_id)
        cursor.execute("DELETE FROM processing_jobs WHERE lecture_id=%s", (lecture_id,))
        cursor.execute("DELETE FROM lectures WHERE id=%s", (lecture_id,))
    repository.write(cleanup)


def fetch_job(repository, job_id):
    with repository.connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM processing_jobs WHERE id=%s", (job_id,))
        return cursor.fetchone()


def expire(repository, job_id):
    repository.write(lambda cursor: cursor.execute(
        "UPDATE processing_jobs SET lease_expires_at=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(3)) WHERE id=%s", (job_id,),
    ))


@pytest.mark.mysql
def test_two_workers_cannot_claim_same_job(mysql_job):
    repository, lecture_id, job_id = mysql_job
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: repository.claim(), range(2)))
    claimed = [result for result in results if result and result["id"] == job_id]
    assert len(claimed) == 1
    assert fetch_job(repository, job_id)["attempt"] == 1


@pytest.mark.mysql
def test_expired_lease_recovers_and_fences_old_worker(mysql_job):
    repository, lecture_id, job_id = mysql_job
    old = repository.claim()
    repository.audio_ready(old, 1000)
    segments = [{"id": str(uuid4()), "position": 0, "start_ms": 0, "end_ms": 1000, "text": "Lecture"}]
    repository.transcript_ready(old, segments)
    expire(repository, job_id)
    new = repository.claim()
    assert new["attempt"] == 2
    assert new["worker_token"] != old["worker_token"]
    with pytest.raises(LeaseLost):
        repository.audio_ready(old, 1000)
    with pytest.raises(LeaseLost):
        repository.fail(old, "indexing", "TEST", "stale")
    with repository.connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT transcript_ready_at,index_ready_at,status FROM lectures WHERE id=%s", (lecture_id,))
        assert cursor.fetchone() == {"transcript_ready_at": None, "index_ready_at": None, "status": "extracting"}
        cursor.execute("SELECT count(*) AS n FROM transcript_segments WHERE lecture_id=%s", (lecture_id,))
        assert cursor.fetchone()["n"] == 0


@pytest.mark.mysql
def test_three_expired_attempts_fail_job(mysql_job):
    repository, lecture_id, job_id = mysql_job
    for attempt in range(1, 4):
        job = repository.claim()
        assert job["attempt"] == attempt
        expire(repository, job_id)
    assert repository.claim() is None
    record = fetch_job(repository, job_id)
    assert record["status"] == "failed"
    assert record["error_code"] == "WORKER_LEASE_EXPIRED"


@pytest.mark.mysql
def test_index_publication_and_summary_failure_preserves_search(mysql_job):
    repository, lecture_id, job_id = mysql_job
    job = repository.claim()
    repository.audio_ready(job, 1000)
    segment = {"id": str(uuid4()), "position": 0, "start_ms": 0, "end_ms": 1000, "text": "Lecture"}
    repository.transcript_ready(job, [segment])
    chunk = {**segment, "id": str(uuid4()), "first_segment_id": segment["id"], "last_segment_id": segment["id"]}
    with pytest.raises(ValueError):
        repository.index_ready(job, [chunk], np.zeros((1, 384)), "test", "test")
    vector = np.zeros((1, 384), dtype="<f4")
    vector[0, 0] = 1
    repository.index_ready(job, [chunk], vector, "test-only-unit-vector", "test-only")
    repository.fail(job, "summarizing", "LLM_UNAVAILABLE", "Сервис конспектов недоступен")
    lecture, rows = SearchRepository(repository.settings).index(lecture_id)
    assert lecture["index_ready_at"] is not None
    assert len(rows) == 1
    assert rows[0]["chunk_id"] == chunk["id"]
    np.testing.assert_array_equal(np.frombuffer(rows[0]["embedding"], dtype="<f4"), vector[0])


@pytest.mark.mysql
@pytest.mark.parametrize("llm_fails", [False, True])
@pytest.mark.parametrize("import_source", [False, True])
def test_worker_with_real_ffmpeg_and_mysql(mysql_job, tmp_path, llm_fails, import_source):
    """Real media tools + queue + DB, external ML/LLM doubles (not quality evaluation)."""
    from dataclasses import replace
    import shutil
    import subprocess
    from app.pipeline.audio import AudioProcessor

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.fail("Integration worker test requires FFmpeg and ffprobe")
    repository, lecture_id, job_id = mysql_job
    settings = replace(repository.settings, storage_root=tmp_path)
    video = tmp_path / "videos" / f"{lecture_id}.mp4"
    video.parent.mkdir()
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=black:s=160x120:r=10", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
         "-t", "1", "-c:v", "mpeg4", "-c:a", "aac", "-y", str(video)],
        check=True, timeout=30,
    )
    segment_id = str(uuid4())
    importer = None
    if import_source:
        import json
        from app.pipeline.import_video import VideoImporter
        original = tmp_path / 'download-source.mp4'
        video.replace(original)
        (tmp_path / 'imports').mkdir()
        (tmp_path / 'imports' / f'{lecture_id}.json').write_text(json.dumps({'url':'https://vimeo.com/1234567'}))
        importer = VideoImporter(settings)
        def local_download(url, job):
            target = importer.directory(job) / 'video.mp4'
            target.parent.mkdir(parents=True)
            shutil.copyfile(original, target)
            return target
        importer.download = local_download
    segment = {"id": segment_id, "position": 0, "start_ms": 0, "end_ms": 900,
               "text": "Теорема Пифагора связывает стороны прямоугольного треугольника."}
    orchestrator = make_worker(tmp_path)

    def summary(segments):
        if llm_fails:
            raise PipelineError("LLM_UNAVAILABLE", "Сервис конспектов недоступен")
        return {"text": "Конспект теста обработки", "provider": "test-double", "model_name": "test-double",
                "chapters": [{"id": str(uuid4()), "position": 0, "title": "Теорема Пифагора", "level": 1,
                              "start_segment_id": segments[0]["id"]}]}

    worker = Worker(settings, repository=repository, audio=AudioProcessor(settings),
                    transcriber=SimpleNamespace(transcribe=lambda *_: [segment]),
                    embedder=orchestrator.embedder,
                    summarizer=SimpleNamespace(summarize=summary), importer=importer)
    assert worker.run_once() is True
    record = fetch_job(repository, job_id)
    assert record["status"] == ("failed" if llm_fails else "succeeded")
    assert record["failed_stage"] == ("summarizing" if llm_fails else None)
    with repository.connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM lectures WHERE id=%s", (lecture_id,))
        lecture = cursor.fetchone()
        assert lecture["status"] == ("failed" if llm_fails else "ready")
        assert 900 <= lecture["duration_ms"] <= 1200
        if import_source:
            assert video.is_file() and lecture['size_bytes'] == video.stat().st_size
        assert lecture["transcript_ready_at"] is not None
        assert lecture["index_ready_at"] is not None
        assert (lecture["summary_ready_at"] is None) == llm_fails
        cursor.execute("SELECT COUNT(*) AS n FROM transcript_segments WHERE lecture_id=%s", (lecture_id,))
        assert cursor.fetchone()["n"] == 1
        cursor.execute("SELECT COUNT(*) AS n FROM search_chunks WHERE lecture_id=%s", (lecture_id,))
        assert cursor.fetchone()["n"] == 1
        cursor.execute("SELECT COUNT(*) AS n FROM lecture_chapters WHERE lecture_id=%s", (lecture_id,))
        assert cursor.fetchone()["n"] == (0 if llm_fails else 1)
    assert list((tmp_path / "audio").iterdir()) == []


@pytest.mark.mysql
def test_import_publication_is_fenced(mysql_job, tmp_path):
    repository, lecture_id, job_id = mysql_job
    old = repository.claim()
    staged, destination = tmp_path/'download.mp4', tmp_path/'public.mp4'
    staged.write_bytes(b'validated-import')
    expire(repository, job_id)
    current = repository.claim()
    with pytest.raises(LeaseLost):
        repository.import_ready(old, staged, destination)
    assert staged.exists() and not destination.exists()
    repository.import_ready(current, staged, destination)
    assert destination.read_bytes() == b'validated-import'
