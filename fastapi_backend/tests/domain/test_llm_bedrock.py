"""Lens's model calls to Amazon Bedrock (llm.py, bedrock.py): each request is Converse as Bedrock's runtime API
defines it, and each answer one Bedrock could give, both held to botocore's own model of the API by its Stubber (a
parameter or field the API doesn't have fails the test). The answers come back to Lens as an OpenAI-compatible
server's."""

from __future__ import annotations

import base64
import json

import pytest

from app.domain import analyze, llm

pytest.importorskip("botocore.session")
from botocore.stub import ANY, Stubber  # noqa: E402 - after the skip

URL = "https://bedrock-runtime.eu-west-1.amazonaws.com"
MODEL = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
KEY = "AKIAEXAMPLE7:wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
META = {"ResponseMetadata": {"HTTPStatusCode": 200}}


def said(text, stop="end_turn", tokens=(12, 3)):
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": stop,
        "usage": {"inputTokens": tokens[0], "outputTokens": tokens[1], "totalTokens": sum(tokens)},
        "metrics": {"latencyMs": 420},
    }


@pytest.fixture
def stub(cfg):
    cfg["llm"].update(base_url=URL, model=MODEL, api_key=KEY, api_key_env=None)
    llm._CLIENTS.clear()
    s = Stubber(llm.bedrock_client(cfg, "eu-west-1"))
    s.activate()
    yield s
    s.assert_no_pending_responses()
    llm._CLIENTS.clear()


def test_a_chat_is_a_converse_call_and_reads_as_an_openai_reply(cfg, stub):
    stub.add_response(
        "converse",
        said("Hello from Bedrock."),
        {
            "modelId": MODEL,
            "system": [{"text": "Be brief."}],
            "messages": [{"role": "user", "content": [{"text": "hi"}]}],
            "inferenceConfig": {"temperature": 0.2, "maxTokens": 50},
        },
    )
    messages = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}]
    assert llm.chat(cfg, messages, max_tokens=50) == "Hello from Bedrock."
    client = llm.bedrock_client(cfg, "eu-west-1")
    assert client.meta.endpoint_url == URL and client.meta.service_model.signing_name == "bedrock"
    creds = client._request_signer._credentials
    assert (creds.access_key, creds.secret_key) == tuple(KEY.split(":"))


def test_tool_calls_and_their_results_go_as_converse_turns(cfg, stub):
    tool = {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search the archive",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        },
    }
    png = base64.b64encode(b"\x89PNG\r\n").decode()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is in this?"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}},
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "search", "arguments": '{"q": "boats"}'}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "Two boats, 2024."},
        {"role": "user", "content": "And when?"},
    ]
    stub.add_response(
        "converse",
        {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": "Let me look."},
                        {"toolUse": {"toolUseId": "tooluse_9", "name": "search", "input": {"q": "boats 2024"}}},
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 80, "outputTokens": 20, "totalTokens": 100},
            "metrics": {"latencyMs": 900},
        },
        {
            "modelId": MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": "What is in this?"}, {"image": {"format": "png", "source": {"bytes": b"\x89PNG\r\n"}}}],
                },
                {"role": "assistant", "content": [{"toolUse": {"toolUseId": "call_1", "name": "search", "input": {"q": "boats"}}}]},
                {
                    "role": "user",
                    "content": [
                        {"toolResult": {"toolUseId": "call_1", "content": [{"text": "Two boats, 2024."}]}},
                        {"text": "And when?"},
                    ],
                },
            ],
            "inferenceConfig": {"temperature": 0.2},
            "toolConfig": {
                "tools": [
                    {
                        "toolSpec": {
                            "name": "search",
                            "description": "Search the archive",
                            "inputSchema": {"json": tool["function"]["parameters"]},
                        }
                    }
                ],
                "toolChoice": {"auto": {}},
            },
        },
    )
    got = llm.chat_message(cfg, messages, tools=[tool])
    assert got == {
        "content": "Let me look.",
        "tool_calls": [{"id": "tooluse_9", "type": "function", "function": {"name": "search", "arguments": '{"q": "boats 2024"}'}}],
    }


def test_a_json_answer_asks_for_the_schema_in_the_system_prompt(cfg, stub):
    schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
    stub.add_response(
        "converse",
        said('{"title": "Boats"}'),
        {
            "modelId": MODEL,
            "system": [
                {"text": "Name it."},
                {"text": "Reply with only a JSON object that matches this JSON Schema:\n" + json.dumps(schema)},
            ],
            "messages": [{"role": "user", "content": [{"text": "two boats"}]}],
            "inferenceConfig": {"temperature": 0.0},
        },
    )
    assert llm.json_out(cfg, "Name it.", "two boats", schema) == {"title": "Boats"}


def test_a_summary_goes_to_bedrock_too(cfg, stub):
    answer = {
        "title": "T",
        "summary": "S",
        "key_points": [],
        "action_items": [],
        "topics": [],
        "people": [],
        "sentiment": "Neutral",
        "importance": 3,
    }
    stub.add_response(
        "converse", said(json.dumps(answer)), {"modelId": MODEL, "system": ANY, "messages": ANY, "inferenceConfig": {"temperature": 0.0}}
    )
    got = analyze._llm(cfg, "Transcript:\n\n[00:00] Ada: hello")
    assert got["title"] == "T" and got["importance"] == 3


def test_a_streamed_answer_comes_whole(cfg, stub):
    stub.add_response("converse", said("<think>hmm</think>Hello."), {"modelId": MODEL, "messages": ANY, "inferenceConfig": ANY})
    assert "".join(llm.stream_chat(cfg, [{"role": "user", "content": "hi"}])) == "Hello."


def test_bedrocks_refusals_read_as_lenss(cfg, stub):
    ask = [{"role": "user", "content": "hi"}]
    stub.add_client_error("converse", "ValidationException", "Input is too long for requested model.", 400)
    with pytest.raises(llm.ContextFull, match="400 from the LLM server: ValidationException"):
        llm.chat(cfg, ask)
    stub.add_client_error("converse", "ValidationException", "This model doesn't support tool use.", 400)
    tool = {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}
    with pytest.raises(llm.ToolsUnsupported):
        llm.chat_message(cfg, ask, tools=[tool])
    stub.add_client_error("converse", "AccessDeniedException", "You don't have access to the model with the specified model ID.", 403)
    with pytest.raises(llm.LLMError, match="403 from the LLM server: AccessDeniedException"):
        llm.chat(cfg, ask)


def test_bedrock_lends_its_key_to_nothing_and_lists_the_model_it_was_given(cfg, stub, monkeypatch):
    from app.domain import semantic

    calls = []
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: calls.append(a))
    assert llm.shared(cfg) == (None, None) and semantic.endpoint(cfg)[:2] == (None, None)
    assert llm.list_models(cfg, ttl=0) == [MODEL] and llm.context_window(cfg, ttl=0) is None and calls == []
    cfg["llm"]["api_key"] = "ABSKbedrockapikey"
    with pytest.raises(llm.LLMError, match="Bedrock takes an AWS access key"):
        llm.chat(cfg, [{"role": "user", "content": "hi"}])
