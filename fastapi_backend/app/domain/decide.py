"""Typed decisions (docs/configuration.md#decisions): Lens asks a System One model questions whose answers are types,
not text. A yes/no question comes back as a probability, a choice as one of the options with a probability for each,
a score as a number on a scale, all with a confidence, in one request and a few hundred milliseconds. Code acts on the
answers with thresholds: what's sure is applied, what isn't is left for a person.

The server is TypeSafe's Jev (`POST https://api.typesafe.ai/v1/systemone`) or anything that speaks the same request:
an open model served locally, or a gateway. Off until an admin switches it on (Settings → Decisions). The API key is
sent when there is one; a gateway that adds its own needs none.

Nothing here raises into a request: `ask` raises DecideError, and the callers that rank, tag and flag catch it and
step aside, so search, imports and comments work the same without a decision model.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import store

log = logging.getLogger("lens")
HOSTED = "https://api.typesafe.ai"
MAX_QUESTIONS = 64  # in one request, whatever the setting says
STATE_CHARS = 24000  # the most state sent with one request
DOWN_SECONDS = 30
NO_KEY = "none"  # what the client is given where there's no key: a gateway in front puts its own in
_DOWN: dict = {}  # base URL -> (until, error): a server that just failed isn't asked again for a little while
_CLIENTS: dict = {}  # (base URL, key, timeout) -> the SDK's client, which keeps its connections
_L = threading.Lock()


class DecideError(RuntimeError):
    """The model couldn't be asked. Its text is safe for anyone (no address, nothing the server said); `detail` has
    the address and the server's words, for the log and for admins testing the settings."""

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail or message


def _section(cfg):
    return {**store.DEFAULTS["decisions"], **(cfg.get("decisions") or {})}


def endpoint(cfg):
    """(base URL, API key or None, model) of the decision server. The key is the one saved in the app, else the
    environment variable decisions.api_key_env names, else, for the hosted server only, TYPESAFE_API_KEY (what
    TypeSafe's own tools read)."""
    d = _section(cfg)
    base = (d.get("base_url") or HOSTED).rstrip("/")
    key = d.get("api_key") or (os.environ.get(d["api_key_env"]) if d.get("api_key_env") else None)
    if not key and base == HOSTED:  # TypeSafe's key only ever goes to TypeSafe
        key = os.environ.get("TYPESAFE_API_KEY")
    return base, key or None, (d.get("model") or "").strip() or None


def configured(cfg):
    """Whether decisions are on and there's a model to ask."""
    return bool(_section(cfg).get("enabled") and endpoint(cfg)[2])


def uses(cfg, feature):
    """Whether a feature that rests on decisions (rerank, classify, moderate, mcp) is on."""
    return configured(cfg) and bool(_section(cfg).get(feature))


def threshold(cfg, name):
    return float(_section(cfg)[name])


# ---------- questions ----------
def noul(instructions, yes=None, no=None):
    """A yes/no question: answered with the probability that it's yes."""
    q = {"type": "noul", "instructions": instructions}
    if yes or no:
        q["criteria"] = store.clean({"true": yes, "false": no})
    return q


def choice(instructions, options):
    """One of `options` ({key: what it means}, or a list of keys): answered with the key and each key's probability."""
    opts = options if isinstance(options, dict) else {str(o): None for o in options}
    if not 2 <= len(opts) <= 255:
        raise ValueError("a choice has 2 to 255 options")
    return {"type": "choice", "instructions": instructions, "criteria": opts}


def score(instructions, levels):
    """A level on a scale (`levels`, from the lowest, up to 10): answered with the expected level."""
    if not 2 <= len(levels) <= 10:
        raise ValueError("a score has 2 to 10 levels")
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


def _sdk():
    try:
        import typesafe_sdk
    except ImportError:  # pragma: no cover - it's a dependency
        raise DecideError("the typesafe-sdk package isn't installed") from None
    return typesafe_sdk


def _question(sdk, q):
    """Our question as the SDK's."""
    if q["type"] == "noul":
        crit = q.get("criteria")
        return sdk.Noul(instructions=q["instructions"], **({"criteria": sdk.NoulCriteria(**crit)} if crit else {}))
    if q["type"] == "choice":
        return sdk.Choice(instructions=q["instructions"], criteria=q["criteria"])
    return sdk.Score(instructions=q["instructions"], criteria=q["criteria"])


