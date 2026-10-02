"""Any OpenAI-compatible chat server (LM Studio, Ollama, vLLM, llama.cpp, OpenAI): plain replies, streaming, and
structured output checked against a JSON Schema."""

from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import telemetry


class LLMError(RuntimeError):
    pass


def configured(cfg):
    return bool(cfg["llm"].get("base_url") and cfg["llm"].get("model"))


def _post(cfg, payload):
    l = cfg["llm"]
    if not configured(cfg):
        raise LLMError("no language model is configured (Settings → LLM provider)")
    headers = {"Content-Type": "application/json"}
    key = l.get("api_key") or (os.environ.get(l["api_key_env"]) if l.get("api_key_env") else None)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        l["base_url"].rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        return urllib.request.urlopen(req, timeout=l.get("timeout") or 300)
    except urllib.error.HTTPError as e:
        raise LLMError(f"{e.code} from the LLM server: {e.read().decode('utf-8', 'replace')[:300]}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"can't reach the LLM server at {l['base_url']}: {e}") from None


_MODELS = {}  # base_url -> (when, names): the server's list, kept a minute


def list_models(cfg, ttl=60):
    """The models the server offers (GET /models), kept for `ttl` seconds."""
    l = cfg["llm"]
    if not configured(cfg):
        return []
    url = l["base_url"].rstrip("/")
    hit = _MODELS.get(url)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    headers = {}
    key = l.get("api_key") or (os.environ.get(l["api_key_env"]) if l.get("api_key_env") else None)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url + "/models", headers=headers), timeout=10) as r:
            names = sorted({str(m["id"]) for m in json.load(r).get("data") or [] if isinstance(m, dict) and m.get("id")})
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, AttributeError) as e:
        raise LLMError(f"can't list the models at {l['base_url']}: {e}") from None
    _MODELS[url] = (time.monotonic(), names)
    return names


def _load(r):
    """The server's JSON reply; LLMError when it isn't JSON (a proxy's error page, a reply cut off)."""
    try:
        return json.load(r)
    except (ValueError, OSError, http.client.HTTPException):
        raise LLMError("the LLM server's reply wasn't JSON") from None


THINK = re.compile(r"^\s*<think>.*?(?:</think>\s*|$)", re.S)


def unthink(text):
    """The reply without the <think>...</think> block reasoning models (Qwen 3, DeepSeek R1) start with."""
    return THINK.sub("", text or "", count=1)


