"""Download/cache the configured real ASR and embedding models before offline use."""
import sys

from app.config import Settings
from app.pipeline.embed import Embedder
from app.pipeline.errors import PipelineError
from app.pipeline.transcribe import Transcriber


def main():
    settings = Settings.from_env()
    try:
        embedder = Embedder(settings)
        embedder.load()
        Transcriber(settings).load()
    except PipelineError as exc:
        print(f"Model preparation FAILED: {exc.code}", file=sys.stderr)
        return 1
    print(f"Models loaded; E5 snapshot: {embedder.revision}")
    print("Pin EMBEDDING_REVISION to this SHA for both search and worker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
