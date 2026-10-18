"""Lens's model calls to an Amazon SageMaker endpoint (llm.py): the request is InvokeEndpoint as SageMaker's runtime API
defines it (botocore's own model of the API, so the path, method and signing name are SageMaker's, not a guess), signed
with AWS Signature Version 4, and the secret key never leaves Lens."""

from __future__ import annotations

import io
import json

import pytest

from app.domain import llm

session = pytest.importorskip("botocore.session")
RUNTIME = session.Session().get_service_model("sagemaker-runtime")

URL = "https://runtime.sagemaker.eu-west-1.amazonaws.com/endpoints/lens-models-4"
KEY = "AKIAEXAMPLE7:wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"


class Answer(io.BytesIO):
    def __init__(self, body, content_type="application/json"):
        super().__init__(body if isinstance(body, bytes) else json.dumps(body).encode())
        self.headers = {"Content-Type": content_type}


@pytest.fixture
def sent(cfg, monkeypatch):
    cfg["llm"].update(base_url=URL, model="qwen", api_key=KEY, api_key_env=None)
    calls, answers = [], []

    def urlopen(req, timeout=None):
        calls.append(req)
        return answers.pop(0)

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    return calls, answers


def test_a_chat_goes_to_invoke_endpoint_signed_and_never_carries_the_secret(cfg, sent):
    calls, answers = sent
    answers.append(Answer({"choices": [{"message": {"role": "assistant", "content": "Hi."}, "finish_reason": "stop"}]}))
    assert llm.chat(cfg, [{"role": "user", "content": "hello"}]) == "Hi."

    (req,) = calls
    op = RUNTIME.operation_model("InvokeEndpoint")
    assert req.get_method() == op.http["method"] == "POST"
    assert req.full_url == URL + op.http["requestUri"].replace("/endpoints/{EndpointName}", "")
    assert json.loads(req.data)["messages"] == [{"role": "user", "content": "hello"}]

    headers = {k.lower(): v for k, v in req.header_items()}
    scope = f"AKIAEXAMPLE7/{headers['x-amz-date'][:8]}/eu-west-1/{RUNTIME.signing_name}/aws4_request"
    assert headers["authorization"].startswith(f"AWS4-HMAC-SHA256 Credential={scope}, SignedHeaders=")
    assert "Signature=" in headers["authorization"]
    assert not any(KEY.split(":")[1] in v for v in headers.values())  # only a signature made with it


def test_a_streamed_answer_reads_as_one_from_any_server(cfg, sent):
    """InvokeEndpoint hands back the container's whole answer: a streamed one is still server-sent events."""
    calls, answers = sent
    chunks = [{"choices": [{"delta": {"content": p}, "finish_reason": None}]} for p in ("Hel", "lo")]
    chunks.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    answers.append(Answer(body.encode(), "text/event-stream"))
    assert "".join(llm.stream_chat(cfg, [{"role": "user", "content": "hi"}])) == "Hello"


def test_an_endpoint_serves_its_one_model_and_is_never_asked_for_a_list(cfg, sent):
    calls, _ = sent
    assert llm.list_models(cfg, ttl=0) == ["qwen"]
    assert llm.context_window(cfg, ttl=0) is None and calls == []


def test_a_key_that_isnt_an_aws_key_says_what_it_should_be(cfg, sent):
    cfg["llm"]["api_key"] = "hf_token"
    with pytest.raises(llm.LLMError, match="access key id"):
        llm.chat(cfg, [{"role": "user", "content": "hello"}])


def test_other_servers_still_get_a_bearer_token_at_chat_completions(cfg, sent):
    calls, answers = sent
    cfg["llm"].update(base_url="https://runtime.sagemaker.example.com/v1", api_key="sk-1")
    answers.append(Answer({"choices": [{"message": {"content": "ok"}}]}))
    llm.chat(cfg, [{"role": "user", "content": "hello"}])
    assert calls[0].full_url == "https://runtime.sagemaker.example.com/v1/chat/completions"
    assert calls[0].get_header("Authorization") == "Bearer sk-1"


def test_embeddings_and_speech_never_borrow_the_aws_key(cfg, sent):
    """Other OpenAI-compatible calls fall back to the LLM provider's server and key; a SageMaker endpoint has neither to
    lend: its key only signs."""
    from app.domain import semantic, speech

    calls, _ = sent
    assert semantic.endpoint(cfg)[:2] == (None, None) and not semantic.configured(cfg)
    cfg["voice"] = {**(cfg.get("voice") or {}), "tts_model": "tts-1"}
    with pytest.raises(speech.ProviderError, match="a server of its own"):
        speech._speak_openai(cfg, "hello", "alloy")
    cfg["embeddings"] = {**(cfg.get("embeddings") or {}), "base_url": "http://ollama:11434/v1"}
    assert semantic.endpoint(cfg)[:2] == ("http://ollama:11434/v1", None) and calls == []
