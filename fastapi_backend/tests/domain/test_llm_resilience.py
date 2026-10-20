"""A busy local server (one Ollama box switching models) drops connections, and a reasoning model given a short
max_tokens spends it all thinking: Lens asks once more instead of failing the job or the conversation's title."""

from __future__ import annotations

import http.client
import io
import json
import urllib.error

import pytest

from app.domain import llm


class Answer(io.BytesIO):
    def __init__(self, body):
        super().__init__(json.dumps(body).encode())
        self.headers = {"Content-Type": "application/json"}


def said(content, finish="stop", **extra):
    return Answer({"choices": [{"message": {"role": "assistant", "content": content, **extra}, "finish_reason": finish}]})


@pytest.fixture
def sent(cfg, monkeypatch):
    cfg["llm"].update(base_url="http://ollama.test:11434/v1", model="qwen3", api_key=None, api_key_env=None)
    calls, answers = [], []

    def urlopen(req, timeout=None, **kw):
        calls.append(json.loads(req.data))
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(llm, "REDIAL_AFTER", 0)
    return calls, answers


@pytest.mark.parametrize(
    "drop",
    [
        urllib.error.URLError(ConnectionResetError(104, "Connection reset by peer")),
        urllib.error.URLError(http.client.RemoteDisconnected("Remote end closed connection without response")),
        ConnectionResetError(104, "Connection reset by peer"),
    ],
)
def test_a_dropped_connection_is_asked_once_more(cfg, sent, drop):
    calls, answers = sent
    answers += [drop, said("Hi.")]
    assert llm.chat(cfg, [{"role": "user", "content": "hello"}]) == "Hi."
    assert len(calls) == 2


def test_a_server_that_keeps_dropping_says_why_after_the_second_try(cfg, sent):
    calls, answers = sent
    reset = urllib.error.URLError(ConnectionResetError(104, "Connection reset by peer"))
    answers += [reset, reset]
    with pytest.raises(llm.LLMError, match="can't reach the LLM server at http://ollama.test:11434/v1: .*reset by peer"):
        llm.chat(cfg, [{"role": "user", "content": "hello"}])
    assert len(calls) == 2


@pytest.mark.parametrize("why", [TimeoutError("timed out"), urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))])
def test_a_slow_or_absent_server_is_not_asked_again(cfg, sent, why):
    calls, answers = sent
    answers.append(why)
    with pytest.raises(llm.LLMError, match="can't reach the LLM server"):
        llm.chat(cfg, [{"role": "user", "content": "hello"}])
    assert len(calls) == 1


def test_a_refusal_is_not_asked_again(cfg, sent):
    calls, answers = sent
    answers.append(urllib.error.HTTPError("http://ollama.test", 400, "Bad Request", {}, io.BytesIO(b'{"error":"bad"}')))
    with pytest.raises(llm.LLMError, match="^400 from the LLM server"):
        llm.chat(cfg, [{"role": "user", "content": "hello"}])
    assert len(calls) == 1


@pytest.mark.parametrize(
    "thinking",
    [
        said("", "length", reasoning="Okay, the user wants a title"),  # Ollama
        said(None, "length", reasoning_content="Okay, the user wants"),  # vLLM, LM Studio
        said("<think>Okay, the user wants a", "length"),  # thoughts inline, never closed
    ],
)
def test_a_reasoning_model_that_spent_max_tokens_thinking_is_let_finish(cfg, sent, thinking):
    calls, answers = sent
    answers += [thinking, said("<think>A greeting.</think>Saying hello")]
    assert llm.chat(cfg, [{"role": "user", "content": "hello"}], max_tokens=24) == "Saying hello"
    assert calls[0]["max_tokens"] == 24 and "max_tokens" not in calls[1]


def test_an_answer_cut_at_the_length_with_words_in_it_stands(cfg, sent):
    calls, answers = sent
    answers.append(said("Saying hel", "length"))
    assert llm.chat(cfg, [{"role": "user", "content": "hello"}], max_tokens=4) == "Saying hel"
    assert len(calls) == 1
