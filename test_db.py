#!/usr/bin/env python3
"""Проверки БД «Киберрывок» (пункт 4 обязанностей участника БД).

Установка:  pip install pymysql
Запуск:     python tests/test_db.py
Окружение:  DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD
Лучше гонять на отдельной базе (kyberryvok_test) после `python migrate.py`:
тесты создают свои строки с случайными UUID и удаляют их в конце.

Функции claim_job / publish_segments / retry_lecture / reset_derived ниже — эталонная
реализация протокола из database.md (порядок блокировок, проверка worker_token и lease).
Их можно отдать бэкенду и worker как образец.
"""
import os
import struct
import sys
import threading
import traceback
import uuid

import pymysql

# MySQL error codes
ER_DUP_ENTRY = 1062
ER_ROW_IS_REFERENCED = 1451      # RESTRICT / родительская строка нужна
ER_NO_REFERENCED_ROW = 1452
ER_CHECK_VIOLATED = 3819

LEASE_SEC = 120
MAX_ATTEMPTS = 3
CREATED = []   # лекции, созданные тестами — удаляются в конце


# ------------------------------------------------------------------ helpers
def connect():
    conn = pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "kyberryvok_test"),
        charset="utf8mb4",
        autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute("SET time_zone = '+00:00'")
    return conn


def uid():
    return str(uuid.uuid4())


def q(conn, sql, args=()):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def expect_error(conn, codes, sql, args=()):
    try:
        q(conn, sql, args)
    except pymysql.MySQLError as e:
        assert e.args[0] in codes, f"ожидали {codes}, получили {e.args}"
        return
    raise AssertionError(f"ожидалась ошибка {codes}, но запрос прошёл: {sql[:70]}")


def new_lecture(conn, status="queued", duration_ms=None):
    lid = uid()
    q(conn,
      """INSERT INTO lectures (id, title, original_filename, storage_key, mime_type,
                               size_bytes, duration_ms, status)
         VALUES (%s, 'test', 't.mp4', %s, 'video/mp4', 1000, %s, %s)""",
      (lid, f"videos/{lid}.mp4", duration_ms, status))
    CREATED.append(lid)
    return lid


def new_job(conn, lid, status="queued", stage="queued"):
    jid = uid()
    q(conn, "INSERT INTO processing_jobs (id, lecture_id, status, stage) VALUES (%s,%s,%s,%s)",
      (jid, lid, status, stage))
    return jid


def add_segments(conn, lid, n):
    ids = []
    for i in range(n):
        sid = uid()
        q(conn,
          "INSERT INTO transcript_segments (id, lecture_id, position, start_ms, end_ms, text) "
          "VALUES (%s,%s,%s,%s,%s,%s)",
          (sid, lid, i, i * 1000, (i + 1) * 1000, f"текст {i}"))
        ids.append(sid)
    return ids


def add_chunk(conn, lid, position, first_id, last_id):
    cid = uid()
    q(conn,
      "INSERT INTO search_chunks (id, lecture_id, position, start_ms, end_ms, text, "
      "first_segment_id, last_segment_id) VALUES (%s,%s,%s,0,1000,'чанк',%s,%s)",
      (cid, lid, position, first_id, last_id))
    return cid


def good_embedding():
    return struct.pack("<384f", 1.0, *([0.0] * 383))


def add_embedding(conn, chunk_id):
    q(conn,
      "INSERT INTO chunk_embeddings (chunk_id, model_revision, embedding) VALUES (%s,'rev-test',%s)",
      (chunk_id, good_embedding()))


def add_full_derived(conn, lid):
    segs = add_segments(conn, lid, 3)
    chunk = add_chunk(conn, lid, 0, segs[0], segs[2])
    add_embedding(conn, chunk)
    q(conn, "INSERT INTO lecture_summaries (lecture_id, text, provider, model_name) "
            "VALUES (%s,'конспект','test','test')", (lid,))
    q(conn, "INSERT INTO lecture_chapters (id, lecture_id, position, title, level, start_segment_id) "
            "VALUES (%s,%s,0,'Глава',1,%s)", (uid(), lid, segs[0]))
    q(conn, "UPDATE lectures SET transcript_ready_at=UTC_TIMESTAMP(3), index_ready_at=UTC_TIMESTAMP(3), "
            "summary_ready_at=UTC_TIMESTAMP(3) WHERE id=%s", (lid,))
    return segs, chunk


def count(conn, table, where="1=1", args=()):
    return q(conn, f"SELECT COUNT(*) FROM {table} WHERE {where}", args)[0][0]


