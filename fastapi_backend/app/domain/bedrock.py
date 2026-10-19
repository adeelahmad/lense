"""Lens's model calls to Amazon Bedrock (llm.py): OpenAI's chat schema in, Bedrock's Converse API out, and Bedrock's
answer back as OpenAI's, so the rest of Lens reads it as any other server's.

The LLM provider's address is Bedrock's runtime, `https://bedrock-runtime.<region>.amazonaws.com`, its model a Bedrock
model id, inference profile or provisioned throughput ARN, and its key an AWS access key, `<access key id>:<secret
access key>` (`:<session token>` after them for temporary keys). Calls go through botocore (Apache-2.0, the `aws`
extra, fetched on first use), which signs them with AWS Signature Version 4, so the key itself is never sent.

Converse takes the parts of a conversation Lens sends: system prompts, text and images, tool definitions, the
assistant's tool calls and their results. A JSON Schema asked for (`response_format`) goes into the system prompt."""

from __future__ import annotations

import base64
import io
import json
import re

BEDROCK = re.compile(r"^https://bedrock-runtime(?:-fips)?\.([a-z0-9-]+)\.amazonaws\.com(?:\.cn)?/?$")
IMAGE = re.compile(r"^data:image/(png|jpeg|jpg|gif|webp);base64,(.+)$", re.S)
STOP = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length"}
# how Bedrock says a request doesn't fit the model's context (a ValidationException)
FULL_SAYS = re.compile(r"too long|too many (?:input )?tokens|context (?:length|window)|maximum context", re.I)


def region(base_url):
    m = BEDROCK.match((base_url or "").strip())
    return m.group(1) if m else None


class Answer(io.BytesIO):
    """Bedrock's answer as an OpenAI-compatible server's reply, for llm.py to read."""

    headers = {"Content-Type": "application/json"}


def _text(content):
    """A message's content as Converse's blocks: text and data-URL images (what Lens sends)."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}] if content.strip() else []
    out = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and (part.get("text") or "").strip():
            out.append({"text": part["text"]})
        elif part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url") or ""
            m = IMAGE.match(url)
            if not m:
                raise ValueError("Bedrock takes images as data: URLs (PNG, JPEG, GIF or WebP)")
            fmt = "jpeg" if m.group(1) == "jpg" else m.group(1)
            out.append({"image": {"format": fmt, "source": {"bytes": base64.b64decode(m.group(2))}}})
    return out


def _arguments(raw):
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        v = None
    return v if isinstance(v, dict) else {}


def converse_params(payload):
    """Converse's request for an OpenAI chat request."""
    system, messages = [], []

    def add(role, blocks):
        if not blocks:
            return
        if messages and messages[-1]["role"] == role:  # Converse takes turns: one message per role in a row
            messages[-1]["content"] += blocks
        else:
            messages.append({"role": role, "content": blocks})

    for m in payload.get("messages") or []:
        role = m.get("role")
        if role in ("system", "developer"):
            system += [b for b in _text(m.get("content")) if "text" in b]
        elif role == "assistant":
            blocks = _text(m.get("content"))
            for c in m.get("tool_calls") or []:
                f = c.get("function") or {}
                blocks.append({"toolUse": {"toolUseId": c["id"], "name": f.get("name"), "input": _arguments(f.get("arguments"))}})
            add("assistant", blocks)
        elif role == "tool":
            said = m.get("content")
            said = said if isinstance(said, str) else json.dumps(said)
            add("user", [{"toolResult": {"toolUseId": m.get("tool_call_id"), "content": [{"text": said or "(nothing)"}]}}])
        else:
            add("user", _text(m.get("content")))
    fmt = payload.get("response_format") or {}
    if fmt.get("type") == "json_schema":
        schema = (fmt.get("json_schema") or {}).get("schema")
        system.append({"text": "Reply with only a JSON object that matches this JSON Schema:\n" + json.dumps(schema)})
    elif fmt.get("type") == "json_object":
        system.append({"text": "Reply with only a JSON object."})
    params = {"modelId": payload["model"], "messages": messages}
    if system:
        params["system"] = system
    inference = {}
    if payload.get("temperature") is not None:
        inference["temperature"] = float(payload["temperature"])
    if payload.get("max_tokens"):
        inference["maxTokens"] = int(payload["max_tokens"])
    if inference:
        params["inferenceConfig"] = inference
    tools = [t["function"] for t in payload.get("tools") or [] if t.get("type") == "function" and t.get("function")]
    if tools:
        specs = [
            {
                "toolSpec": {
                    "name": f["name"],
                    **({"description": f["description"]} if f.get("description") else {}),
                    "inputSchema": {"json": f.get("parameters") or {"type": "object", "properties": {}}},
                }
            }
            for f in tools
        ]
        # Converse has no "none": the tools stay offered so earlier calls make sense, and the model is free to answer
        choice = {"any": {}} if payload.get("tool_choice") == "required" else {"auto": {}}
        params["toolConfig"] = {"tools": specs, "toolChoice": choice}
    return params


def openai_answer(out, model):
    """OpenAI's chat reply for Converse's answer."""
    content = ((out.get("output") or {}).get("message") or {}).get("content") or []
    text = "".join(b["text"] for b in content if isinstance(b.get("text"), str))
    calls = [
        {"id": u["toolUseId"], "type": "function", "function": {"name": u["name"], "arguments": json.dumps(u.get("input") or {})}}
        for u in (b.get("toolUse") for b in content)
        if u
    ]
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = calls
    u = out.get("usage") or {}
    return {
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": STOP.get(out.get("stopReason"), "stop")}],
        "usage": {"prompt_tokens": u.get("inputTokens"), "completion_tokens": u.get("outputTokens"), "total_tokens": u.get("totalTokens")},
    }
