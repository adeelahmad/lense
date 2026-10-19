"""Lens's model calls to an Amazon SageMaker endpoint (llm.py): the request is InvokeEndpoint as SageMaker's runtime API
defines it (botocore's own model of the API, so the path, method and signing name are SageMaker's, not a guess), signed
with AWS Signature Version 4, and the secret key never leaves Lens."""

from __future__ import annotations

import io
import itertools
import json

import pytest

from app.domain import llm
from tests import aws_eventstream as eventstream

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
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

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


class Parts:
    """InvokeEndpointWithResponseStream's answer: AWS's event stream, read a message at a time as it arrives."""

    headers = {"Content-Type": "application/vnd.amazon.eventstream"}

    def __init__(self, messages):
        self.messages, self.reads, self.closed = list(messages), 0, False

    def read(self, n=-1):
        self.reads += 1
        return self.messages.pop(0) if self.messages else b""

    def close(self):
        self.closed = True


def sse(*pieces, stop="stop"):
    chunks = [{"choices": [{"delta": {"content": p}, "finish_reason": None}]} for p in pieces]
    chunks.append({"choices": [{"delta": {}, "finish_reason": stop}]})
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


def test_a_streamed_answer_comes_as_the_container_writes_it(cfg, sent):
    """InvokeEndpointWithResponseStream: the container's server-sent events, in PayloadPart events that may split a
    line anywhere."""
    calls, answers = sent
    body = sse("Hel", "lo", " there")
    cut = [0, 7, 40, 41, len(body) // 2, len(body) - 3, len(body)]
    parts = Parts(eventstream.event("PayloadPart", body[x:y], "application/octet-stream") for x, y in itertools.pairwise(cut))
    answers.append(parts)
    pieces = llm.stream_chat(cfg, [{"role": "user", "content": "hi"}])
    assert next(pieces) == "Hel" and parts.messages  # the first words come before the rest is read
    assert "Hel" + "".join(pieces) == "Hello there" and parts.closed

    (req,) = calls
    op = RUNTIME.operation_model("InvokeEndpointWithResponseStream")
    assert req.get_method() == op.http["method"]
    assert req.full_url == URL + op.http["requestUri"].replace("/endpoints/{EndpointName}", "")
    headers = {k.lower(): v for k, v in req.header_items()}
    accept = op.input_shape.members["Accept"].serialization["name"].lower()
    assert headers[accept] == "text/event-stream" and json.loads(req.data)["stream"] is True
    assert headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE7/")
    assert set(op.output_shape.members["Body"].members) >= {"PayloadPart", "ModelStreamError"}  # what Parts sends


def test_a_model_error_mid_stream_says_so(cfg, sent):
    _, answers = sent
    first = eventstream.event("PayloadPart", sse("Hel")[:60], "application/octet-stream")
    error = eventstream.exception("ModelStreamError", {"Message": "the model stopped", "ErrorCode": "500"})
    answers.append(Parts([first, error]))
    with pytest.raises(llm.LLMError, match="stopped mid-answer: ModelStreamError: the model stopped"):
        "".join(llm.stream_chat(cfg, [{"role": "user", "content": "hi"}]))


def test_keys_that_may_only_invoke_get_the_answer_whole(cfg, sent):
    """An IAM policy made before answers streamed allows InvokeEndpoint only: the stream's 403 asks again for it whole."""
    calls, answers = sent
    denied = llm.urllib.error.HTTPError(URL, 403, "Forbidden", {}, io.BytesIO(b'{"message": "not authorized"}'))
    answers.extend([denied, Answer(sse("Hello"), "text/event-stream")])
    assert "".join(llm.stream_chat(cfg, [{"role": "user", "content": "hi"}])) == "Hello"
    assert [r.full_url for r in calls] == [URL + "/invocations-response-stream", URL + "/invocations"]

    answers.extend([denied, denied])  # a key refused both ways says so
    with pytest.raises(llm.LLMError, match="403 from the LLM server"):
        "".join(llm.stream_chat(cfg, [{"role": "user", "content": "hi"}]))


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
