"""Small real-provider smoke check; prints no credentials or response body."""
import sys

from app.config import Settings
from app.pipeline.errors import PipelineError
from app.providers.llm import Summarizer


def main():
    segments = [
        dict(id="11111111-1111-4111-8111-111111111111", text="В прямоугольном треугольнике квадрат гипотенузы равен сумме квадратов катетов."),
        dict(id="22222222-2222-4222-8222-222222222222", text="Например, катеты три и четыре дают гипотенузу пять. Эта теорема помогает вычислять расстояния."),
    ]
    try:
        result = Summarizer(Settings.from_env()).summarize(segments)
    except PipelineError as exc:
        print(f"LLM smoke FAILED: {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    print(f"LLM smoke OK: {len(result['text'])} characters, {len(result['chapters'])} anchored chapters")
    print("Protocol and anchors passed; lecture accuracy still requires human review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
