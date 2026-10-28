import logging
import time
from uuid import uuid4
from .errors import PipelineError


class Transcriber:
    def __init__(self, settings):
        self.settings = settings
        self.model = None
        self.model_name = ("Systran/faster-whisper-" + settings.whisper_model
                           if "/" not in settings.whisper_model else settings.whisper_model)

    def load(self):
        if self.model is not None:
            return
        try:
            from huggingface_hub import snapshot_download
            from faster_whisper import WhisperModel
            path = snapshot_download(
                self.model_name,
                revision=self.settings.whisper_revision,
                cache_dir=str(self.settings.model_cache),
                local_files_only=self.settings.models_offline,
                allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.*", "preprocessor_config.json"],
            )
            self.model = WhisperModel(path, device="cpu", compute_type="int8",
                                      cpu_threads=self.settings.cpu_threads, num_workers=1)
        except Exception as exc:
            logging.getLogger(__name__).error("ASR unavailable: type=%s", type(exc).__name__)
            raise PipelineError("ASR_UNAVAILABLE", "Модель распознавания речи недоступна") from None

    def transcribe(self, audio_path, duration_ms):
        self.load()
        try:
            source, _ = self.model.transcribe(str(audio_path), beam_size=5, vad_filter=True)
            result = []
            next_report = 0
            for item in source:
                now = time.monotonic()
                if now >= next_report:
                    logging.getLogger(__name__).info('ASR progress recognized_until_s=%.1f', item.end)
                    next_report = now + 15
                text = item.text.strip()
                start = max(0, round(item.start * 1000))
                end = min(duration_ms, round(item.end * 1000))
                if not text or end <= start:
                    continue
                result.append(dict(id=str(uuid4()), position=len(result), start_ms=start, end_ms=end, text=text))
            if not result:
                raise PipelineError("TRANSCRIPT_EMPTY", "В аудиодорожке не удалось распознать речь")
            logging.getLogger(__name__).info('ASR completed segments=%d', len(result))
            return result
        except PipelineError:
            raise
        except Exception:
            raise PipelineError("TRANSCRIPTION_FAILED", "Не удалось распознать речь") from None
