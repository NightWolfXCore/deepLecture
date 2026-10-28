"""Configuration/transport checks with doubles; these do not execute Qwen or E5."""
from dataclasses import replace
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.pipeline.embed import Embedder
from app.pipeline.errors import PipelineError
from app.pipeline.transcribe import Transcriber
from app.providers.llm import Summarizer


SEGMENTS = [dict(id="anchor", text="Теорема Пифагора.")]


@pytest.mark.parametrize("mode,key,expected", [
    ("none", "", None),
    ("none", "unused-value", None),
    ("bearer", "test-only", "Bearer test-only"),
])
def test_local_openai_protocol_and_explicit_auth(mode, key, expected):
    def handler(request):
        assert str(request.url) == "http://ollama:11434/v1/chat/completions"
        assert request.headers.get("Authorization") == expected
        payload = json.loads(request.content)
        assert payload["model"] == "Qwen3-4B-Q4_K_M"
        assert payload["reasoning_effort"] == "none"
        assert payload["response_format"] == {"type": "json_object"}
        assert json.loads(payload["messages"][1]["content"])["lecture_segments"] == SEGMENTS
        result = dict(text="Конспект", chapters=[dict(title="Теорема", level=1, start_segment_id="anchor")])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})
    settings = replace(Settings(), llm_base_url="http://ollama:11434/v1/", llm_model="Qwen3-4B-Q4_K_M",
                       llm_auth_mode=mode, llm_api_key=key, llm_allow_http=True, llm_reasoning_effort="none")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert Summarizer(settings, client).summarize(SEGMENTS)["chapters"][0]["start_segment_id"] == "anchor"


@pytest.mark.parametrize("overrides", [
    dict(llm_auth_mode="none"),  # HTTP still requires a separate opt-in.
    dict(llm_allow_http=True, llm_auth_mode="bearer", llm_api_key=""),
    dict(llm_allow_http=True, llm_auth_mode="bearer", llm_api_key="   "),
    dict(llm_allow_http=True, llm_auth_mode="typo"),
    dict(llm_allow_http=True, llm_auth_mode="none", llm_base_url="http://user:pass@ollama:11434/v1"),
    dict(llm_allow_http=True, llm_auth_mode="none", llm_base_url="http://ollama:11434/v1?key=secret"),
])
def test_invalid_llm_config_never_sends_a_request(overrides):
    def handler(request):
        pytest.fail("Invalid settings reached the network")
    settings = replace(Settings(), llm_base_url="http://ollama:11434/v1")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PipelineError) as exc:
            Summarizer(replace(settings, **overrides), client).summarize(SEGMENTS)
    assert exc.value.code == "LLM_NOT_CONFIGURED"


def test_local_config_from_environment(monkeypatch):
    for name, value in dict(LLM_BASE_URL="http://ollama:11434/v1", LLM_API_KEY="", LLM_AUTH_MODE="none",
                            LLM_ALLOW_HTTP="true", LLM_TIMEOUT="300", MODELS_OFFLINE="true",
                            LLM_REASONING_EFFORT="none").items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    assert settings.llm_auth_mode == "none" and settings.llm_allow_http
    assert settings.llm_api_key == "" and settings.llm_timeout == 300
    assert settings.models_offline
    assert settings.llm_reasoning_effort == "none"


def test_external_default_does_not_send_optional_reasoning_parameter():
    def handler(request):
        assert "reasoning_effort" not in json.loads(request.content)
        result = dict(text="Конспект", chapters=[dict(title="Теорема", level=1, start_segment_id="anchor")])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = Summarizer(replace(Settings(), llm_api_key="test-only"), client).summarize(SEGMENTS)
    assert result["text"] == "Конспект"


@pytest.mark.parametrize("offline", [False, True])
def test_model_loaders_pass_cache_only_policy(monkeypatch, tmp_path, offline):
    calls = []
    snapshot = tmp_path / ("a" * 40)
    def download(name, **kwargs):
        calls.append((name, kwargs))
        return str(snapshot)
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=lambda *a, **kw: object()))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda _: None))
    model = SimpleNamespace(tokenizer=object(), get_sentence_embedding_dimension=lambda: 384)
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=lambda *a, **kw: model))
    settings = replace(Settings(), model_cache=tmp_path, models_offline=offline)
    embedder = Embedder(settings)
    embedder.load()
    Transcriber(settings).load()
    assert embedder.revision == "a" * 40
    assert len(calls) == 2
    assert all(kw["local_files_only"] is offline and kw["cache_dir"] == str(tmp_path) for _, kw in calls)


@pytest.mark.parametrize("loader,code", [(Embedder, "EMBEDDING_UNAVAILABLE"), (Transcriber, "ASR_UNAVAILABLE")])
def test_offline_cache_miss_fails_without_online_retry(monkeypatch, loader, code):
    calls = []
    def download(*a, **kwargs):
        calls.append(kwargs)
        raise FileNotFoundError("Not cached")
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=object))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda _: None))
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=object))
    with pytest.raises(PipelineError) as exc:
        loader(replace(Settings(), models_offline=True)).load()
    assert exc.value.code == code
    assert len(calls) == 1 and calls[0]["local_files_only"] is True
