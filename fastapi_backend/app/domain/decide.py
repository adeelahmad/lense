"""Routine decisions the assistant takes instead of asking: which namespace a file goes in, and the like.

A decision model (Jev, typesafe.ai's System One) answers a multiple-choice question about some state in a few hundred
milliseconds, with an honest confidence, for a fraction of what a language model costs. When it's set up (a key in
Settings → AI assistant, or TYPESAFE_API_KEY) it takes these; otherwise the language model does, asked for a JSON
answer. Above `decisions.act_above` confidence the choice is acted on; below it the person is asked, with the options
ranked.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from . import activity, llm

log = logging.getLogger(__name__)
# Jev reads up to about 32k tokens; a state is cut well below that
MAX_STATE = 60_000


class Undecided(RuntimeError):
    """No engine could answer."""


def engine(cfg):
    """Which engine takes decisions: "jev", "llm", or None (off, or nothing set up)."""
    d = cfg.get("decisions") or {}
    want = d.get("engine") or "auto"
    if want == "off":
        return None
    if want == "jev" or (want == "auto" and d.get("api_key")):
        return "jev"
    return "llm" if llm.configured(cfg) else None


def _state(state):
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, default=str)
    return text[:MAX_STATE]


def _jev(cfg, questions, state):
    d = cfg.get("decisions") or {}
    headers = {"Content-Type": "application/json"}
    if d.get("api_key"):
        headers["Authorization"] = f"Bearer {d['api_key']}"
    body = {"state": _state(state), "model": d.get("model") or "jev-latest", "questions": questions}
    req = urllib.request.Request(
        (d.get("base_url") or "https://api.typesafe.ai/v1").rstrip("/") + "/systemone",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    with activity.call("decision", cfg, body["model"], detail={"questions": len(questions)}) as ledger:
        try:
            with urllib.request.urlopen(req, timeout=d.get("timeout") or 10) as r:
                answers = json.load(r)["answers"]
        except urllib.error.HTTPError as e:
            raise Undecided(f"{e.code} from the decision model: {e.read().decode('utf-8', 'replace')[:200]}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as e:
            raise Undecided(f"can't reach the decision model: {e}") from None
        if d.get("price_per_call") is not None:
            ledger.usage(cost_usd=float(d["price_per_call"]))
        return answers


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
    if how == "jev":
        try:
            a = _jev(cfg, {"q": {"type": "choice", "instructions": question, "criteria": dict(options)}}, state)["q"]
            if a.get("choice") in options:
                probs = a.get("probabilities") or {a["choice"]: a.get("confidence", 0)}
                return {"choice": a["choice"], "confidence": float(a.get("confidence") or 0), "ranked": _ranked(probs), "by": "jev"}
        except Undecided as e:
            log.warning("decision model failed, asking the language model: %s", e)
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
