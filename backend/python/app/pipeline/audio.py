import json
import math
from pathlib import Path
import subprocess
from .errors import PipelineError


class AudioProcessor:
    def __init__(self, settings):
        self.settings = settings

    def extract(self, video_path: Path, audio_path: Path) -> int:
        try:
            result = subprocess.run([
                "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                "-show_format", "-show_streams", "-of", "json", str(video_path),
            ], check=True, capture_output=True, timeout=30)
            info = json.loads(result.stdout)
            duration = float(info["format"]["duration"])
            streams = info["streams"]
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("duration")
            if not any(s.get("codec_type") == "video" for s in streams):
                raise ValueError("video")
            if not any(s.get("codec_type") == "audio" for s in streams):
                raise PipelineError("NO_AUDIO_STREAM", "В видео отсутствует аудиодорожка")
            containers = set(info["format"].get("format_name", "").split(","))
            if not containers.intersection({"mov", "mp4", "matroska", "webm"}):
                raise ValueError("container")
            duration_ms = round(duration * 1000)
            if duration_ms > self.settings.max_duration_ms:
                raise PipelineError("VIDEO_TOO_LONG", "Длительность видео превышает допустимый предел")
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([
                "ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file,pipe",
                "-threads", str(self.settings.cpu_threads), "-i", str(video_path),
                "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                "-t", str(duration), str(audio_path),
            ], check=True, capture_output=True, timeout=self.settings.ffmpeg_timeout)
            return duration_ms
        except PipelineError:
            raise
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            raise PipelineError("AUDIO_EXTRACTION_FAILED", "Не удалось извлечь аудио из видео") from None
