import json
from dataclasses import replace
import httpx
import pytest
from app.config import Settings
from app.pipeline.errors import PipelineError
from app.providers.llm import Summarizer


def transport(handler):
    return Summarizer(replace(Settings(), llm_api_key='test-only'),
                      client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)


@pytest.mark.parametrize('status,code', [(404,'LLM_MODEL_NOT_FOUND'),(401,'LLM_AUTH_FAILED'),
    (403,'LLM_AUTH_FAILED'),(429,'LLM_RATE_LIMITED'),(500,'LLM_HTTP_ERROR')])
def test_http_failures_have_safe_specific_codes(status,code):
    with pytest.raises(PipelineError) as exc:
        transport(lambda r:httpx.Response(status,json={'error':'provider-secret'}))._completion({})
    assert exc.value.code == code
    assert 'provider-secret' not in exc.value.message


@pytest.mark.parametrize('reason,content,code', [('length','{}','LLM_RESPONSE_TRUNCATED'),
    ('stop','broken JSON','LLM_RESPONSE_INVALID'), ('stop',None,'LLM_RESPONSE_INVALID')])
def test_invalid_or_truncated_content_is_not_published(reason,content,code):
    with pytest.raises(PipelineError) as exc:
        transport(lambda r:httpx.Response(200,json={'choices':[{'finish_reason':reason,'message':{'content':content}}]}))._completion({})
    assert exc.value.code == code


def test_fenced_json_is_normalized_without_inventing_content():
    result = transport(lambda r:httpx.Response(200,json={'choices':[{'message':{'content':'```json\n{"text":"текст","chapters":[]}\n```'}}]}))._completion({})
    assert result == {'text':'текст','chapters':[]}


@pytest.mark.parametrize('exception,code',[(httpx.ReadTimeout,'LLM_TIMEOUT'),(httpx.ConnectError,'LLM_UNAVAILABLE')])
def test_network_failures_are_distinguishable(exception,code):
    def fail(request): raise exception('provider-secret',request=request)
    with pytest.raises(PipelineError) as exc: transport(fail)._completion({})
    assert exc.value.code == code
    assert 'provider-secret' not in exc.value.message
