from pathlib import Path
import re
import threading
import numpy as np
from .errors import PipelineError


class Embedder:
    dimensions = 384

    def __init__(self, settings):
        self.settings = settings
        self.model_name = settings.embedding_model
        self.revision = settings.embedding_revision
        self.model = None
        self.tokenizer = None
        self._lock = threading.Lock()

    def load(self):
        with self._lock:
            if self.model is not None:
                return
            try:
                from huggingface_hub import snapshot_download
                from sentence_transformers import SentenceTransformer
                import torch
                torch.set_num_threads(self.settings.cpu_threads)
                path = snapshot_download(
                    self.model_name, revision=self.settings.embedding_revision,
                    cache_dir=str(self.settings.model_cache),
                    local_files_only=self.settings.models_offline,
                    allow_patterns=["*.json", "*.safetensors", "pytorch_model.bin", "*.txt", "tokenizer.model", "sentencepiece.bpe.model"],
                )
                revision = Path(path).name
                if not re.fullmatch(r"[0-9a-f]{40}", revision):
                    raise ValueError("Expected immutable Hugging Face snapshot revision")
                model = SentenceTransformer(path, device="cpu", trust_remote_code=False)
                if model.get_sentence_embedding_dimension() != self.dimensions:
                    raise ValueError("Embedding dimension differs from database contract")
                self.model = model
                self.tokenizer = model.tokenizer
                self.revision = revision
            except Exception:
                raise PipelineError("EMBEDDING_UNAVAILABLE", "Модель семантического поиска недоступна") from None

    def _encode(self, texts):
        self.load()
        try:
            with self._lock:
                vectors = np.asarray(self.model.encode(texts, batch_size=16, normalize_embeddings=True,
                                                      show_progress_bar=False), dtype="<f4")
            if vectors.ndim != 2 or vectors.shape[1] != self.dimensions or not np.isfinite(vectors).all():
                raise ValueError("Invalid embeddings")
            norms = np.linalg.norm(vectors, axis=1)
            if not np.allclose(norms, 1, atol=1e-4):
                raise ValueError("Invalid normalization")
            return vectors
        except Exception:
            raise PipelineError("EMBEDDING_FAILED", "Не удалось вычислить семантический индекс") from None

    def encode_passages(self, texts):
        return self._encode(["passage: " + text for text in texts])

    def encode_query(self, query):
        return self._encode(["query: " + query])[0]
