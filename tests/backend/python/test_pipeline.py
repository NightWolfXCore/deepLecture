"""Provider doubles exercise validation; FFmpeg check below uses real tools."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import subprocess
import shutil
import pytest
import httpx
from app.config import Settings
from app.pipeline.audio import AudioProcessor
from app.pipeline.chunk import build_chunks
from app.pipeline.errors import PipelineError
from app.pipeline.transcribe import Transcriber
from app.providers.llm import Summarizer


class Tokenizer:
    model_max_length = 512
    def encode(self, text, add_special_tokens=False):
        return text.split()
    def decode(self, tokens, skip_special_tokens=True):
        return " ".join(tokens)
    def num_special_tokens_to_add(self, pair=False):
        return 2


def segment(id="first", text="Квадрат гипотенузы равен сумме квадратов катетов.", start=0, end=1000):
    return dict(id=id, position=0, text=text, start_ms=start, end_ms=end)


def test_chunk_split_never_exceeds_token_budget_and_preserves_source():
    source = [segment(text=" ".join("слово" + str(i) for i in range(1000)))]
    chunks = build_chunks(source, Tokenizer(), max_tokens=80, overlap_tokens=10)
    assert len(chunks) > 1
    assert len({x['id'] for x in chunks}) == len(chunks)
    for chunk in chunks:
        assert len(Tokenizer().encode("passage: " + chunk['text'])) + 2 <= 80
        assert chunk['first_segment_id'] == chunk['last_segment_id'] == 'first'
        assert chunk['start_ms'] == 0 and chunk['end_ms'] == 1000
    assert set(" ".join(x['text'] for x in chunks).split()) == set(source[0]['text'].split())


def test_chunk_short_segments_overlap_and_make_forward_progress():
    segments = [segment(str(i), f"Тема номер {i}", i * 1000, (i+1)*1000) for i in range(30)]
    chunks = build_chunks(segments, Tokenizer(), max_tokens=32, overlap_tokens=8)
    assert 1 < len(chunks) <= 30
    assert chunks[-1]['last_segment_id'] == '29'
    assert int(chunks[1]['first_segment_id']) <= int(chunks[0]['last_segment_id'])


def test_empty_transcript_not_indexed():
    with pytest.raises(PipelineError, match='пуст'):
        build_chunks([], Tokenizer())


def test_whisper_clamps_times_and_filters_blank_or_reversed_segments():
    transcriber = Transcriber(Settings())
    source = [SimpleNamespace(start=-1, end=.3, text=' начало '),
              SimpleNamespace(start=.4, end=2, text=' конец '),
              SimpleNamespace(start=2, end=3, text='outside'),
              SimpleNamespace(start=.2, end=.3, text=' ')]
    transcriber.model = SimpleNamespace(transcribe=lambda *args, **kwargs: (source, None))
    result = transcriber.transcribe(Path('unused.wav'), 1000)
    assert [(x['start_ms'], x['end_ms']) for x in result] == [(0,300),(400,1000)]
    assert [x['position'] for x in result] == [0,1]


def test_config_blank_revision_uses_default(monkeypatch):
    monkeypatch.setenv('EMBEDDING_REVISION','')
    monkeypatch.setenv('WHISPER_REVISION','')
    assert Settings.from_env().embedding_revision == 'main'
    assert Settings.from_env().whisper_revision == 'main'


def test_whisper_short_model_alias_resolves_to_official_repository():
    assert Transcriber(replace(Settings(), whisper_model="small")).model_name == "Systran/faster-whisper-small"
    assert Transcriber(replace(Settings(), whisper_model="org/custom")).model_name == "org/custom"


def response(data):
    return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps(data)}}]})


def llm_settings():
    return replace(Settings(), llm_api_key='test-only', llm_model='test-model')


def test_llm_validates_anchors_and_keeps_them_in_order():
    chapters = [dict(title='Следующий раздел',level=1,start_segment_id='second'),
                dict(title='Начало',level=2,start_segment_id='first')]
    client=httpx.Client(transport=httpx.MockTransport(lambda request: response(dict(text='Конспект',chapters=chapters))))
    result=Summarizer(llm_settings(), client=client).summarize([segment(), segment('second',start=1000,end=2000)])
    assert [x['start_segment_id'] for x in result['chapters']] == ['first','second']
    assert result['chapters'][0]['level'] == 1
    assert result['provider'] == 'openai-compatible'


@pytest.mark.parametrize('anchor', ['hallucinated-id', {'unhashable':'id'}, ['invalid-list']])
def test_llm_rejects_hallucinated_or_invalid_anchor(anchor):
    client=httpx.Client(transport=httpx.MockTransport(lambda request: response(dict(text='Summary',chapters=[dict(title='A',level=1,start_segment_id=anchor)]))))
    with pytest.raises(PipelineError) as exc:
        Summarizer(llm_settings(), client=client).summarize([segment()])
    assert exc.value.code == 'SUMMARY_INVALID'


def test_llm_retries_transient_errors_only_and_is_bounded():
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(429,json={})
    with pytest.raises(PipelineError):
        Summarizer(llm_settings(),client=httpx.Client(transport=httpx.MockTransport(handler)),sleep=lambda _:None).summarize([segment()])
    assert len(calls)==3


def test_llm_no_key_is_explicit_failure():
    with pytest.raises(PipelineError) as exc:
        Summarizer(Settings()).summarize([segment()])
    assert exc.value.code=='LLM_NOT_CONFIGURED'


def test_real_ffmpeg_extracts_mono_16khz_and_rejects_no_audio(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg binaries not installed')
    video=tmp_path/'input.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=black:s=64x64:d=1',
                    '-f','lavfi','-i','sine=frequency=400:duration=1','-c:v','mpeg4','-c:a','aac','-shortest',str(video)], check=True)
    audio=tmp_path/'audio.wav'
    duration=AudioProcessor(Settings()).extract(video,audio)
    assert 900<=duration<=1100
    info=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(audio)],capture_output=True,check=True).stdout)
    assert info['streams'][0]['channels']==1
    assert info['streams'][0]['sample_rate']=='16000'
    with pytest.raises(PipelineError) as exc:
        AudioProcessor(replace(Settings(), max_duration_ms=100)).extract(video,tmp_path/'too-long.wav')
    assert exc.value.code=='VIDEO_TOO_LONG'
    silent=tmp_path/'silent.mp4'
    subprocess.run(['ffmpeg','-v','error','-i',str(video),'-an','-c:v','copy',str(silent)],check=True)
    with pytest.raises(PipelineError) as exc:
        AudioProcessor(Settings()).extract(silent,tmp_path/'silent.wav')
    assert exc.value.code=='NO_AUDIO_STREAM'
