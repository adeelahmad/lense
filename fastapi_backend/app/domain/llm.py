"""Any OpenAI-compatible chat server (LM Studio, Ollama, vLLM, llama.cpp, OpenAI): plain replies, streaming, and
structured output checked against a JSON Schema.

An Amazon SageMaker endpoint whose container takes OpenAI's chat schema (vLLM, LMI) is one too: its address is
`https://runtime.sagemaker.<region>.amazonaws.com/endpoints/<name>` and its key is an AWS access key,
`<access key id>:<secret access key>` (`:<session token>` after them for temporary keys). Lens posts chat requests to
the endpoint's `/invocations`, signed with AWS Signature Version 4 (botocore, the `aws` extra, fetched on first use),
so the key itself is never sent.

So is Amazon Bedrock: its address is `https://bedrock-runtime.<region>.amazonaws.com`, its model a Bedrock model id,
inference profile or provisioned throughput ARN, and its key an AWS access key as for SageMaker. Lens's chat requests go
to Bedrock's Converse API, translated both ways (bedrock.py)."""

from __future__ import annotations

import http.client
import itertools
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import bedrock, telemetry


class LLMError(RuntimeError):
    pass


class ContextFull(LLMError):
    """The server refused a request too long for the model's context. `window`: the context it said the model has, when
    it said (kept as the model's, so the next conversation is summarised before it fills that)."""

    def __init__(self, message, window=None):
        super().__init__(message)
        self.window = window


# how servers say a request doesn't fit: OpenAI's, vLLM's and OpenRouter's "maximum context length", llama.cpp's
# "exceeds the available context size", Groq's context_length_exceeded, Mistral's "too large for model with N maximum
# context length", Anthropic's "prompt is too long", Gemini's "exceeds the maximum number of tokens"
FULL_SAYS = re.compile(r"context[ _-]?(?:length|size|window)|maximum context|prompt is too long|exceeds the maximum number of tokens", re.I)
WINDOW_SAYS = re.compile(r"maximum context length is (\d{3,7})|with (\d{3,7}) maximum context length|\"n_ctx\"\s*:\s*(\d{3,7})", re.I)


def configured(cfg):
    return bool(cfg["llm"].get("base_url") and cfg["llm"].get("model"))


def trying(saved, base_url=None, model=None, api_key=None):
    """The LLM settings with typed values over the saved ones, for a test before saving. The saved key (stored or
    from the environment) goes only to the saved address, so a new address never receives it."""
    l = dict(saved)
    if base_url and base_url.strip():
        url = base_url.strip()
        if url.rstrip("/") != (saved.get("base_url") or "").rstrip("/"):
            l.update(api_key=None, api_key_env=None)
        l["base_url"] = url
    if model and model.strip():
        l["model"] = model.strip()
    if api_key:
        l["api_key"] = api_key
    return l


