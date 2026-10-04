"""Routine decisions the assistant takes instead of asking: which namespace a file goes in, and the like.

A decision model (Jev, typesafe.ai's System One) answers a multiple-choice question about some state in a few hundred
milliseconds, with an honest confidence, for a fraction of what a language model costs. When it's set up (a key in
Settings → AI assistant, or TYPESAFE_API_KEY) it takes these; otherwise the language model does, asked for a JSON
answer. Above `decisions.act_above` confidence the choice is acted on; below it the person is asked, with the options
ranked.

Laya (engine "laya") is a decision model that runs on the machine itself: the same questions and answers as System
One, on MLX, so only on Apple Silicon. Lens runs it in this process there (Settings → Components fetches laya-mlx and
the chosen model); elsewhere, such as Docker on a Mac, it asks a Laya server (`lens decide-server` on the Mac) at
`decisions.laya_url`. When it can't answer, the language model does, as when Jev fails.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request

from . import llm, machine

log = logging.getLogger(__name__)
# Jev reads up to about 32k tokens; a state is cut well below that
MAX_STATE = 60_000
# the Laya models converted for MLX (github.com/mizorewww/laya-mlx), by Hugging Face repository
LAYA_MODELS = {
    "aac6fef/laya-mlx": "English, 421M parameters, 512 tokens of state",
    "aac6fef/laya-multilingual-mlx": "Many languages, 322M parameters, 1,024 tokens, about twice as fast",
    "aac6fef/laya-typed-decisions-mlx": "English, tuned for typed decisions, 421M parameters, 1,024 tokens",
}
LAYA_DEFAULT = "aac6fef/laya-mlx"


class Undecided(RuntimeError):
    """No engine could answer."""


def engine(cfg):
    """Which engine takes decisions: "jev", "laya", "llm", or None (off, or nothing set up)."""
    d = cfg.get("decisions") or {}
    want = d.get("engine") or "auto"
    if want == "off":
        return None
    if want == "laya":
        return "laya"
    if want == "jev" or (want == "auto" and d.get("api_key")):
        return "jev"
    return "llm" if llm.configured(cfg) else None


def _state(state):
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, default=str)
    return text[:MAX_STATE]


def _system_one(base_url, body, api_key=None, timeout=10):
    """POST to a System One API (Jev's, or a Laya server's) and return its answers."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(base_url.rstrip("/") + "/systemone", data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["answers"]
    except urllib.error.HTTPError as e:
        raise Undecided(f"{e.code} from the decision model: {e.read().decode('utf-8', 'replace')[:200]}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as e:
        raise Undecided(f"can't reach the decision model: {e}") from None


def _jev(cfg, questions, state):
    d = cfg.get("decisions") or {}
    body = {"state": _state(state), "model": d.get("model") or "jev-latest", "questions": questions}
    return _system_one(d.get("base_url") or "https://api.typesafe.ai/v1", body, d.get("api_key"), d.get("timeout") or 10)


# ---------- Laya on MLX ----------
_AGENTS = {}  # model -> laya_mlx.Agent, loaded once per process
_LOAD = threading.Lock()
_RUN = threading.Lock()  # one forward pass at a time on the GPU


def laya_model(cfg):
    return (cfg.get("decisions") or {}).get("laya_model") or LAYA_DEFAULT


def laya_here():
    """Whether this machine can run Laya itself (MLX needs Apple Silicon)."""
    return machine.probe()["apple_silicon"]


def laya_status(cfg):
    """Where Laya would answer from, and why not when it can't: {"available", "where", "model", "reason"}."""
    d = cfg.get("decisions") or {}
    model = laya_model(cfg)
    if d.get("laya_url"):
        return {"available": True, "where": "server", "model": model, "reason": None}
    if not laya_here():
        return {
            "available": False,
            "where": None,
            "model": model,
            "reason": "Laya runs on MLX, which needs a Mac with Apple Silicon. Run `lens decide-server` on one and "
            "put its address in Laya server",
        }
    from . import components

    if not components.LAYA.present(cfg):
        return {
            "available": False,
            "where": "here",
            "model": model,
            "reason": "laya-mlx and the model are still being fetched (Settings → Components)",
        }
    return {"available": True, "where": "here", "model": model, "reason": None}


def laya_agent(model):
    """The Laya model, loaded in this process (once)."""
    with _LOAD:
        if model not in _AGENTS:
            try:
                import laya_mlx
            except ImportError as e:
                raise Undecided(f"laya-mlx isn't installed here: {e}") from None
            try:
                _AGENTS[model] = laya_mlx.load(model, dtype="float16")
            except Exception as e:  # noqa: BLE001 - a missing or broken download, MLX refusing the device
                raise Undecided(f"can't load {model}: {e}") from None
        return _AGENTS[model]


def laya_predict(model, state, questions):
    agent = laya_agent(model)
    with _RUN:
        return agent.system_one(state, questions)


def _laya(cfg, questions, state):
    d = cfg.get("decisions") or {}
    if d.get("laya_url"):
        body = {"state": _state(state), "model": laya_model(cfg), "questions": questions}
        return _system_one(d["laya_url"], body, timeout=d.get("timeout") or 10)
    st = laya_status(cfg)
    if not st["available"]:
        raise Undecided(st["reason"])
    try:
        return laya_predict(st["model"], _state(state), questions)["answers"]
    except Undecided:
        raise
    except Exception as e:  # noqa: BLE001 - whatever the model raises, the language model takes over
        raise Undecided(f"Laya failed: {e}") from None


ENGINES = {"jev": _jev, "laya": _laya}


def _ranked(probs):
    return sorted(({"option": k, "p": round(float(v), 3)} for k, v in probs.items()), key=lambda x: -x["p"])


def choose(cfg, question, options, state):
    """Pick one of `options` ({name: what it means}) for `state`. Returns {"choice", "confidence", "ranked", "by"}.
    Raises Undecided when no engine is set up or all fail."""
    if not options:
        raise Undecided("nothing to choose from")
    if len(options) == 1:
        only = next(iter(options))
        return {"choice": only, "confidence": 1.0, "ranked": [{"option": only, "p": 1.0}], "by": "only option"}
    how = engine(cfg)
    if how in ENGINES:
        try:
            a = ENGINES[how](cfg, {"q": {"type": "choice", "instructions": question, "criteria": dict(options)}}, state)["q"]
            if a.get("choice") in options:
                probs = a.get("probabilities") or {a["choice"]: a.get("confidence", 0)}
                return {"choice": a["choice"], "confidence": float(a.get("confidence") or 0), "ranked": _ranked(probs), "by": how}
        except Undecided as e:
            log.warning("decision model (%s) failed, asking the language model: %s", how, e)
        how = "llm" if llm.configured(cfg) else None
    if how != "llm":
        raise Undecided("no decision model or language model is set up")
    schema = {
        "type": "object",
        "properties": {"choice": {"type": "string", "enum": list(options)}, "confidence": {"type": "number"}},
        "required": ["choice", "confidence"],
    }
    listed = "\n".join(f"- {k}: {v}" for k, v in options.items())
    try:
        a = llm.json_out(
            cfg,
            "You take routine decisions. Pick the one option that fits, and say how sure you are from 0 to 1: "
            "below 0.8 when the evidence is thin or two options fit.",
            f"{question}\n\nOptions:\n{listed}\n\nState:\n{_state(state)}",
            schema,
        )
    except llm.LLMError as e:
        raise Undecided(str(e)) from None
    conf = min(1.0, max(0.0, float(a["confidence"])))
    rest = (1 - conf) / max(1, len(options) - 1)
    return {
        "choice": a["choice"],
        "confidence": conf,
        "ranked": _ranked({k: conf if k == a["choice"] else rest for k in options}),
        "by": "llm",
    }


def sure(cfg, decision):
    """Whether a decision is sure enough to act on without asking."""
    return decision["confidence"] >= float((cfg.get("decisions") or {}).get("act_above") or 0.8)


# ---------- a Laya server, for Lens where MLX can't run (Docker on a Mac) ----------
def ready_laya(cfg, model, say=print):
    """Fetch laya-mlx and `model` into the data folder when they aren't there yet."""
    from . import components

    c = {**cfg, "decisions": {**(cfg.get("decisions") or {}), "engine": "laya", "laya_url": None, "laya_model": model}}
    components.activate(c)
    if not components.LAYA.present(c):
        components.LAYA.fetch(c, machine.probe(), say)


def laya_server(cfg, host="127.0.0.1", port=8790, model=LAYA_DEFAULT, say=print):
    """An HTTP server answering System One's API (POST /v1/systemone, GET /v1/health) with Laya on this machine. A
    request's `model` picks among LAYA_MODELS; any other name gets `model`."""
    import http.server

    if not laya_here():
        raise Undecided("Laya runs on MLX, which needs a Mac with Apple Silicon")
    ready_laya(cfg, model, say)
    laya_agent(model)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, obj):
            data = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.rstrip("/") in ("/v1/health", "/health"):
                return self._send(200, {"ok": True, "models": sorted(_AGENTS)})
            self._send(404, {"detail": "not found"})

        def do_POST(self):
            if self.path.rstrip("/") not in ("/v1/systemone", "/systemone"):
                return self._send(404, {"detail": "not found"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                questions, state = body["questions"], body["state"]
                if not (isinstance(questions, dict) and questions):
                    raise ValueError("questions is an object of named questions")
            except (ValueError, KeyError, TypeError) as e:
                return self._send(400, {"detail": f"a System One request has state and questions: {e}"})
            want = body.get("model") if body.get("model") in LAYA_MODELS else model
            try:
                if want not in _AGENTS:
                    ready_laya(cfg, want, say)
                out = laya_predict(want, state, questions)
            except Exception as e:  # noqa: BLE001 - the caller falls back to its language model
                return self._send(500, {"detail": f"{type(e).__name__}: {e}"[:400]})
            self._send(200, {**out, "model": want})

    return http.server.ThreadingHTTPServer((host, port), Handler)


def serve(cfg, host="127.0.0.1", port=8790, model=LAYA_DEFAULT, say=print):
    """`lens decide-server`: answer with Laya until stopped."""
    srv = laya_server(cfg, host, port, model, say)
    say(
        f"Laya ({model}) answers at http://{host}:{port}/v1; in Docker on this Mac, Lens reaches it at http://host.docker.internal:{port}/v1"
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
