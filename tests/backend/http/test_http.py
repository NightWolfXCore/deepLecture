"""Real PHP + MySQL + internal FastAPI integration.

Embeddings are deterministic test vectors: these tests verify integration, NOT
Whisper accuracy, E5 semantic quality, or a live paid LLM provider.
Explicit RUN_HTTP_TESTS=1 and a database ending in _test prevent accidental use.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import struct
import subprocess
import time
from uuid import uuid4

import httpx
import pymysql
import pytest

pytestmark = pytest.mark.skipif(os.getenv("RUN_HTTP_TESTS") != "1", reason="Set RUN_HTTP_TESTS=1 for isolated HTTP integration")
ROOT = Path(__file__).resolve().parents[3]

def uid():
    return str(uuid4())

@pytest.fixture(scope="session")
def db():
    name = os.getenv("DB_NAME", "deeplecture_test")
    assert name.endswith("_test"), "Refusing to clear a database without _test suffix"
    deadline = time.monotonic() + 90
    while True:
        try:
            conn = pymysql.connect(host=os.getenv("DB_HOST", "127.0.0.1"), port=int(os.getenv("DB_PORT", "3306")),
                user=os.getenv("DB_USER", "deeplecture"), password=os.getenv("DB_PASSWORD", ""),
                database=name, autocommit=True, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
            break
        except pymysql.MySQLError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
    with conn.cursor() as cur:
        cur.execute("SET time_zone = '+00:00'")
        for statement in (ROOT / "tests/backend/fixtures/schema.sql").read_text().split(";"):
            if statement.strip():
                cur.execute(statement)
    yield conn
    conn.close()

@pytest.fixture(autouse=True)
def clear_database(db):
    with db.cursor() as cur:
        for table in ("lecture_chapters", "lecture_summaries", "chunk_embeddings", "search_chunks", "transcript_segments", "processing_jobs", "lectures"):
            cur.execute(f"DELETE FROM {table}")

@pytest.fixture(scope="session")
def client():
    base = os.getenv("TEST_API_URL", "http://127.0.0.1:18080")
    # This suite targets an isolated local stack. Do not proxy its traffic to
    # an external corporate/cloud proxy inherited by the test container.
    with httpx.Client(base_url=base, timeout=15, trust_env=False) as api:
        deadline = time.monotonic() + 90
        while True:
            try:
                if api.get("/api/v1/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                pytest.fail("PHP health endpoint did not become available")
            time.sleep(1)
        yield api

@pytest.fixture(scope="session")
def videos(tmp_path_factory):
    directory = tmp_path_factory.mktemp("videos")
    outputs = {}
    for ext, codec in (("mp4", "mpeg4"), ("webm", "libvpx")):
        path = directory / f"lecture.{ext}"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=1", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", codec, "-c:a", "aac" if ext == "mp4" else "libvorbis", "-shortest", "-y", str(path)], check=True)
        outputs[ext] = path
    return outputs

def seed(db, status="ready", available=True):
    lecture, job, segment, chunk, chapter = [uid() for _ in range(5)]
    ts = "2026-10-26 08:00:00.000"
    with db.cursor() as cur:
        cur.execute("""INSERT INTO lectures (id,title,original_filename,storage_key,mime_type,size_bytes,duration_ms,status,transcript_ready_at,index_ready_at,summary_ready_at,created_at,updated_at)
        VALUES (%s,%s,'lecture.mp4',%s,'video/mp4',100,180000,%s,%s,%s,%s,%s,%s)""",
        (lecture, "Теорема Пифагора", f"videos/{lecture}.mp4", status, ts if available else None, ts if available else None, ts if available and status == "ready" else None, ts, ts))
        job_status = "succeeded" if status == "ready" else "failed" if status == "failed" else "queued"
        stage = "ready" if status == "ready" else "summarizing" if status == "failed" else "queued"
        cur.execute("""INSERT INTO processing_jobs (id,lecture_id,status,stage,attempt,failed_stage,error_code,error_message,created_at,updated_at)
        VALUES (%s,%s,%s,%s,0,%s,%s,%s,%s,%s)""", (job, lecture, job_status, stage,
            "summarizing" if status == "failed" else None, "LLM_UNAVAILABLE" if status == "failed" else None, "Конспект временно недоступен" if status == "failed" else None, ts, ts))
        if available:
            cur.execute("INSERT INTO transcript_segments VALUES (%s,%s,0,12000,18000,%s)", (segment, lecture, "Квадрат гипотенузы равен сумме квадратов катетов."))
            cur.execute("INSERT INTO search_chunks VALUES (%s,%s,0,12000,18000,%s,%s,%s)", (chunk, lecture, "Квадрат гипотенузы равен сумме квадратов катетов.", segment, segment))
            vector = struct.pack("<384f", 1.0, *([0.0] * 383))
            cur.execute("INSERT INTO chunk_embeddings VALUES (%s,%s,%s,384,%s)", (chunk, "intfloat/multilingual-e5-small", os.getenv("EMBEDDING_REVISION", "test-fixture-v1"), vector))
            if status == "ready":
                cur.execute("INSERT INTO lecture_summaries VALUES (%s,%s,'test-fixture','test-llm',%s)", (lecture, "Связь сторон прямоугольного треугольника.", ts))
                cur.execute("INSERT INTO lecture_chapters VALUES (%s,%s,0,%s,1,%s)", (chapter, lecture, "Теорема Пифагора", segment))
    return dict(lecture=lecture, job=job, segment=segment, chunk=chunk, chapter=chapter)

def error(response, status, code=None):
    assert response.status_code == status, response.text
    body = response.json()
    assert isinstance(body["error"]["details"], dict)
    assert body["request_id"]
    assert body["error"]["message"]
    if code:
        assert body["error"]["code"] == code
    for forbidden in ("SQLSTATE", "Traceback", "DB_PASSWORD", "PDOException"):
        assert forbidden not in response.text


def test_health_and_readiness(client):
    assert client.get("/api/v1/health").json() == {"data": {"status": "ok"}}
    assert client.get("/api/v1/ready").status_code == 200

@pytest.mark.parametrize("ext,mime", [("mp4", "video/mp4"), ("webm", "video/webm")])
def test_upload_catalog_card_and_range(client, db, videos, ext, mime):
    content = videos[ext].read_bytes()
    response = client.post("/api/v1/lectures", data={"title": "  Русская лекция  "}, files={"file": (f"lecture.{ext}", content, mime)})
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["status"] == "queued" and data["job_id"]
    lecture = data["id"]
    card = client.get(f"/api/v1/lectures/{lecture}").json()["data"]
    assert card["title"] == "Русская лекция"
    assert card["duration_ms"] is None
    assert card["availability"] == {"transcript": False, "search": False, "summary": False, "terms": False}
    assert card["processing"]["can_retry"] is False
    catalog = client.get("/api/v1/lectures").json()
    assert catalog["meta"]["total"] == 1
    assert catalog["data"][0]["id"] == lecture
    media = client.get(card["media_url"], headers={"Range": "bytes=0-15"})
    assert media.status_code == 206, media.text
    assert media.content == content[:16]
    assert media.headers["content-type"].split(";")[0] == mime
    assert media.headers["content-range"] == f"bytes 0-15/{len(content)}"
    head = client.head(card["media_url"])
    assert head.status_code == 200 and not head.content
    with db.cursor() as cur:
        cur.execute("SELECT status FROM processing_jobs WHERE id=%s", (data["job_id"],))
        assert cur.fetchone()["status"] == "queued"


def test_ready_transcript_search_summary(client, db):
    ids = seed(db)
    transcript = client.get(f"/api/v1/lectures/{ids['lecture']}/transcript").json()["data"]
    assert transcript["segments"][0]["id"] == ids["segment"]
    assert transcript["segments"][0]["start_ms"] == 12000
    response = client.post("/api/v1/search", json={"lecture_id": ids["lecture"], "query": "  Как связаны стороны?  ", "limit": 5})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["query"] == "Как связаны стороны?"
    assert body["meta"]["count"] == 1
    hit = body["data"][0]
    assert hit["chunk_id"] == ids["chunk"] and hit["lecture_id"] == ids["lecture"]
    assert hit["score"] == pytest.approx(1.0)
    assert hit["start_ms"] == 12000 and "t=12" in hit["playback_url"]
    summary = client.get(f"/api/v1/lectures/{ids['lecture']}/summary").json()["data"]
    assert summary["text"] and summary["chapters"][0]["start_ms"] == 12000
    assert summary["chapters"][0]["start_segment_id"] == ids["segment"]


def test_failed_summary_keeps_search_available(client, db):
    ids = seed(db, "failed")
    card = client.get(f"/api/v1/lectures/{ids['lecture']}").json()["data"]
    assert card["availability"] == {"transcript": True, "search": True, "summary": False, "terms": True}
    assert card["processing"]["failed_stage"] == "summarizing"
    assert card["processing"]["can_retry"] is True
    error(client.get(f"/api/v1/lectures/{ids['lecture']}/summary"), 409, "SUMMARY_FAILED")
    assert client.post("/api/v1/search", json={"lecture_id": ids["lecture"], "query": "Теорема"}).status_code == 200


def test_retry_is_atomic_clears_outputs_and_preserves_history(client, db):
    ids = seed(db, "failed")
    result = client.post(f"/api/v1/lectures/{ids['lecture']}/retry")
    assert result.status_code == 202, result.text
    assert result.json()["data"]["job_id"] != ids["job"]
    card = client.get(f"/api/v1/lectures/{ids['lecture']}").json()["data"]
    assert card["status"] == "queued" and not any(card["availability"].values())
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM processing_jobs WHERE lecture_id=%s", (ids["lecture"],))
        assert cur.fetchone()["n"] == 2
        for table in ("transcript_segments", "search_chunks", "lecture_chapters", "lecture_summaries"):
            cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE lecture_id=%s", (ids["lecture"],))
            assert cur.fetchone()["n"] == 0
        cur.execute("SELECT COUNT(*) AS n FROM chunk_embeddings")
        assert cur.fetchone()["n"] == 0
    error(client.post(f"/api/v1/lectures/{ids['lecture']}/retry"), 409)


def test_concurrent_retry_only_creates_one_active_job(client, db):
    ids = seed(db, "failed")
    path = f"/api/v1/lectures/{ids['lecture']}/retry"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(path), range(2)))
    assert sorted(r.status_code for r in responses) == [202, 409]
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM processing_jobs WHERE lecture_id=%s AND status IN ('queued','running')", (ids["lecture"],))
        assert cur.fetchone()["n"] == 1

@pytest.mark.parametrize("suffix,code", [("transcript", "TRANSCRIPT_NOT_READY"), ("summary", "SUMMARY_NOT_READY")])
def test_processing_resources_not_ready(client, db, suffix, code):
    ids = seed(db, "queued", False)
    error(client.get(f"/api/v1/lectures/{ids['lecture']}/{suffix}"), 409, code)
    error(client.post("/api/v1/search", json={"lecture_id": ids["lecture"], "query": "Тест"}), 409, "LECTURE_NOT_READY")

@pytest.mark.parametrize("path", ["", "/transcript", "/summary"])
def test_missing_lecture(client, path):
    error(client.get(f"/api/v1/lectures/{uid()}{path}"), 404)

@pytest.mark.parametrize("payload", [None, [], {"lecture_id": "invalid", "query": "x"}, {"lecture_id": "11111111-1111-4111-8111-111111111111", "query": " "}, {"lecture_id": "11111111-1111-4111-8111-111111111111", "query": "x", "limit": 0}, {"lecture_id": "11111111-1111-4111-8111-111111111111", "query": "x", "limit": "5"}])
def test_search_validation(client, payload):
    response = client.post("/api/v1/search", content="null" if payload is None else None, json=payload if payload is not None else None, headers={"Content-Type": "application/json"})
    error(response, 422)


def test_malformed_json(client):
    error(client.post("/api/v1/search", content="{broken", headers={"Content-Type": "application/json"}), 400)


def test_global_search_spans_ready_lectures_and_empty_collection(client, db):
    assert client.post('/api/v1/search', json={'query':'вопрос'}).json()['data'] == []
    first, second = seed(db), seed(db, 'failed')
    seed(db, 'queued', available=False)
    result = client.post('/api/v1/search', json={'query':'стороны треугольника','limit':10})
    assert result.status_code == 200, result.text
    assert {hit['lecture_id'] for hit in result.json()['data']} == {first['lecture'],second['lecture']}
    for hit in result.json()['data']:
        assert hit['lecture_title'] and hit['lecture_id'] in hit['playback_url']


@pytest.mark.parametrize('url', ['https://youtu.be/abcdefghijk?t=12','https://vimeo.com/12345678'])
def test_import_is_queued_and_not_falsely_published(client, db, url):
    response = client.post('/api/v1/lectures/import', json={'title':'Лекция по ссылке','url':url})
    assert response.status_code == 202, response.text
    lecture = response.json()['data']['id']
    card = client.get(f'/api/v1/lectures/{lecture}').json()['data']
    assert card['status'] == 'queued' and card['media_url'] is None
    assert not any(card['availability'].values())
    with db.cursor() as cur:
        cur.execute('SELECT size_bytes,storage_key FROM lectures WHERE id=%s',(lecture,))
        row = cur.fetchone()
    assert row['size_bytes'] == 0 and row['storage_key'] == f'videos/{lecture}.mp4'
    assert client.get(f'/media/imports/{lecture}.json').status_code == 404


@pytest.mark.parametrize('url', ['file:///etc/passwd','http://127.0.0.1/a',
    'https://youtube.com.evil.test/watch?v=abcdefghijk','https://u:p@youtube.com/watch?v=abcdefghijk',
    'https://youtube.com/playlist?list=abc','https://vimeo.com/abc'])
def test_import_rejects_non_platform_or_non_video_urls(client, url):
    error(client.post('/api/v1/lectures/import', json={'title':'Тест','url':url}),422,'VALIDATION_ERROR')


def test_terms_are_anchored_and_available_after_summary_failure(client, db):
    ids = seed(db, 'failed')
    response = client.get(f"/api/v1/lectures/{ids['lecture']}/terms")
    assert response.status_code == 200, response.text
    terms = response.json()['data']['terms']
    assert terms and any('гипотенуз' in term['term'] for term in terms)
    for term in terms:
        assert term['definition']
        assert term['mentions'][0]['segment_id'] == ids['segment']
        assert term['mentions'][0]['start_ms'] == 12000
    queued = seed(db, 'queued', available=False)
    error(client.get(f"/api/v1/lectures/{queued['lecture']}/terms"),409,'TERMS_NOT_READY')


def test_chat_returns_sources_from_selected_lecture(client, db):
    ids = seed(db)
    other = seed(db)
    response = client.post(f"/api/v1/lectures/{ids['lecture']}/chat", json={'question':'Как связаны стороны?','history':[]})
    assert response.status_code == 200, response.text
    result = response.json()['data']
    assert result['answer'] and result['lecture_id'] == ids['lecture']
    assert [src['chunk_id'] for src in result['sources']] == [ids['chunk']]
    assert result['sources'][0]['start_ms'] == 12000
    assert other['chunk'] not in str(result)


@pytest.mark.parametrize('body', [{'question':' '},{'question':'x','history':[{'role':'system','content':'x'}]},
    {'question':'x','history':[{'role':'user','content':'x'}]*7}])
def test_chat_validates_question_and_history(client, db, body):
    ids = seed(db)
    error(client.post(f"/api/v1/lectures/{ids['lecture']}/chat",json=body),422,'VALIDATION_ERROR')

@pytest.mark.parametrize("query", ["page=0", "per_page=101", "page=oops", "page[]=1"])
def test_pagination_validation(client, query):
    error(client.get(f"/api/v1/lectures?{query}"), 422)


def test_upload_rejects_disguised_file(client):
    error(client.post("/api/v1/lectures", data={"title": "Invalid"}, files={"file": ("attack.mp4", b"<?php echo 'bad'; ?>", "video/mp4")}), 415)


def test_upload_missing_file(client):
    error(client.post("/api/v1/lectures", files={"title": (None, "No file")}), 422)


def test_upload_rejects_wrong_request_content_type(client):
    error(client.post("/api/v1/lectures", data={"title": "No multipart"}), 415)


def test_upload_enforces_test_size_limit(client):
    # Test override uses 1 MiB; production remains 500 MiB.
    error(client.post("/api/v1/lectures", data={"title": "Too big"},
          files={"file": ("large.mp4", b"0" * (1048576 + 1), "video/mp4")}), 413)


def test_search_does_not_mix_lectures(client, db):
    first = seed(db)
    second = seed(db)
    response = client.post("/api/v1/search", json={"lecture_id": first["lecture"], "query": "Теорема"})
    assert response.status_code == 200
    assert [row["chunk_id"] for row in response.json()["data"]] == [first["chunk"]]
    assert second["chunk"] not in response.text


def test_rejects_incompatible_embedding_revision(client, db):
    ids = seed(db)
    with db.cursor() as cur:
        cur.execute("UPDATE chunk_embeddings SET model_revision='wrong-model' WHERE chunk_id=%s", (ids["chunk"],))
    response = client.post("/api/v1/search", json={"lecture_id": ids["lecture"], "query": "Теорема"})
    error(response, 503)


def test_missing_schema_returns_actionable_unavailable_without_sql_leak(client, db):
    with db.cursor() as cursor:
        cursor.execute('RENAME TABLE lectures TO lectures_startup_probe')
    try:
        response = client.get('/api/v1/lectures')
        error(response, 503, 'DATABASE_SCHEMA_NOT_READY')
        assert 'SQLSTATE' not in response.text
        assert client.get('/api/v1/ready').status_code == 503
    finally:
        with db.cursor() as cursor:
            cursor.execute('RENAME TABLE lectures_startup_probe TO lectures')
    assert client.get('/api/v1/lectures').status_code == 200


def test_categories_validate_filter_and_change_without_resetting_pipeline(client, db):
    a = seed(db)
    b = seed(db)
    changed = client.patch(f"/api/v1/lectures/{a['lecture']}", json={'category':'mathematics'})
    assert changed.status_code == 200, changed.text
    response = client.get('/api/v1/lectures?category=mathematics').json()
    assert response['meta']['total'] == 1
    assert [row['id'] for row in response['data']] == [a['lecture']]
    card = client.get(f"/api/v1/lectures/{a['lecture']}").json()['data']
    assert card['category'] == 'mathematics' and card['availability']['search']
    error(client.get('/api/v1/lectures?category=invalid'),422,'VALIDATION_ERROR')
    error(client.patch(f"/api/v1/lectures/{a['lecture']}", json={'category':'invalid'}),422,'VALIDATION_ERROR')
    imported = client.post('/api/v1/lectures/import',json={'title':'Physics','url':'https://vimeo.com/12345','category':'physics'})
    assert imported.status_code == 202
    assert client.get('/api/v1/lectures?category=physics').json()['meta']['total'] == 1


def test_summary_docx_is_real_ooxml_and_escapes_source(client, db):
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    ids = seed(db)
    text = '## Определение\n**Логарифм** — показатель степени.\n- a > 0 & a ≠ 1\n1. Решим пример.'
    with db.cursor() as cur:
        cur.execute('UPDATE lectures SET title=%s WHERE id=%s', ('Тема <script> & логарифм',ids['lecture']))
        cur.execute('UPDATE lecture_summaries SET text=%s WHERE lecture_id=%s',(text,ids['lecture']))
    response = client.get(f"/api/v1/lectures/{ids['lecture']}/summary.docx")
    assert response.status_code == 200, response.text[:100]
    assert 'wordprocessingml.document' in response.headers['content-type']
    assert 'attachment;' in response.headers['content-disposition']
    with zipfile.ZipFile(io.BytesIO(response.content)) as doc:
        assert doc.testzip() is None
        for name in doc.namelist(): ET.fromstring(doc.read(name))
        xml = doc.read('word/document.xml').decode()
        assert 'Heading2' in xml and '<w:b/>' in xml
        assert '&lt;script&gt;' in xml and '&amp;' in xml
        assert 'ИИ может ошибаться' in xml and 'Навигация по лекции' in xml
    queued = seed(db, 'queued', False)
    error(client.get(f"/api/v1/lectures/{queued['lecture']}/summary.docx"),409,'SUMMARY_NOT_READY')
    error(client.get('/api/v1/lectures/ffffffff-ffff-4fff-8fff-ffffffffffff/summary.docx'),404,'LECTURE_NOT_FOUND')


def test_regenerate_summary_reuses_transcript_and_index(client, db, tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace
    from app.config import Settings
    from app.repositories.database import JobRepository
    from app.worker import Worker
    ids = seed(db)
    response = client.post(f"/api/v1/lectures/{ids['lecture']}/summary/regenerate")
    assert response.status_code == 202, response.text
    error(client.post(f"/api/v1/lectures/{ids['lecture']}/summary/regenerate"),409,'PROCESSING_ALREADY_ACTIVE')
    card=client.get(f"/api/v1/lectures/{ids['lecture']}").json()['data']
    assert card['availability']['search'] and card['availability']['transcript'] and not card['availability']['summary']
    cfg=replace(Settings.from_env(), storage_root=tmp_path)
    def summary(segments):
        assert len(segments)==1
        return {'text':'## Определение\nУчебный конспект','provider':'test','model_name':'fixture',
            'chapters':[{'id':uid(),'position':0,'title':'Определение','level':1,'start_segment_id':segments[0]['id']}]}
    forbidden=SimpleNamespace(extract=lambda *a:pytest.fail('FFmpeg must not run'),transcribe=lambda *a:pytest.fail('ASR must not run'))
    worker=Worker(cfg,repository=JobRepository(cfg),audio=forbidden,transcriber=forbidden,
                  summarizer=SimpleNamespace(summarize=summary),heartbeat_interval=.1)
    assert worker.run_once()
    card=client.get(f"/api/v1/lectures/{ids['lecture']}").json()['data']
    assert card['status']=='ready' and card['availability']['search']
    assert client.get(f"/api/v1/lectures/{ids['lecture']}/summary").json()['data']['text'].startswith('## Определение')
    with db.cursor() as cur:
        cur.execute('SELECT id FROM search_chunks WHERE lecture_id=%s',(ids['lecture'],))
        assert cur.fetchone()['id']==ids['chunk']


def test_summary_cooldown_is_enforced_on_server_and_preserves_data(client, db):
    ids=seed(db)
    with db.cursor() as cur:
        cur.execute('UPDATE lectures SET summary_generated_at=UTC_TIMESTAMP(3) WHERE id=%s',(ids['lecture'],))
    response=client.post(f"/api/v1/lectures/{ids['lecture']}/summary/regenerate")
    error(response,429,'SUMMARY_REGENERATION_COOLDOWN')
    assert response.json()['error']['details']['available_at'].endswith('Z')
    card=client.get(f"/api/v1/lectures/{ids['lecture']}").json()['data']
    assert not card['processing']['can_regenerate_summary']
    assert card['availability']['summary']
    assert client.get(f"/api/v1/lectures/{ids['lecture']}/summary").status_code==200
    with db.cursor() as cur:
        cur.execute('UPDATE lectures SET summary_generated_at=UTC_TIMESTAMP(3)-INTERVAL 24 HOUR-INTERVAL 1 SECOND WHERE id=%s',(ids['lecture'],))
    assert client.get(f"/api/v1/lectures/{ids['lecture']}").json()['data']['processing']['can_regenerate_summary']
    def request(): return client.post(f"/api/v1/lectures/{ids['lecture']}/summary/regenerate").status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _:request(),range(2)))==[202,409]