def _post(cfg, payload, whole=False):
    """The configured server's answer to a chat request (an open HTTP response, or Bedrock's as one); LLMError, or
    ContextFull, when it refuses. `whole` asks a SageMaker endpoint for the answer whole, as before it streamed."""
    l = cfg["llm"]
    if not configured(cfg):
        raise LLMError("no language model is configured (Settings → LLM provider)")
    if region := bedrock.region(l.get("base_url")):
        return _bedrock(cfg, payload, region)
    tries = _spread(cfg)[:2]  # a unit that can't be reached hands the call to the next
    for n, at in enumerate(tries, 1):
        req = chat_request(at, payload, whole)
        try:
            r = urllib.request.urlopen(req, timeout=l.get("timeout") or 300)
            if "vnd.amazon.eventstream" in (r.headers.get("Content-Type") or ""):
                return PayloadParts(r)
            return r
        except urllib.error.HTTPError as e:
            said = e.read().decode("utf-8", "replace")
            if e.code == 403 and req.full_url.endswith("/invocations-response-stream"):
                return _post(at, payload, whole=True)  # keys that may only InvokeEndpoint, as before answers streamed
            if e.code in (400, 413) and FULL_SAYS.search(said):
                m = WINDOW_SAYS.search(said)
                window = _tokens(next(g for g in m.groups() if g)) if m else None
                if window:  # what the server said, rather than what it lists or nothing
                    _WINDOWS[(l["base_url"].rstrip("/"), payload.get("model") or l["model"])] = (time.monotonic(), window)
                raise ContextFull(f"{e.code} from the LLM server: {said[:300]}", window) from None
            raise LLMError(f"{e.code} from the LLM server: {said[:300]}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if n == len(tries):
                raise LLMError(f"can't reach the LLM server at {at['llm']['base_url']}: {e}") from None


_TURN = itertools.count()  # whose turn it is among the addresses of llm.spread


def _spread(cfg):
    """Where a chat call may go, in order: the provider alone, or with `llm.spread` (auto scaling's units serving the
    provider's model with its key, scaling.py `_serve`) every one of them, starting with the next in turn. A spread
    left from another provider is ignored."""
    l = cfg["llm"]
    s = l.get("spread") or {}
    if not s.get("urls") or s.get("base_url") != l.get("base_url"):
        return [cfg]
    urls = [s["base_url"], *s["urls"]]
    i = next(_TURN) % len(urls)
    return [cfg if u == l["base_url"] else {**cfg, "llm": {**l, "base_url": u}} for u in urls[i:] + urls[:i]]


_MODELS = {}  # base_url -> (when, {name: what the server says of it}): the server's list, kept a minute
_WINDOWS = {}  # (base_url, model) -> (when, tokens or None): each model's context window, kept ten minutes


def _key(l):
    return l.get("api_key") or (os.environ.get(l["api_key_env"]) if l.get("api_key_env") else None)


def _headers(cfg):
    key = _key(cfg["llm"])
    return {"Authorization": f"Bearer {key}"} if key else {}


SAGEMAKER = re.compile(r"^https://runtime\.sagemaker\.([a-z0-9-]+)\.amazonaws\.com(?:\.cn)?/endpoints/[A-Za-z0-9-]{1,63}/?$")


def sagemaker_region(cfg):
    """The region of the SageMaker endpoint Lens's model calls go to; None when they go to an OpenAI-compatible server."""
    m = SAGEMAKER.match((cfg["llm"].get("base_url") or "").strip())
    return m.group(1) if m else None


def aws(cfg):
    """True when Lens's model calls go to AWS (SageMaker or Bedrock), signed with the provider's AWS key."""
    return bool(sagemaker_region(cfg) or bedrock.region((cfg.get("llm") or {}).get("base_url")))


def shared(cfg):
    """(base URL, key) of the LLM provider's server for its other OpenAI-compatible calls (embeddings, speech) when
    they have no server of their own; (None, None) for SageMaker or Bedrock, which Lens calls for chat only and whose
    AWS key is only ever used to sign."""
    l = cfg.get("llm") or {}
    if aws(cfg):
        return None, None
    return l.get("base_url"), _key(l)


def chat_request(cfg, payload, whole=False):
    """The POST of a chat request to the configured server: `/chat/completions` with the key as a bearer token, or a
    SageMaker endpoint's `/invocations` signed with the AWS key (`/invocations-response-stream` for a streamed
    answer: InvokeEndpointWithResponseStream, whose parts are the container's own server-sent events)."""
    l = cfg["llm"]
    body = json.dumps(payload).encode()
    region = sagemaker_region(cfg)
    if region is None:
        headers = {"Content-Type": "application/json", **_headers(cfg)}
        return urllib.request.Request(l["base_url"].rstrip("/") + "/chat/completions", data=body, headers=headers, method="POST")
    if payload.get("stream") and not whole:
        url = l["base_url"].strip().rstrip("/") + "/invocations-response-stream"
        headers = {"Content-Type": "application/json", "X-Amzn-SageMaker-Accept": "text/event-stream"}
    else:
        url = l["base_url"].strip().rstrip("/") + "/invocations"
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    return urllib.request.Request(url, data=body, headers=_aws_signed(cfg, region, url, body, headers), method="POST")


class PayloadParts:
    """SageMaker's streamed answer (AWS's event stream encoding, read with botocore's own decoder) as the container
    wrote it: the bytes of each PayloadPart event, in lines. An error event (ModelStreamError, InternalStreamFailure)
    is an OSError, which `_stream` reports as the server stopping mid-answer."""

    def __init__(self, r):
        self.r, self.headers = r, {"Content-Type": "text/event-stream"}

    def _parts(self):
        from botocore.eventstream import EventStreamBuffer, ParserError

        buf = EventStreamBuffer()
        try:
            while chunk := self.r.read(65536):
                buf.add_data(chunk)
                for m in buf:
                    h = m.headers
                    if h.get(":message-type") == "event" and h.get(":event-type") == "PayloadPart":
                        yield m.payload
                    elif h.get(":message-type") in ("exception", "error"):
                        what = h.get(":exception-type") or h.get(":error-code") or "error"
                        try:
                            said = json.loads(m.payload or b"{}").get("Message") or h.get(":error-message") or ""
                        except ValueError:
                            said = h.get(":error-message") or ""
                        raise OSError(f"{what}: {said}"[:300])
        except ParserError as e:
            raise OSError(f"the answer's event stream broke: {e}") from None

    def __iter__(self):
        rest = b""
        for part in self._parts():
            rest += part
            *lines, rest = rest.split(b"\n")
            for line in lines:
                yield line + b"\n"
        if rest:
            yield rest

    def close(self):
        self.r.close()


def _aws_key(cfg, service):
    """(access key id, secret access key, session token or None) from the key `<id>:<secret>[:<token>]`."""
    parts = (_key(cfg["llm"]) or "").split(":", 2)
    if len(parts) < 2 or not all(parts[:2]):
        raise LLMError(f"{service} takes an AWS access key as the provider's key: <access key id>:<secret access key>")
    return parts[0], parts[1], parts[2] if len(parts) > 2 else None


def _botocore(cfg, service):
    """botocore, fetched with the `aws` extra the first time Lens calls AWS."""
    try:
        import botocore.session  # noqa: F401
    except ImportError:
        from . import components

        try:
            components.pip_install(cfg, "aws")
            import botocore.session  # noqa: F401
        except Exception as e:  # noqa: BLE001 - why it couldn't, for the person
            raise LLMError(f"calling {service} needs the aws packages, which couldn't be installed here: {e}") from None
    import botocore

    return botocore


def _aws_signed(cfg, region, url, body, headers):
    """The headers with AWS Signature Version 4 for SageMaker's runtime, from the key `<id>:<secret>[:<token>]`."""
    key = _aws_key(cfg, "a SageMaker endpoint")
    _botocore(cfg, "SageMaker")
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    req = AWSRequest(method="POST", url=url, data=body, headers=headers)
    SigV4Auth(Credentials(*key), "sagemaker", region).add_auth(req)
    return dict(req.headers.items())


_CLIENTS = {}  # (region, key) -> Bedrock's runtime client: making one reads botocore's models, which takes a while


def bedrock_client(cfg, region):
    key = _aws_key(cfg, "Bedrock")
    hit = _CLIENTS.get((region, key))
    if hit is None:
        botocore = _botocore(cfg, "Bedrock")
        from botocore.config import Config

        timeout = cfg["llm"].get("timeout") or 300
        hit = botocore.session.Session().create_client(
            "bedrock-runtime",
            region_name=region,
            aws_access_key_id=key[0],
            aws_secret_access_key=key[1],
            aws_session_token=key[2],
            config=Config(read_timeout=timeout, connect_timeout=10, retries={"max_attempts": 2, "mode": "standard"}),
        )
        _CLIENTS.clear()  # one provider at a time: a changed key leaves no client behind
        _CLIENTS[(region, key)] = hit
    return hit


def _bedrock(cfg, payload, region):
    """Bedrock's Converse answer to an OpenAI chat request, as an OpenAI-compatible server's reply."""
    client = bedrock_client(cfg, region)
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        params = bedrock.converse_params(payload)
    except ValueError as e:
        raise LLMError(f"400 from the LLM server: {e}") from None
    try:
        if payload.get("stream"):
            try:
                return bedrock.Streamed(client.converse_stream(**params)["stream"], payload["model"])
            except ClientError as e:  # keys that may only InvokeModel, as before answers streamed: the answer whole
                if (e.response.get("Error") or {}).get("Code") != "AccessDeniedException":
                    raise
        out = client.converse(**params)
    except ClientError as e:
        err, status = e.response.get("Error") or {}, (e.response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 400
        said = f"{err.get('Code')}: {err.get('Message')}"
        if err.get("Code") == "ValidationException" and bedrock.FULL_SAYS.search(err.get("Message") or ""):
            raise ContextFull(f"{status} from the LLM server: {said[:300]}") from None
        raise LLMError(f"{status} from the LLM server: {said[:300]}") from None
    except BotoCoreError as e:
        raise LLMError(f"can't reach Bedrock in {region}: {e}") from None
    return bedrock.Answer(json.dumps(bedrock.openai_answer(out, payload["model"])).encode())


def post(cfg, payload):
    """The configured server's answer to a chat request, for callers outside this module."""
    return _post(cfg, payload)


def _listing(cfg, ttl=60):
    """The models the server offers (GET /models), each name with what the server says of it, kept for `ttl` seconds."""
    l = cfg["llm"]
    url = l["base_url"].rstrip("/")
    if aws(cfg):  # an endpoint serves the one model it was made with; Bedrock's model is the one named
        return {l["model"]: {"id": l["model"]}}
    hit = _MODELS.get(url)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    try:
        with urllib.request.urlopen(urllib.request.Request(url + "/models", headers=_headers(cfg)), timeout=10) as r:
            listed = {str(m["id"]): m for m in json.load(r).get("data") or [] if isinstance(m, dict) and m.get("id")}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, AttributeError) as e:
        raise LLMError(f"can't list the models at {l['base_url']}: {e}") from None
    _MODELS[url] = (time.monotonic(), listed)
    return listed


def list_models(cfg, ttl=60):
    """The models the server offers (GET /models), kept for `ttl` seconds."""
    return sorted(_listing(cfg, ttl)) if configured(cfg) else []


# what a GET /models entry calls the model's context window: LM Studio's loaded context first (what it runs with), then
# OpenRouter's, Together's and others' context_length, vLLM's max_model_len, Mistral's max_context_length, Groq's
# context_window and max_input_tokens
WINDOW_KEYS = ("loaded_context_length", "context_length", "max_model_len", "max_context_length", "context_window", "max_input_tokens")


def _tokens(v):
    if isinstance(v, str) and v.strip().isdigit():
        v = int(v)
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 256 else None


def _window_of(entry):
    for k in WINDOW_KEYS:
        if n := _tokens(entry.get(k)):
            return n
    return _tokens((entry.get("top_provider") or {}).get("context_length")) if isinstance(entry.get("top_provider"), dict) else None


def _llamacpp_window(cfg):
    """The context llama.cpp's server runs each request with (GET /props at its root), which its model list doesn't
    give (only the context the model was trained for)."""
    root = re.sub(r"/v1/?$", "", cfg["llm"]["base_url"].rstrip("/"))
    try:
        with urllib.request.urlopen(urllib.request.Request(root + "/props", headers=_headers(cfg)), timeout=10) as r:
            j = json.load(r)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise LLMError(f"can't ask llama.cpp's server for its context: {e}") from None
    if not isinstance(j, dict):
        return None
    gen = j.get("default_generation_settings")
    return _tokens(gen.get("n_ctx")) if isinstance(gen, dict) else _tokens(j.get("n_ctx"))


def context_window(cfg, model=None, ttl=600):
    """The model's context window in tokens as its server says: its GET /models entry (WINDOW_KEYS; a server listing
    one model speaks for whichever is asked for), or llama.cpp's /props. None when the server doesn't say or can't be
    asked. Kept `ttl` seconds, a miss too, so a server that doesn't say isn't asked before every answer."""
    if not configured(cfg):
        return None
    model = model or cfg["llm"]["model"]
    key = (cfg["llm"]["base_url"].rstrip("/"), model)
    hit = _WINDOWS.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    try:
        listed = _listing(cfg)
        entry = listed.get(model) or (next(iter(listed.values())) if len(listed) == 1 else {})
        n = _window_of(entry) or (_llamacpp_window(cfg) if entry.get("owned_by") == "llamacpp" else None)
    except LLMError:
        n = None
    _WINDOWS[key] = (time.monotonic(), n)
    return n


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


def chat_message(cfg, messages, tools=None, model=None, temperature=0.2, tool_choice="auto"):
    """One assistant turn; returns {"content", "tool_calls"}. Raises ToolsUnsupported if the server rejects tools.
    tool_choice "none" offers the tools (so earlier calls in the messages still make sense) but asks for text."""
    payload = {"model": model or cfg["llm"]["model"], "messages": messages, "temperature": temperature}
    if tools:
        payload.update(tools=tools, tool_choice=tool_choice)
    try:
        with telemetry.model_call(cfg, payload) as call, _post(cfg, payload) as r:
            j = call.reply(_load(r))
    except ContextFull:  # a 400 too, but the tools aren't why
        raise
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
