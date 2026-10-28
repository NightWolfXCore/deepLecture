"""Search contract with explicit deterministic encoder and repository doubles."""
from datetime import datetime
from uuid import uuid4
import numpy as np
import pytest
from fastapi.testclient import TestClient
from app.api import create_app
from app.config import Settings
from app.pipeline.errors import PipelineError


LECTURE='11111111-1111-4111-8111-111111111111'


class Encoder:
    model_name='intfloat/multilingual-e5-small'
    revision='test-fixture-v1'
    def load(self): pass
    def encode_query(self, text):
        v=np.zeros(384,dtype='<f4'); v[0]=1
        return v


class Repository:
    def __init__(self):
        self.lecture={'index_ready_at':datetime.now()}
        self.rows=[dict(chunk_id='b',start_ms=2000,embedding=self.vector(0),dimensions=384,model_name=Encoder.model_name,model_revision=Encoder.revision),
                   dict(chunk_id='a',start_ms=1000,embedding=self.vector(0),dimensions=384,model_name=Encoder.model_name,model_revision=Encoder.revision),
                   dict(chunk_id='c',start_ms=0,embedding=self.vector(1),dimensions=384,model_name=Encoder.model_name,model_revision=Encoder.revision)]
    @staticmethod
    def vector(index):
        v=np.zeros(384,dtype='<f4');v[index]=1;return v.tobytes()
    def ping(self): pass
    def index(self,id): return self.lecture,self.rows


def test_search_ranks_similarity_then_time_and_honors_limit():
    with TestClient(create_app(Settings(),Repository(),Encoder())) as client:
        assert client.get('/internal/v1/ready').status_code==200
        result=client.post('/internal/v1/search',json={'lecture_id':LECTURE,'query':'  вопрос  ','limit':2})
        assert result.status_code==200
        assert result.json()['data']==[{'chunk_id':'a','score':1.0},{'chunk_id':'b','score':1.0}]


@pytest.mark.parametrize('payload', [
    {'lecture_id':LECTURE,'query':' '},
    {'lecture_id':'bad','query':'text'},
    {'lecture_id':LECTURE,'query':'text','limit':True},
    {'lecture_id':LECTURE,'query':'text','limit':21},
    {'lecture_id':LECTURE,'query':'text','extra':'unexpected'},
])
def test_search_validation(payload):
    with TestClient(create_app(Settings(),Repository(),Encoder())) as client:
        r=client.post('/internal/v1/search',json=payload)
        assert r.status_code==422
        assert r.json()['error']['code']=='VALIDATION_ERROR'
        assert r.json()['request_id']


@pytest.mark.parametrize('mutation', ['revision','dimension','length','nan','nonunit'])
def test_corrupt_or_incompatible_index_is_rejected(mutation):
    repo=Repository()
    if mutation=='revision': repo.rows[0]['model_revision']='other'
    if mutation=='dimension': repo.rows[0]['dimensions']=10
    if mutation=='length': repo.rows[0]['embedding']=b'short'
    if mutation=='nan': repo.rows[0]['embedding']=np.full(384,np.nan,dtype='<f4').tobytes()
    if mutation=='nonunit': repo.rows[0]['embedding']=np.ones(384,dtype='<f4').tobytes()
    with TestClient(create_app(Settings(),repo,Encoder())) as client:
        r=client.post('/internal/v1/search',json={'lecture_id':LECTURE,'query':'текст'})
        assert r.status_code==503
        assert r.json()['error']['code']=='INDEX_INVALID'


def test_absent_lecture_and_not_ready_are_distinct():
    repo=Repository()
    with TestClient(create_app(Settings(),repo,Encoder())) as client:
        repo.lecture=None
        assert client.post('/internal/v1/search',json={'lecture_id':LECTURE,'query':'текст'}).status_code==404
        repo.lecture={'index_ready_at':None}
        assert client.post('/internal/v1/search',json={'lecture_id':LECTURE,'query':'текст'}).status_code==409


def test_failed_model_startup_stays_unready_without_fake_fallback():
    class BrokenEncoder(Encoder):
        def load(self): raise PipelineError('EMBEDDING_UNAVAILABLE','not ready')
    with TestClient(create_app(Settings(),Repository(),BrokenEncoder())) as client:
        assert client.get('/internal/v1/ready').status_code==503
        assert client.post('/internal/v1/search',json={'lecture_id':LECTURE,'query':'текст'}).status_code==503


def test_invalid_json_returns_400():
    with TestClient(create_app(Settings(),Repository(),Encoder())) as client:
        r=client.post('/internal/v1/search',content='{',headers={'Content-Type':'application/json'})
        assert r.status_code==400
        assert r.json()['error']['code']=='INVALID_JSON'


def test_model_recovers_after_transient_startup_failure():
    import time
    class RecoveringEncoder(Encoder):
        attempts = 0
        def load(self):
            self.attempts += 1
            if self.attempts == 1:
                raise PipelineError('EMBEDDING_UNAVAILABLE', 'temporary failure')
    encoder = RecoveringEncoder()
    with TestClient(create_app(Settings(), Repository(), encoder, retry_interval=0.01)) as client:
        deadline = time.monotonic() + 2
        while client.get('/internal/v1/ready').status_code != 200:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert encoder.attempts == 2
        assert client.post('/internal/v1/search', json={'lecture_id': LECTURE, 'query': 'текст'}).status_code == 200
