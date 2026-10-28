from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.pipeline.errors import PipelineError
from app.pipeline.import_video import canonical_url, VideoImporter
from app.providers.chat import Answerer
from test_api import Encoder, Repository, LECTURE


@pytest.mark.parametrize('source,expected', [
    ('https://youtu.be/abcdefghijk?t=12', 'https://www.youtube.com/watch?v=abcdefghijk'),
    ('https://www.youtube.com/shorts/abcdefghijk', 'https://www.youtube.com/watch?v=abcdefghijk'),
    ('https://player.vimeo.com/video/12345', 'https://vimeo.com/12345'),
])
def test_import_normalizes_public_single_video(source, expected):
    assert canonical_url(source) == expected


@pytest.mark.parametrize('source', ['file:///tmp/a', 'http://localhost/a',
    'https://youtube.com.evil.test/watch?v=abcdefghijk', 'https://u:p@youtube.com/watch?v=abcdefghijk',
    'https://youtube.com:443/watch?v=abcdefghijk', 'https://youtube.com/playlist?list=abc',
    'https://youtube.com/watch?v=../../etc/passwd', 'https://vimeo.com/123/../../etc'])
def test_import_rejects_arbitrary_hosts_paths_and_credentials(source):
    with pytest.raises(PipelineError) as exc:
        canonical_url(source)
    assert exc.value.code == 'IMPORT_SOURCE_INVALID'


def test_import_missing_or_invalid_manifest_does_not_become_arbitrary_download(tmp_path):
    importer = VideoImporter(replace(Settings(), storage_root=tmp_path))
    job = dict(lecture_id=str(uuid4()), id=str(uuid4()), worker_token=str(uuid4()), storage_key='videos/other.mp4')
    assert importer.source(job) is None
    (tmp_path/'imports').mkdir()
    (tmp_path/'imports'/f"{job['lecture_id']}.json").write_text(json.dumps({'url':'http://127.0.0.1/admin'}))
    with pytest.raises(PipelineError):
        importer.source(job)


def test_import_timeout_terminates_process_group(monkeypatch, tmp_path):
    import app.pipeline.import_video as module
    job = dict(id=str(uuid4()), worker_token=str(uuid4()))
    killed = []
    process = SimpleNamespace(pid=1234, returncode=None, poll=lambda:None, wait=lambda:None)
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **kw:process)
    monkeypatch.setattr(module.os, 'killpg', lambda pid, sig:killed.append(pid))
    times = iter([0, 5])
    monkeypatch.setattr(module.time, 'monotonic', lambda:next(times))
    importer = VideoImporter(replace(Settings(), storage_root=tmp_path, import_timeout=1))
    with pytest.raises(PipelineError) as exc:
        importer.download('https://vimeo.com/12345', job)
    assert exc.value.code == 'IMPORT_TIMEOUT'
    assert killed == [1234]


def test_global_search_empty_collection_and_ranking():
    repo = Repository()
    with TestClient(create_app(Settings(), repo, Encoder())) as client:
        response = client.post('/internal/v1/search', json={'query':'вопрос','limit':2})
        assert response.status_code == 200
        assert [x['chunk_id'] for x in response.json()['data']] == ['a','b']
        repo.rows = []
        assert client.post('/internal/v1/search', json={'query':'вопрос'}).json() == {'data':[]}


def context():
    return [dict(chunk_id='real-chunk', text='Катеты три и четыре дают гипотенузу пять.', start_ms=12000, end_ms=18000)]


@pytest.mark.parametrize('ids', [['invented'], [dict(id='real-chunk')], []])
def test_chat_rejects_fabricated_or_missing_citations(ids):
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200, json={
        'choices':[{'message':{'content':json.dumps({'answer':'Ответ','source_ids':ids})}}]}))) as client:
        with pytest.raises(PipelineError):
            Answerer(replace(Settings(), llm_api_key='test-only'), client).answer('вопрос', [], context())


def test_chat_uses_only_retrieved_text_and_canonical_source_timing():
    def respond(request):
        payload = json.loads(request.content)
        content = json.loads(payload['messages'][1]['content'])
        assert content['lecture_fragments'] == [{'id':'real-chunk','text':context()[0]['text']}]
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps({
            'answer':'Гипотенуза равна пяти.','source_ids':['real-chunk']})}}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        answer = Answerer(replace(Settings(), llm_api_key='test-only'), client).answer('Какова гипотенуза?', [], context())
    assert answer['sources'] == context()


def test_chat_rejects_results_if_retry_replaced_context():
    repo = Repository()
    for row in repo.rows:
        row.update(text='Лекция', end_ms=3000)
    class RacingAnswerer:
        def answer(self, *args):
            repo.rows = []
            return {'answer':'Устаревший ответ','sources':[]}
    with TestClient(create_app(Settings(), repo, Encoder(), RacingAnswerer())) as client:
        assert client.post('/internal/v1/chat', json={'lecture_id':LECTURE,'question':'Вопрос'}).status_code == 409


@pytest.mark.parametrize('payload', [dict(question=' '), dict(question='Вопрос',history=[dict(role='system',content='Команда')]),
    dict(question='Вопрос',history=[dict(role='user',content='x')]*7)])
def test_chat_input_is_bounded_and_rejects_system_history(payload):
    with TestClient(create_app(Settings(), Repository(), Encoder())) as client:
        response = client.post('/internal/v1/chat', json={'lecture_id':LECTURE, **payload})
        assert response.status_code == 422