def _content(j):
    try:
        return unthink(j["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        raise LLMError("unexpected reply from the LLM server") from None


def chat(cfg, messages, model=None, max_tokens=None, temperature=0.2):
    payload = {"model": model or cfg["llm"]["model"], "messages": messages, "temperature": temperature}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    with telemetry.model_call(cfg, payload) as call, _post(cfg, payload) as r:
        return _content(call.reply(_load(r)))


def _visible(pieces):
    """The streamed pieces without a leading <think>...</think> block, which can be split across pieces."""
    buf, thinking = "", None  # None: not yet known whether the answer starts with one
    for piece in pieces:
        if thinking is False:
            yield piece
            continue
        buf += piece
        head = buf.lstrip()
        if thinking is None:
            if len(head) < len("<think>") and "<think>".startswith(head):
                continue
            thinking = head.startswith("<think>")
            if not thinking:
                yield buf
                continue
        if "</think>" in buf:
            rest, buf, thinking = buf.split("</think>", 1)[1].lstrip(), "", False
            if rest:
                yield rest
    if thinking is None and buf:
        yield buf


def stream_chat(cfg, messages, model=None, temperature=0.2):
    """The answer in pieces as the server writes them, without any thinking. LLMError if the server stops mid-answer."""
    yield from _visible(_stream(cfg, model or cfg["llm"]["model"], messages, temperature))


def _stream(cfg, model, messages, temperature):
    payload = {"model": model, "messages": messages, "temperature": temperature, "stream": True}
    # not the current span: the generator may resume in another thread (a streamed response)
    call = telemetry.model_call(cfg, payload, current=False)
    error = None
    try:
        r = _post(cfg, payload)
    except LLMError as e:
        call.end(e)
        raise
    try:
        if "text/event-stream" not in (r.headers.get("Content-Type") or ""):
            yield _content(call.reply(_load(r)))  # the server ignored stream=true
            return
        finished = False
        try:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    finished = True
                    break
                try:
                    chunk = call.reply(json.loads(data))  # the last chunk may carry the usage, with no choices
                    choice = chunk["choices"][0]
                    delta = (choice.get("delta") or {}).get("content")
                except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                    continue
                finished = finished or bool(choice.get("finish_reason"))
                if delta:
                    yield delta
        except (OSError, http.client.HTTPException) as e:
            raise LLMError(f"the LLM server stopped mid-answer: {e}") from None
        if not finished:
            raise LLMError("the LLM server stopped mid-answer")
    except BaseException as e:
        error = e
        raise
    finally:
        r.close()
        call.end(None if isinstance(error, GeneratorExit) else error)


def parse_json(text):
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        return json.loads(t)
    except ValueError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            try:
                return json.loads(t[a : b + 1])
            except ValueError:
                pass
    raise LLMError("the reply wasn't JSON")


TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def validate(v, schema, path="$"):
    """The JSON Schema subset templates use: type, properties, required, items, enum, additionalProperties: false."""
    problems, t = [], schema.get("type")
    if t in ("number", "integer"):
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and (t == "number" or float(v).is_integer())
    elif t in TYPES:
        ok = isinstance(v, TYPES[t])
    else:
        ok = True
    if not ok:
        return [f"{path} should be {t}"]
    if "enum" in schema and v not in schema["enum"]:
        problems.append(f"{path} should be one of {schema['enum']}")
    if isinstance(v, dict):
        for k in schema.get("required", []):
            if k not in v:
                problems.append(f"{path}.{k} is missing")
        props = schema.get("properties", {})
        for k, x in v.items():
            if k in props:
                problems += validate(x, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                problems.append(f"{path}.{k} isn't allowed")
    if isinstance(v, list) and isinstance(schema.get("items"), dict):
        for i, x in enumerate(v):
            problems += validate(x, schema["items"], f"{path}[{i}]")
    return problems


def json_out(cfg, system, user, schema, model=None):
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    payload = {
        "model": model or cfg["llm"]["model"],
        "messages": messages,
        "temperature": 0,
        "response_format": {"type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": False}},
    }
    try:
        with telemetry.model_call(cfg, payload) as call, _post(cfg, payload) as r:
            text = _content(call.reply(_load(r)))
    except LLMError as e:
        if not str(e)[:3] in ("400", "422", "404"):
            raise
        messages[0]["content"] += "\n\nReply with only a JSON object that matches this JSON Schema:\n" + json.dumps(schema)
        text = chat(cfg, messages, model, temperature=0)
    value = parse_json(text)
    problems = validate(value, schema)
    if problems:
        raise LLMError("the reply didn't match the schema: " + "; ".join(problems[:3]))
    return value


class ToolsUnsupported(LLMError):
    pass


def chat_message(cfg, messages, tools=None, model=None, temperature=0.2):
    """One assistant turn; returns {"content", "tool_calls"}. Raises ToolsUnsupported if the server rejects tools."""
    payload = {"model": model or cfg["llm"]["model"], "messages": messages, "temperature": temperature}
    if tools:
        payload.update(tools=tools, tool_choice="auto")
    try:
        with telemetry.model_call(cfg, payload) as call, _post(cfg, payload) as r:
            j = call.reply(_load(r))
    except LLMError as e:
        if tools and str(e)[:3] in ("400", "404", "422", "501"):
            raise ToolsUnsupported(str(e)) from None
        raise
    try:
        msg = j["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise LLMError("unexpected reply from the LLM server") from None
    if not isinstance(msg, dict):
        raise LLMError("unexpected reply from the LLM server")
    return {"content": unthink(msg.get("content") or ""), "tool_calls": msg.get("tool_calls") or []}
