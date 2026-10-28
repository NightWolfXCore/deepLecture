"""Long-running queue consumer: python -m app.worker [--once]."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import signal
import threading

from app.config import Settings
from app.pipeline.audio import AudioProcessor
from app.pipeline.chunk import build_chunks
from app.pipeline.embed import Embedder
from app.pipeline.errors import PipelineError
from app.pipeline.transcribe import Transcriber
from app.pipeline.import_video import VideoImporter
from app.providers.llm import Summarizer
from app.repositories.database import JobRepository, LeaseLost

logger = logging.getLogger("deeplecture.worker")


class Heartbeat:
    """Independent connection/thread so long ASR and HTTP calls cannot expire a lease."""
    def __init__(self, repository: JobRepository, job: dict, interval: float):
        self.repository, self.job, self.interval = repository, job, interval
        self.stopped = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="job-heartbeat")

    def _run(self):
        while not self.stopped.wait(self.interval):
            try:
                self.repository.heartbeat(self.job)
            except Exception:
                # Conservatively discard output; never continue publishing with uncertain ownership.
                self.lost.set()
                logger.warning("Job heartbeat lost; output will be discarded")
                return

    def check(self):
        if self.lost.is_set():
            raise LeaseLost("Heartbeat could not maintain job ownership")

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stopped.set()
        self.thread.join(timeout=20)


def video_path(storage_root: Path, key: str) -> Path:
    """Validate DB-derived paths too: a compromised key cannot read arbitrary files."""
    root = storage_root.resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root / "videos") or not path.is_file():
        raise PipelineError("VIDEO_NOT_FOUND", "Исходное видео недоступно")
    return path


class Worker:
    def __init__(self, settings: Settings, repository=None, audio=None, transcriber=None,
                 embedder=None, summarizer=None, heartbeat_interval: float | None = None, importer=None):
        self.settings = settings
        lease = getattr(settings, "lease_seconds", 120)
        self.repository = repository or JobRepository(settings, lease_seconds=lease)
        self.heartbeat_interval = heartbeat_interval or getattr(settings, "heartbeat_seconds", 20)
        if not 0 < self.heartbeat_interval < self.repository.lease_seconds / 2:
            raise ValueError("Heartbeat interval must be less than half the lease")
        self.audio = audio or AudioProcessor(settings)
        self.transcriber = transcriber or Transcriber(settings)
        self.embedder = embedder or Embedder(settings)
        self.summarizer = summarizer or Summarizer(settings)
        self.importer = importer or VideoImporter(settings)

    def run_once(self) -> bool:
        job = self.repository.claim()
        if not job:
            return False
        stage = "extracting"
        # Token-scoped artifacts cannot overwrite another attempt's audio.
        audio_dir = self.settings.storage_root / "audio"
        audio_file = audio_dir / f"{job['id']}-{job['worker_token']}.wav"
        try:
            with Heartbeat(self.repository, job, self.heartbeat_interval) as heartbeat:
                audio_dir.mkdir(parents=True, exist_ok=True)
                source = self.importer.source(job)
                if source is not None:
                    destination = self.settings.storage_root / job['storage_key']
                    video = destination if destination.is_file() else self.importer.download(source, job)
                    duration = self.audio.extract(video, audio_file)
                    heartbeat.check()
                    self.repository.import_ready(job, video, destination)
                else:
                    video = video_path(self.settings.storage_root, job["storage_key"])
                    duration = self.audio.extract(video, audio_file)
                heartbeat.check()
                self.repository.audio_ready(job, duration)
                stage = "transcribing"
                segments = self.transcriber.transcribe(audio_file, duration)
                heartbeat.check()
                self.repository.transcript_ready(job, segments)
                stage = "indexing"
                self.embedder.load()
                chunks = build_chunks(segments, self.embedder.tokenizer)
                vectors = self.embedder.encode_passages([chunk["text"] for chunk in chunks])
                heartbeat.check()
                self.repository.index_ready(job, chunks, vectors, self.embedder.model_name, self.embedder.revision)
                stage = "summarizing"
                summary = self.summarizer.summarize(segments)
                heartbeat.check()
                self.repository.summary_ready(job, summary)
                logger.info("Completed lecture %s", job["lecture_id"])
        except LeaseLost:
            logger.warning("Discarded results from stale job %s", job["id"])
        except Exception as error:
            code = error.code if isinstance(error, PipelineError) else "PROCESSING_FAILED"
            message = error.message if isinstance(error, PipelineError) else "Не удалось обработать лекцию"
            logger.warning("Job %s failed at %s (%s)", job["id"], stage, code)
            try:
                self.repository.fail(job, stage, code, message)
            except LeaseLost:
                logger.warning("Failure ignored: worker lease has expired")
            except Exception:
                # Leave running job recoverable by its lease rather than inventing success.
                logger.error("Could not persist failure; lease recovery will retry the job")
        finally:
            self.importer.cleanup(job)
            try:
                audio_file.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove temporary audio")
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process at most one available job")
    arguments = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    worker = Worker(Settings.from_env())
    while not stop.is_set():
        try:
            handled = worker.run_once()
        except Exception:
            logger.error("Queue unavailable; retrying without changing job state")
            if arguments.once:
                raise SystemExit(1)
            handled = False
        if arguments.once:
            break
        if not handled:
            stop.wait(worker.settings.poll_seconds)


if __name__ == "__main__":
    main()
