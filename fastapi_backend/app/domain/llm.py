"""Any OpenAI-compatible chat server (LM Studio, Ollama, vLLM, llama.cpp, OpenAI): plain replies, streaming, and
structured output checked against a JSON Schema."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


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
    req = urllib.request.Request(l["base_url"].rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        return urllib.request.urlopen(req, timeout=l.get("timeout") or 300)
    except urllib.error.HTTPError as e:
        raise LLMError(f"{e.code} from the LLM server: {e.read().decode('utf-8', 'replace')[:300]}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"can't reach the LLM server at {l['base_url']}: {e}") from None


def _content(j):
    try:
        return j["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError("unexpected reply from the LLM server") from None


def chat(cfg, messages, model=None, max_tokens=None, temperature=0.2):
    payload = {"model": model or cfg["llm"]["model"], "messages": messages, "temperature": temperature}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    with _post(cfg, payload) as r:
        return _content(json.load(r))


def stream_chat(cfg, messages, model=None, temperature=0.2):
    r = _post(cfg, {"model": model or cfg["llm"]["model"], "messages": messages, "temperature": temperature, "stream": True})
    try:
        if "text/event-stream" not in (r.headers.get("Content-Type") or ""):
            yield _content(json.load(r))  # the server ignored stream=true
            return
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
            except (ValueError, KeyError, IndexError, TypeError):
                continue
            if delta:
                yield delta
    finally:
        r.close()


def parse_json(text):
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        return json.loads(t)
    except ValueError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            try:
                return json.loads(t[a:b + 1])
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
    payload = {"model": model or cfg["llm"]["model"], "messages": messages, "temperature": 0,
               "response_format": {"type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": False}}}
    try:
        with _post(cfg, payload) as r:
            text = _content(json.load(r))
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
        with _post(cfg, payload) as r:
            j = json.load(r)
    except LLMError as e:
        if tools and str(e)[:3] in ("400", "404", "422", "501"):
            raise ToolsUnsupported(str(e)) from None
        raise
    try:
        msg = j["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise LLMError("unexpected reply from the LLM server") from None
    return {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or []}
