#!/usr/bin/env python3
"""Пересчёт эмбеддингов фикстур настоящей моделью intfloat/multilingual-e5-small.

Зачем: seed_fixtures.sql кладёт UI-only векторы (one-hot), по ним поиск не проверить.
Этот скрипт перезаписывает chunk_embeddings реальными векторами.

Установка:  pip install pymysql numpy sentence-transformers
Запуск:     python seeds/seed_real_embeddings.py --revision <хэш коммита модели на Hugging Face>
Окружение:  DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD

--revision обязателен: контракт требует ТОЧНУЮ ревизию, и FastAPI сверяет её
с ревизией, которой пользуется при кодировании запроса.
Префикс "passage: " для документов (и "query: " для запросов) — рекомендация авторов e5;
он должен совпадать у worker и FastAPI. Согласуйте это с ними.
"""
import argparse
import os

import numpy as np
import pymysql

MODEL_NAME = "intfloat/multilingual-e5-small"
DIMENSIONS = 384
DEFAULT_LECTURES = (
    "a0000000-0000-4000-8000-000000000002",
    "a0000000-0000-4000-8000-000000000003",
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--revision", required=True)
    ap.add_argument("--lecture-id", action="append", help="по умолчанию — фикстурные ready и failed")
    args = ap.parse_args()
    lectures = args.lecture_id or list(DEFAULT_LECTURES)

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME, revision=args.revision)

    conn = pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "kyberryvok"),
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SET time_zone = '+00:00'")
            for lid in lectures:
                cur.execute("SELECT id, text FROM search_chunks WHERE lecture_id=%s ORDER BY position", (lid,))
                rows = cur.fetchall()
                if not rows:
                    print(f"{lid}: чанков нет, пропуск")
                    continue
                vectors = model.encode(
                    ["passage: " + text for _, text in rows],
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                )
                for (chunk_id, _), vec in zip(rows, vectors):
                    arr = np.asarray(vec, dtype="<f4")
                    assert arr.shape == (DIMENSIONS,), arr.shape
                    assert np.isfinite(arr).all(), "NaN/Inf в векторе"
                    assert abs(float(np.linalg.norm(arr)) - 1.0) < 1e-3, "вектор не нормализован"
                    cur.execute(
                        """INSERT INTO chunk_embeddings
                               (chunk_id, model_name, model_revision, dimensions, embedding)
                           VALUES (%s, %s, %s, %s, %s)
                           ON DUPLICATE KEY UPDATE
                               model_name = VALUES(model_name),
                               model_revision = VALUES(model_revision),
                               dimensions = VALUES(dimensions),
                               embedding = VALUES(embedding)""",
                        (chunk_id, MODEL_NAME, args.revision, DIMENSIONS, arr.tobytes()),
                    )
                print(f"{lid}: обновлено {len(rows)} эмбеддингов")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
