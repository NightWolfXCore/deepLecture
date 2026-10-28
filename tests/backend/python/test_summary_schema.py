"""Schema and bounded format-repair regression; provider responses are explicit doubles."""
import json
from dataclasses import replace
import httpx
import pytest
from app.config import Settings
from app.pipeline.errors import PipelineError
from app.providers.llm import Summarizer

SEGMENTS = [{'id':'source-segment','text':'Определение многочлена и пример из лекции.'}]
VALID = {'text':'## Определение\nУчебный конспект по материалу.',
         'chapters':[{'title':'Определение','level':1,'start_segment_id':'source-segment'}]}


def result(content):
    return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(content)}}]})


def model(handler, strict=True):
    return Summarizer(replace(Settings(),llm_api_key='test-only',llm_structured_output=strict),
                      httpx.Client(transport=httpx.MockTransport(handler)),sleep=lambda _:None)


def test_structured_summary_requires_fields_and_only_real_anchors():
    def handler(request):
        payload=json.loads(request.content)
        fmt=payload['response_format']
        assert fmt['type']=='json_schema' and fmt['json_schema']['strict'] is True
        schema=fmt['json_schema']['schema']
        assert schema['required']==['text','chapters']
        assert schema['properties']['text']['type']=='string'
        assert schema['properties']['chapters']['minItems']==1
        assert schema['properties']['chapters']['items']['properties']['start_segment_id']['enum']==['source-segment']
        return result(VALID)
    assert model(handler).summarize(SEGMENTS)['chapters'][0]['start_segment_id']=='source-segment'


@pytest.mark.parametrize('bad',[{'summary':'wrong field name'}, {'text':['not a string'],'chapters':[]},
    {'text':'text','chapters':{}}, {'text':'','chapters':[]},
    {'text':'text','chapters':[{'title':'A','level':1,'start_segment_id':'invented'}]}])
def test_invalid_response_is_retried_from_original_source_once(bad):
    calls=[]
    def handler(request):
        payload=json.loads(request.content); calls.append(payload)
        assert json.loads(payload['messages'][1]['content'])=={'lecture_segments':SEGMENTS}
        return result(bad if len(calls)==1 else VALID)
    assert model(handler).summarize(SEGMENTS)['text']==VALID['text']
    assert len(calls)==2 and 'Предыдущий ответ' in calls[1]['messages'][0]['content']


def test_persistent_shape_failure_is_not_published_and_does_not_leak(caplog):
    calls=[]
    def handler(request):
        calls.append(request)
        return result({'text':['private-provider-text'],'chapters':'bad'})
    with pytest.raises(PipelineError) as exc:
        model(handler).summarize(SEGMENTS)
    assert exc.value.code=='LLM_RESPONSE_INVALID' and len(calls)==2
    assert 'private-provider-text' not in caplog.text


def test_schema_on_merge_has_no_fabricated_chapter_anchors():
    def handler(request):
        schema=json.loads(request.content)['response_format']['json_schema']['schema']
        assert schema['properties']['chapters']['maxItems']==0
        assert schema['properties']['chapters']['minItems']==0
        return result({'text':'Объединённые определения и примеры','chapters':[]})
    assert model(handler)._request({'partial_summaries':['Тема 1','Тема 2']})['chapters']==[]


def test_external_legacy_mode_keeps_json_object():
    def handler(request):
        assert json.loads(request.content)['response_format']=={'type':'json_object'}
        return result(VALID)
    assert model(handler,strict=False).summarize(SEGMENTS)['text']==VALID['text']


def test_http_error_is_not_a_reason_for_format_repair():
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(404,json={'error':'not found'})
    with pytest.raises(PipelineError) as exc: model(handler).summarize(SEGMENTS)
    assert exc.value.code=='LLM_MODEL_NOT_FOUND' and len(calls)==1


def test_structured_setting_is_explicit(monkeypatch):
    monkeypatch.setenv('LLM_STRUCTURED_OUTPUT','true')
    assert Settings.from_env().llm_structured_output is True
