"""Public YouTube/Vimeo import. No arbitrary URL, playlist or shell execution."""
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID
from .errors import PipelineError


def canonical_url(url):
    try:
        p = urlsplit(url)
        if p.scheme not in ('http', 'https') or p.username or p.password or p.port:
            raise ValueError()
        host, path = p.hostname, p.path
        video_id = None
        if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
            if path == '/watch':
                ids = parse_qs(p.query).get('v', [])
                video_id = ids[0] if len(ids) == 1 else None
            else:
                m = re.fullmatch(r'/(?:shorts|embed)/([\w-]{11})/?', path, re.ASCII)
                video_id = m[1] if m else None
        elif host == 'youtu.be':
            video_id = path.strip('/')
        if video_id and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            return 'https://www.youtube.com/watch?v=' + video_id
        if host in ('vimeo.com', 'www.vimeo.com', 'player.vimeo.com'):
            m = re.fullmatch(r'/(?:video/)?([0-9]{1,15})/?', path)
            if m:
                return 'https://vimeo.com/' + m[1]
    except (ValueError, TypeError, AttributeError):
        pass
    raise PipelineError('IMPORT_SOURCE_INVALID', 'Поддерживаются только публичные видео YouTube и Vimeo')


class VideoImporter:
    def __init__(self, settings):
        self.settings = settings

    def directory(self, job):
        job_id, token = str(UUID(job['id'])), str(UUID(job['worker_token']))
        return self.settings.storage_root / 'downloads' / f'{job_id}-{token}'

    def source(self, job):
        lecture_id = str(UUID(job['lecture_id']))
        path = self.settings.storage_root / 'imports' / f'{lecture_id}.json'
        if not path.exists():
            return None
        try:
            if not path.resolve().is_relative_to((self.settings.storage_root / 'imports').resolve()) or path.stat().st_size > 4096:
                raise ValueError()
            data = json.loads(path.read_text())
            url = canonical_url(data['url'])
            if job['storage_key'] != f'videos/{lecture_id}.mp4':
                raise ValueError()
            return url
        except (OSError, ValueError, KeyError, TypeError):
            raise PipelineError('IMPORT_SOURCE_INVALID', 'Не удалось прочитать источник импорта') from None

    def download(self, url, job):
        url = canonical_url(url)
        directory = self.directory(job)
        directory.mkdir(parents=True, exist_ok=True)
        maximum = getattr(self.settings, 'max_import_bytes', 524288000)
        duration = getattr(self.settings, 'max_duration_ms', 7200000) // 1000
        command = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist',
                   '--quiet', '--no-warnings', '--no-progress', '--no-cache-dir', '--no-overwrites',
                   '--retries', '2', '--fragment-retries', '2', '--socket-timeout', '20',
                   '--max-filesize', str(maximum), '--match-filter', f'duration <= {duration} & !is_live',
                   '--use-extractors', 'youtube,vimeo',
                   '--js-runtimes', 'deno',
                   '-f', 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]', '--merge-output-format', 'mp4',
                   '-o', str(directory / 'video.%(ext)s'), '--', url]
        # Output is bounded by quiet mode and never included in API errors/logs.
        log = directory / 'download.log'
        process = None
        logger = logging.getLogger(__name__)
        logger.info('Import download started lecture=%s', job.get('lecture_id', job['id']))
        started = time.monotonic()
        next_report = started + 15
        try:
            with log.open('wb') as output:
                process = subprocess.Popen(command, stdout=output, stderr=output, start_new_session=os.name == 'posix')
                deadline = started + getattr(self.settings, 'import_timeout', 1800)
                while process.poll() is None:
                    now = time.monotonic()
                    if now > deadline:
                        raise PipelineError('IMPORT_TIMEOUT', 'Время загрузки видео истекло')
                    # Two streams plus merge output can coexist temporarily.
                    size = sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
                    if now >= next_report:
                        logger.info('Import download running lecture=%s elapsed_s=%d temporary_bytes=%d', job.get('lecture_id', job['id']), int(now - started), size)
                        next_report = now + 15
                    if size > maximum * 3 + 1048576:
                        raise PipelineError('IMPORT_TOO_LARGE', 'Видео превышает лимит импорта')
                    time.sleep(0.25)
                path = directory / 'video.mp4'
                if process.returncode or not path.is_file() or path.stat().st_size <= 0:
                    logger.warning('Import download failed lecture=%s exit_code=%s', job.get('lecture_id', job['id']), process.returncode)
                    raise PipelineError('IMPORT_FAILED', 'Не удалось загрузить публичное видео; проверьте доступность ссылки')
                if path.stat().st_size > maximum:
                    raise PipelineError('IMPORT_TOO_LARGE', 'Видео превышает лимит импорта')
                logger.info('Import download completed lecture=%s bytes=%d', job.get('lecture_id', job['id']), path.stat().st_size)
                return path
        except OSError:
            raise PipelineError('IMPORT_FAILED', 'Загрузчик видео недоступен') from None
        finally:
            if process is not None and process.poll() is None:
                if os.name == 'posix':
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.wait()
            log.unlink(missing_ok=True)

    def cleanup(self, job):
        shutil.rmtree(self.directory(job), ignore_errors=True)