def _client(sdk, base, key, timeout):
    with _L:
        k = (base, key, timeout)
        if k not in _CLIENTS:
            # one more try when the server is busy (429, 529) or the connection drops, then give up: a search
            # shouldn't wait on this
            retry = sdk.RetryPolicy(max_retries=1, backoff_initial=0.3, backoff_max=1.0, timeout=timeout * 2)
            _CLIENTS[k] = sdk.TypeSafeClient(api_key=key or NO_KEY, base_url=base, timeout=timeout, retry=retry)
        return _CLIENTS[k]


def _mark_down(base, err):
    with _L:
        _DOWN[base] = (time.time() + DOWN_SECONDS, err)  # err: (public message, detail)


def recovered(cfg=None):
    """Forget that a server failed (after its settings change, or to test it)."""
    with _L:
        _DOWN.clear()
        old = list(_CLIENTS.values())
        _CLIENTS.clear()
    for c in old:
        try:
            c.close()
        except Exception:  # noqa: BLE001  (a client that won't close is only a connection left to time out)
            pass


def ask(cfg, state, questions, timeout=None):
    """Answers to `questions` ({id: question}) about `state` (text, or a JSON-able object), in one request:
    {id: {type, p | choice + probabilities | score, confidence}}. DecideError when there's no server, or it can't
    answer."""
    if not configured(cfg):
        raise DecideError("no decision model is set up (Settings → Decisions)")
    if not questions:
        return {}
    if len(questions) > MAX_QUESTIONS:
        raise ValueError(f"at most {MAX_QUESTIONS} questions in one request")
    base, key, model = endpoint(cfg)
    with _L:
        until, why = _DOWN.get(base, (0, None))
    if until > time.time():
        raise DecideError(*why)
    if isinstance(state, str):
        state = state[:STATE_CHARS]
    sdk = _sdk()
    timeout = float(timeout or _section(cfg).get("timeout") or 10)
    try:
        r = _client(sdk, base, key, timeout).system_one(state, {k: _question(sdk, q) for k, q in questions.items()}, model=model)
    except sdk.TypeSafeAPIError as e:
        status = getattr(e, "status", None)
        said = f"the decision server answered {status}" if status else "the decision server didn't answer"
        err = (said, f"{said} ({base}): {str(e)[:300]}")
        log.warning("%s", err[1])
        if status is None or status in (401, 403, 429, 529) or status >= 500:
            _mark_down(base, err)
        raise DecideError(*err) from None
    except sdk.TypeSafeError as e:
        # it couldn't be reached, or what it sent wasn't an answer: either way it isn't asked again for a while
        err = ("the decision server couldn't be reached or didn't give an answer", f"no usable answer from {base}: {str(e)[:300]}")
        log.warning("%s", err[1])
        _mark_down(base, err)
        raise DecideError(*err) from None
    out = {}
    for qid, q in questions.items():
        try:
            a = r.answers[qid]
            conf = getattr(a, "confidence", None)
            if q["type"] == "noul":
                p = float(a.noul)
                # a yes/no answer's confidence is how far its probability is from a coin toss
                out[qid] = {"type": "noul", "p": p, "confidence": float(conf) if conf is not None else max(p, 1 - p)}
            elif q["type"] == "choice":
                if a.choice not in q["criteria"]:  # an answer that isn't one of the options is no answer
                    raise DecideError("the decision server chose something that wasn't an option")
                probs = {k: float(v) for k, v in (a.probabilities or {}).items() if k in q["criteria"]}
                out[qid] = {
                    "type": "choice",
                    "choice": a.choice,
                    "probabilities": probs,
                    "confidence": float(conf if conf is not None else probs.get(a.choice, 0)),
                }
            else:
                out[qid] = {"type": "score", "score": float(a.score), "confidence": float(conf) if conf is not None else None}
        except (KeyError, TypeError, ValueError, AttributeError):
            raise DecideError(f"the decision server left a question unanswered ({qid})") from None
    return out


def ask_many(cfg, requests, timeout=None, workers=6):
    """Several requests at once: `requests` is [(state, questions)], the answers come back in the same order, and a
    request that failed comes back as its DecideError (so the caller keeps what did answer)."""

    def one(item):
        try:
            return ask(cfg, item[0], item[1], timeout)
        except DecideError as e:
            return e

    if len(requests) <= 1:
        return [one(r) for r in requests]
    with ThreadPoolExecutor(max_workers=min(workers, len(requests))) as pool:
        return list(pool.map(one, requests))


def status(cfg):
    """What Settings shows: {enabled, configured, base_url, hosted, model, key}."""
    base, key, model = endpoint(cfg)
    return {
        "enabled": bool(_section(cfg).get("enabled")),
        "configured": configured(cfg),
        "base_url": base,
        "hosted": base == HOSTED,
        "model": model,
        "key": bool(key),
    }