# ------------------------------------------- эталонный протокол из database.md
RESET_ORDER = [
    "DELETE FROM lecture_chapters WHERE lecture_id=%s",
    "DELETE FROM lecture_summaries WHERE lecture_id=%s",
    "DELETE e FROM chunk_embeddings e JOIN search_chunks c ON c.id=e.chunk_id WHERE c.lecture_id=%s",
    "DELETE FROM search_chunks WHERE lecture_id=%s",
    "DELETE FROM transcript_segments WHERE lecture_id=%s",
]


def reset_derived(cur, lid):
    """chapters → summaries → embeddings → chunks → segments, затем обнуление readiness."""
    for sql in RESET_ORDER:
        cur.execute(sql, (lid,))
    cur.execute("UPDATE lectures SET transcript_ready_at=NULL, index_ready_at=NULL, "
                "summary_ready_at=NULL WHERE id=%s", (lid,))


def purge_lecture(conn, lid):
    conn.begin()
    try:
        with conn.cursor() as cur:
            reset_derived(cur, lid)
            cur.execute("DELETE FROM lectures WHERE id=%s", (lid,))  # jobs — каскадом
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def retry_lecture(conn, lid, new_job_id):
    """Блокировка lectures → проверка failed и отсутствия queued/running → сброс → новое задание."""
    conn.begin()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM lectures WHERE id=%s FOR UPDATE", (lid,))
            row = cur.fetchone()
            if not row or row[0] != "failed":
                conn.rollback()
                return False
            cur.execute("SELECT COUNT(*) FROM processing_jobs WHERE lecture_id=%s "
                        "AND status IN ('queued','running')", (lid,))
            if cur.fetchone()[0]:
                conn.rollback()
                return False
            reset_derived(cur, lid)
            cur.execute("INSERT INTO processing_jobs (id, lecture_id, status, stage) "
                        "VALUES (%s,%s,'queued','queued')", (new_job_id, lid))
            cur.execute("UPDATE lectures SET status='queued' WHERE id=%s", (lid,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def claim_job(conn, job_id):
    """Захват задания. Возвращает новый worker_token или None (проиграл гонку / нечего брать)."""
    conn.begin()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT lecture_id FROM processing_jobs WHERE id=%s", (job_id,))
            lid = cur.fetchone()[0]
            cur.execute("SELECT id FROM lectures WHERE id=%s FOR UPDATE", (lid,))   # 1) лекция
            cur.execute(                                                            # 2) задание
                "SELECT status, attempt, stage, "
                "(lease_expires_at IS NOT NULL AND lease_expires_at < UTC_TIMESTAMP(3)) "
                "FROM processing_jobs WHERE id=%s FOR UPDATE", (job_id,))
            status, attempt, stage, expired = cur.fetchone()      # перепроверка под блокировкой
            if status == "queued":
                pass
            elif status == "running" and expired:
                if attempt >= MAX_ATTEMPTS:
                    cur.execute(
                        "UPDATE processing_jobs SET status='failed', failed_stage=stage, "
                        "error_code='WORKER_LEASE_EXPIRED', "
                        "error_message='Обработка прервана: исполнитель перестал отвечать.', "
                        "worker_token=NULL, lease_expires_at=NULL, finished_at=UTC_TIMESTAMP(3) "
                        "WHERE id=%s", (job_id,))
                    cur.execute("UPDATE lectures SET status='failed' WHERE id=%s", (lid,))
                    conn.commit()
                    return None
                reset_derived(cur, lid)       # повторный захват начинает с чистого листа
            else:
                conn.rollback()
                return None
            token = uid()
            cur.execute(
                "UPDATE processing_jobs SET status='running', stage='extracting', "
                "attempt=attempt+1, worker_token=%s, heartbeat_at=UTC_TIMESTAMP(3), "
                "lease_expires_at=DATE_ADD(UTC_TIMESTAMP(3), INTERVAL %s SECOND), "
                "started_at=COALESCE(started_at, UTC_TIMESTAMP(3)) WHERE id=%s",
                (token, LEASE_SEC, job_id))
            cur.execute("UPDATE lectures SET status='extracting' WHERE id=%s", (lid,))
        conn.commit()
        return token
    except Exception:
        conn.rollback()
        raise


def publish_segments(conn, lid, job_id, token, n):
    """Публикация транскрипта: проверка token + running + lease, сегменты и ready_at — в одной транзакции."""
    conn.begin()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM lectures WHERE id=%s FOR UPDATE", (lid,))
            cur.execute("SELECT worker_token, status, lease_expires_at > UTC_TIMESTAMP(3) "
                        "FROM processing_jobs WHERE id=%s FOR UPDATE", (job_id,))
            row = cur.fetchone()
            if not row or row[0] != token or row[1] != "running" or not row[2]:
                conn.rollback()
                return False
            for i in range(n):
                cur.execute(
                    "INSERT INTO transcript_segments (id, lecture_id, position, start_ms, end_ms, text) "
                    "VALUES (%s,%s,%s,%s,%s,%s)", (uid(), lid, i, i * 1000, (i + 1) * 1000, f"текст {i}"))
            cur.execute("UPDATE lectures SET transcript_ready_at=UTC_TIMESTAMP(3) WHERE id=%s", (lid,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def expire_lease(conn, job_id):
    q(conn, "UPDATE processing_jobs SET lease_expires_at = UTC_TIMESTAMP(3) - INTERVAL 1 SECOND "
            "WHERE id=%s", (job_id,))


# -------------------------------------------------------------------- tests
def test_check_constraints(c):
    expect_error(c, {ER_CHECK_VIOLATED}, "INSERT INTO lectures (id,title,original_filename,storage_key,mime_type,size_bytes,duration_ms) "
                 "VALUES (%s,'t','t.mp4',%s,'video/mp4',1,0)", (uid(), "videos/x0.mp4"))
    expect_error(c, {ER_CHECK_VIOLATED}, "INSERT INTO lectures (id,title,original_filename,storage_key,mime_type,size_bytes,status) "
                 "VALUES (%s,'t','t.mp4',%s,'video/mp4',1,'bogus')", (uid(), "videos/x1.mp4"))
    lid = new_lecture(c)
    expect_error(c, {ER_CHECK_VIOLATED}, "INSERT INTO processing_jobs (id,lecture_id,status) VALUES (%s,%s,'bogus')", (uid(), lid))
    expect_error(c, {ER_CHECK_VIOLATED}, "INSERT INTO processing_jobs (id,lecture_id,stage) VALUES (%s,%s,'bogus')", (uid(), lid))
    expect_error(c, {ER_CHECK_VIOLATED}, "INSERT INTO transcript_segments VALUES (%s,%s,0,500,500,'x')", (uid(), lid))
    expect_error(c, {ER_CHECK_VIOLATED}, "INSERT INTO transcript_segments VALUES (%s,%s,0,0,500,'   ')", (uid(), lid))
    segs = add_segments(c, lid, 2)
    expect_error(c, {ER_CHECK_VIOLATED}, "INSERT INTO search_chunks VALUES (%s,%s,5,900,100,'x',%s,%s)", (uid(), lid, segs[0], segs[1]))
    expect_error(c, {ER_CHECK_VIOLATED}, "INSERT INTO lecture_chapters VALUES (%s,%s,5,'Глава',3,%s)", (uid(), lid, segs[0]))
    expect_error(c, {ER_CHECK_VIOLATED}, "INSERT INTO lecture_summaries (lecture_id,text,provider,model_name) VALUES (%s,'  ','p','m')", (lid,))


def test_unique_constraints(c):
    lid = new_lecture(c)
    add_segments(c, lid, 1)
    expect_error(c, {ER_DUP_ENTRY}, "INSERT INTO transcript_segments VALUES (%s,%s,0,0,10,'дубль')", (uid(), lid))
    expect_error(c, {ER_DUP_ENTRY}, "INSERT INTO lectures (id,title,original_filename,storage_key,mime_type,size_bytes) "
                 "VALUES (%s,'t','t.mp4',%s,'video/mp4',1)", (uid(), f"videos/{lid}.mp4"))


def test_embedding_format_constraints(c):
    lid = new_lecture(c)
    segs = add_segments(c, lid, 2)
    chunk = add_chunk(c, lid, 0, segs[0], segs[1])
    expect_error(c, {ER_CHECK_VIOLATED}, "INSERT INTO chunk_embeddings (chunk_id,model_revision,embedding) VALUES (%s,'r',%s)",
                 (chunk, b"\x00" * 100))                                      # неверная длина
    expect_error(c, {ER_CHECK_VIOLATED}, "INSERT INTO chunk_embeddings (chunk_id,model_revision,dimensions,embedding) "
                 "VALUES (%s,'r',128,%s)", (chunk, b"\x00" * 512))            # не 384
    add_embedding(c, chunk)                                                    # корректный проходит
    assert count(c, "chunk_embeddings", "chunk_id=%s AND OCTET_LENGTH(embedding)=1536", (chunk,)) == 1


def test_fk_restrict_on_segments(c):
    lid = new_lecture(c)
    segs = add_segments(c, lid, 2)
    add_chunk(c, lid, 0, segs[0], segs[1])
    expect_error(c, {ER_ROW_IS_REFERENCED}, "DELETE FROM transcript_segments WHERE id=%s", (segs[0],))
    expect_error(c, {ER_NO_REFERENCED_ROW}, "INSERT INTO lecture_chapters VALUES (%s,%s,0,'Глава',1,%s)", (uid(), lid, uid()))


def test_cleanup_order_and_cascades(c):
    lid = new_lecture(c, status="failed")
    new_job(c, lid, "failed", "summarizing")
    segs, chunk = add_full_derived(c, lid)

    # неправильный порядок (сегменты раньше чанков/глав) обязан быть отклонён
    expect_error(c, {ER_ROW_IS_REFERENCED}, "DELETE FROM transcript_segments WHERE lecture_id=%s", (lid,))

    # правильный порядок + обнуление readiness; видео (строка лекции) и история заданий остаются
    c.begin()
    with c.cursor() as cur:
        reset_derived(cur, lid)
    c.commit()
    for t, col in [("lecture_chapters", "lecture_id"), ("lecture_summaries", "lecture_id"),
                   ("search_chunks", "lecture_id"), ("transcript_segments", "lecture_id")]:
        assert count(c, t, f"{col}=%s", (lid,)) == 0, t
    assert count(c, "chunk_embeddings", "chunk_id=%s", (chunk,)) == 0
    row = q(c, "SELECT transcript_ready_at, index_ready_at, summary_ready_at, storage_key FROM lectures WHERE id=%s", (lid,))[0]
    assert row[0] is None and row[1] is None and row[2] is None and row[3] == f"videos/{lid}.mp4"
    assert count(c, "processing_jobs", "lecture_id=%s", (lid,)) == 1

    # каскад: удаление лекции убирает задания
    purge_lecture(c, lid)
    assert count(c, "lectures", "id=%s", (lid,)) == 0
    assert count(c, "processing_jobs", "lecture_id=%s", (lid,)) == 0


def test_embedding_cascades_from_chunk(c):
    lid = new_lecture(c)
    segs = add_segments(c, lid, 2)
    chunk = add_chunk(c, lid, 0, segs[0], segs[1])
    add_embedding(c, chunk)
    q(c, "DELETE FROM search_chunks WHERE id=%s", (chunk,))
    assert count(c, "chunk_embeddings", "chunk_id=%s", (chunk,)) == 0


def test_retry_is_transactional(c):
    lid = new_lecture(c, status="failed")
    old = new_job(c, lid, "failed", "summarizing")
    add_full_derived(c, lid)

    new_id = uid()
    assert retry_lecture(c, lid, new_id) is True
    jobs = q(c, "SELECT id, status FROM processing_jobs WHERE lecture_id=%s ORDER BY created_at, id", (lid,))
    assert len(jobs) == 2 and {j[1] for j in jobs} == {"failed", "queued"}   # старое сохранено как история
    assert count(c, "transcript_segments", "lecture_id=%s", (lid,)) == 0
    assert q(c, "SELECT status, summary_ready_at FROM lectures WHERE id=%s", (lid,))[0] == ("queued", None)

    # повторный retry при активном задании отклоняется и ничего не меняет
    assert retry_lecture(c, lid, uid()) is False
    assert count(c, "processing_jobs", "lecture_id=%s", (lid,)) == 2


def test_two_concurrent_claims_one_wins(c):
    for _ in range(5):
        lid = new_lecture(c)
        jid = new_job(c, lid)
        results, errors = [], []
        barrier = threading.Barrier(2)

        def run():
            conn = connect()
            try:
                barrier.wait()
                results.append(claim_job(conn, jid))
            except Exception as e:     # noqa: BLE001
                errors.append(e)
            finally:
                conn.close()

        threads = [threading.Thread(target=run) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors, errors
        winners = [r for r in results if r]
        assert len(results) == 2 and len(winners) == 1, results
        assert q(c, "SELECT attempt, worker_token, status FROM processing_jobs WHERE id=%s", (jid,))[0] == (1, winners[0], "running")


def test_stale_worker_token_rejected(c):
    lid = new_lecture(c)
    jid = new_job(c, lid)
    t1 = claim_job(c, jid)
    assert t1
    expire_lease(c, jid)
    assert publish_segments(c, lid, jid, t1, 3) is False, "просроченный lease не должен публиковать"

    t2 = claim_job(c, jid)                       # перехват просроченного running
    assert t2 and t2 != t1
    assert q(c, "SELECT attempt FROM processing_jobs WHERE id=%s", (jid,))[0][0] == 2
    assert publish_segments(c, lid, jid, t1, 3) is False, "старый токен не должен публиковать"
    assert count(c, "transcript_segments", "lecture_id=%s", (lid,)) == 0
    assert q(c, "SELECT transcript_ready_at FROM lectures WHERE id=%s", (lid,))[0][0] is None

    assert publish_segments(c, lid, jid, t2, 3) is True
    assert count(c, "transcript_segments", "lecture_id=%s", (lid,)) == 3
    assert q(c, "SELECT transcript_ready_at IS NOT NULL FROM lectures WHERE id=%s", (lid,))[0][0] == 1


def test_max_three_claims_then_lease_expired(c):
    lid = new_lecture(c)
    jid = new_job(c, lid)
    for attempt in (1, 2, 3):
        assert claim_job(c, jid), f"захват {attempt} должен пройти"
        expire_lease(c, jid)
    assert claim_job(c, jid) is None
    job = q(c, "SELECT status, error_code, failed_stage, finished_at IS NOT NULL FROM processing_jobs WHERE id=%s", (jid,))[0]
    assert job[0] == "failed" and job[1] == "WORKER_LEASE_EXPIRED" and job[2] and job[3] == 1
    assert q(c, "SELECT status FROM lectures WHERE id=%s", (lid,))[0][0] == "failed"


def test_fixtures_consistent(c):
    """Работает, если загружен seeds/seed_fixtures.sql; иначе пропускается.
    Проверяет инварианты, которые БД сама не гарантирует (их обязан соблюдать worker)."""
    ready = "a0000000-0000-4000-8000-000000000002"
    failed = "a0000000-0000-4000-8000-000000000003"
    if count(c, "lectures", "id IN (%s,%s)", (ready, failed)) < 2:
        print("    (фикстуры не загружены — пропуск)")
        return
    for lid in (ready, failed):
        # чанки: сегменты той же лекции, first.position <= last.position, время охватывает сегменты
        bad = q(c, """SELECT c.id FROM search_chunks c
                      JOIN transcript_segments f ON f.id=c.first_segment_id
                      JOIN transcript_segments l ON l.id=c.last_segment_id
                      WHERE c.lecture_id=%s AND (f.lecture_id<>c.lecture_id OR l.lecture_id<>c.lecture_id
                            OR f.position>l.position OR c.start_ms<>f.start_ms OR c.end_ms<>l.end_ms)""", (lid,))
        assert not bad, f"битые чанки {bad}"
        # эмбеддинг у каждого чанка, длина 1536
        assert count(c, "search_chunks c LEFT JOIN chunk_embeddings e ON e.chunk_id=c.id",
                     "c.lecture_id=%s AND (e.chunk_id IS NULL OR OCTET_LENGTH(e.embedding)<>1536)", (lid,)) == 0
        # выход за duration_ms
        assert count(c, "transcript_segments s JOIN lectures l ON l.id=s.lecture_id",
                     "s.lecture_id=%s AND s.end_ms>l.duration_ms", (lid,)) == 0
    # главы: первая — уровня 1, уровень 2 только после 1, время не убывает
    chapters = q(c, """SELECT ch.level, s.start_ms FROM lecture_chapters ch
                       JOIN transcript_segments s ON s.id=ch.start_segment_id
                       WHERE ch.lecture_id=%s ORDER BY ch.position""", (ready,))
    assert chapters and chapters[0][0] == 1
    assert all(chapters[i][1] <= chapters[i + 1][1] for i in range(len(chapters) - 1))
    # failed: транскрипт и индекс есть, конспекта нет, задание упало на summarizing
    row = q(c, "SELECT transcript_ready_at IS NOT NULL, index_ready_at IS NOT NULL, summary_ready_at IS NULL, status "
               "FROM lectures WHERE id=%s", (failed,))[0]
    assert row == (1, 1, 1, "failed")
    assert q(c, "SELECT status, failed_stage FROM processing_jobs WHERE lecture_id=%s", (failed,))[0] == ("failed", "summarizing")
    assert count(c, "lecture_summaries", "lecture_id=%s", (failed,)) == 0


# ------------------------------------------------------------------- runner
def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    conn = connect()
    failed = 0
    try:
        for name, fn in tests:
            try:
                fn(conn)
                print(f"OK    {name}")
            except Exception:    # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
    finally:
        for lid in CREATED:
            try:
                purge_lecture(conn, lid)
            except Exception as e:    # noqa: BLE001
                print(f"не удалось убрать {lid}: {e}")
        conn.close()
    print(f"\n{len(tests) - failed}/{len(tests)} тестов прошло")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
