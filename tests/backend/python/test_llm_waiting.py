"""Do not discard a slow CPU generation and silently enqueue it again."""
import json
from dataclasses import replace
import httpx
import pytest
from app.config import Settings
from app.pipeline.errors import PipelineError
from app.providers.llm import Summarizer


@pytest.mark.parametrize('failure,expected,count', [
    (httpx.ReadTimeout,'LLM_TIMEOUT',1),
    (httpx.WriteTimeout,'LLM_TIMEOUT',1),
    (httpx.ReadError,'LLM_UNAVAILABLE',1),
    (httpx.ConnectTimeout,'LLM_TIMEOUT',3),
    (httpx.ConnectError,'LLM_UNAVAILABLE',3),
])
def test_only_failures_before_connection_can_be_resubmitted(failure,expected,count):
    calls=[]
    def handler(request):
        calls.append(request)
        raise failure('private-provider-message',request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model=Summarizer(replace(Settings(),llm_api_key='test-only'),client,sleep=lambda _:None)
        with pytest.raises(PipelineError) as exc:
            model._request({'lecture_segments':[{'id':'segment','text':'fixture'}]})
    assert len(calls)==count and exc.value.code==expected


def test_server_compute_failure_is_not_repeated():
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(500,json={'error':'private-provider-message'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PipelineError):
            Summarizer(replace(Settings(),llm_api_key='test-only'),client,sleep=lambda _:None)._completion({})
    assert len(calls)==1


def test_generation_logs_timing_and_token_count_without_response_text(caplog):
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={
        'choices':[{'message':{'content':json.dumps({'text':'private-lecture-text','chapters':[]})}}],
        'usage':{'completion_tokens':24}}))) as client:
        with caplog.at_level('INFO'):
            Summarizer(replace(Settings(),llm_api_key='test-only'),client)._completion({})
    assert 'elapsed_s=' in caplog.text and 'completion_tokens=24' in caplog.text
    assert 'private-lecture-text' not in caplog.text
