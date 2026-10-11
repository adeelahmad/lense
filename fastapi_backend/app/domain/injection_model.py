"""A small local model trained to spot prompt injection, for ai.injection_check "classifier" (untrusted.flags).

ProtectAI's deberta-v3-base-prompt-injection-v2 (Apache-2.0) labels a text SAFE or INJECTION. It runs here, on ONNX
Runtime (the optional `injection` extra), so checking a tool's result sends nothing anywhere and costs no tokens. It is
off by default: the model is about 740 MB, fetched on first use when the setting asks for it (components.py), and until
it is here the free patterns carry on alone. A text longer than the model reads at once (512 tokens) is checked a
window at a time, up to WINDOWS of them, and the most suspicious window counts. Each check is a row in the activity
ledger (`injection_check`), with the tokens read and the time it took.

It reads a tool's result as the text its JSON carries (untrusted._plain): JSON itself looks like code to it. The model
reads English best, misses some attacks and flags some honest requests written for people ("please add the tracking
number to the link"); it is one layer of several (untrusted.py), a warning and never the one that stops an action.
"""

from __future__ import annotations

import logging
import threading

from . import activity, components

log = logging.getLogger("lens")

NAME = "protectai/deberta-v3-base-prompt-injection-v2"
MODEL, TOKENIZER = "prompt-injection-v2.onnx", "prompt-injection-v2-tokenizer.json"
WINDOW = 512  # tokens the model reads at once, its two special ones included
WINDOWS = 8
ABOVE = 0.8  # how sure the model must be that a window is an injection

_lock = threading.Lock()
_loaded: dict = {}


def ready(cfg):
    """Whether the model, its tokenizer and ONNX Runtime are here."""
    components.activate(cfg)
    return components.importable("onnxruntime", "tokenizers") and all(components.model_file(cfg, n) for n in (MODEL, TOKENIZER))


def _load(cfg):
    path = components.model_file(cfg, MODEL)
    with _lock:
        if _loaded.get("path") != path:
            import onnxruntime
            from tokenizers import Tokenizer

            tok = Tokenizer.from_file(components.model_file(cfg, TOKENIZER))
            tok.no_truncation()
            tok.no_padding()
            session = onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"])
            _loaded.clear()
            _loaded.update(path=path, tok=tok, session=session, inputs={i.name for i in session.get_inputs()})
        return _loaded


def _softmax(row):
    import math

    top = max(row)
    exp = [math.exp(x - top) for x in row]
    return [x / sum(exp) for x in exp]


def score(cfg, text):
    """How sure the model is that `text` (its most suspicious window) is a prompt injection, from 0 to 1."""
    import numpy as np

    m = _load(cfg)
    tok = m["tok"]
    ids = tok.encode(text, add_special_tokens=False).ids
    cls, sep = tok.token_to_id("[CLS]"), tok.token_to_id("[SEP]")
    size = WINDOW - 2
    windows = [ids[i : i + size] for i in range(0, max(len(ids), 1), size)][:WINDOWS]
    best = 0.0
    with activity.call("injection_check", cfg, NAME, detail={"windows": len(windows)}, price=activity.model_cost(cfg)) as ledger:
        for w in windows:
            seq = np.array([[cls, *w, sep]], dtype=np.int64)
            feed = {"input_ids": seq, "attention_mask": np.ones_like(seq), "token_type_ids": np.zeros_like(seq)}
            logits = m["session"].run(None, {k: v for k, v in feed.items() if k in m["inputs"]})[0][0]
            best = max(best, _softmax([float(x) for x in logits])[1])
        ledger.usage(tokens_in=sum(len(w) + 2 for w in windows), tokens_out=0)
    return best


def flags(cfg, text):
    """["the injection classifier"] when the model is sure `text` is an injection; [] when it isn't, or isn't here yet."""
    if not ready(cfg):
        return []
    try:
        return ["the injection classifier"] if score(cfg, text) >= ABOVE else []
    except Exception:  # noqa: BLE001 - the patterns still stand; an answer never fails on a check
        log.warning("the injection classifier failed", exc_info=True)
        return []
