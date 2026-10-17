"""Florence-2 describes pictures on this machine (docs/configuration.md#descriptions): with llm.describe_engine
"florence-2", Microsoft's Florence-2 (MIT), fine-tuned, says in a paragraph what each page or shot shows, for the
describe step, instead of a model provider's model that can see images. No picture leaves the machine and no model
provider is called.

It runs on ONNX Runtime on the CPU (the `florence` extra: ONNX Runtime and the tokenizers library), from the 8-bit
ONNX exports onnx-community published, pinned to a revision and checked against their SHA-256: base (0.23B parameters,
about 275 MB) or large (0.77B, about 830 MB), llm.florence_model. components.py fetches them the first time a job
waits at the describe step with Florence-2 chosen, so an install that doesn't use it fetches nothing. A picture takes
a few seconds on a laptop's CPU, longer on a Raspberry Pi; base is the one for small machines.

It runs the way Florence2ForConditionalGeneration does: the picture, resized to 768 x 768 and normalised, becomes 577
image features; the task's prompt (<MORE_DETAILED_CAPTION>: "Describe with a paragraph what is shown in the image.")
is embedded after them; the encoder reads both; and the decoder writes the description a token at a time, the likeliest
each time, never writing three tokens in a row it already wrote (the model's no_repeat_ngram_size), with its attention
cache. Each picture is a row in the activity ledger (`model.describe`), with the tokens read and written and the time
it took. Florence-2 reads no instructions, so what a picture is ("page 3 of …") isn't given to it.
"""

from __future__ import annotations

import threading

from . import activity, components
from .descriptions import Failed

SIZES = ("base", "large")
PARTS = ("vision_encoder", "embed_tokens", "encoder_model", "decoder_model_merged")
PROMPT = "Describe with a paragraph what is shown in the image."  # the model's <MORE_DETAILED_CAPTION> task
SIDE = 768  # pixels on each side, as the model reads a picture
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)
BOS, EOS = 0, 2  # the decoder starts from EOS (decoder_start_token_id) and its first token is BOS (forced_bos_token_id)
NO_REPEAT = 3
MOST = 256  # tokens written at most: a long paragraph

_lock = threading.Lock()
_loaded: dict = {}


def size(cfg):
    s = (cfg.get("llm") or {}).get("florence_model") or "base"
    return s if s in SIZES else "base"


def name(s):
    return f"Florence-2-{s}"


def files(s):
    """Its files, as components.FILES names them: the four ONNX models, then the tokenizer."""
    return [f"florence-2-{s}-ft/{p}_int8.onnx" for p in PARTS] + [f"florence-2-{s}-ft/tokenizer.json"]


def ready(cfg):
    """Whether the chosen model, its tokenizer and ONNX Runtime are here."""
    components.activate(cfg)
    return components.importable("onnxruntime", "tokenizers") and all(components.model_file(cfg, n) for n in files(size(cfg)))


def _load(cfg, s):
    with _lock:  # one per process: loading hundreds of MB for every picture would take longer than describing it
        if _loaded.get("size") != s:
            import onnxruntime
            from tokenizers import Tokenizer

            paths = [components.model_file(cfg, n) for n in files(s)]
            _loaded.clear()  # the other size's sessions go: one is enough memory
            sessions = {p: onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"]) for p, path in zip(PARTS, paths)}
            tok = Tokenizer.from_file(paths[-1])
            _loaded.update(size=s, sessions=sessions, tok=tok, prompt=tok.encode(PROMPT).ids)
        return _loaded


def pixels(path):
    """The picture as the model reads it: RGB, 768 x 768 (bicubic), scaled to [0, 1], normalised, channels first."""
    import numpy as np
    from PIL import Image

    with Image.open(path) as im:
        x = np.asarray(im.convert("RGB").resize((SIDE, SIDE), Image.BICUBIC), dtype=np.float32) / 255.0
    return ((x - np.array(MEAN, dtype=np.float32)) / np.array(STD, dtype=np.float32)).transpose(2, 0, 1)[None].astype(np.float32)


def _banned(seq, n=NO_REPEAT):
    """The tokens that would repeat an n-gram already in seq."""
    if len(seq) < n:
        return set()
    tail = tuple(seq[len(seq) - n + 1 :])
    return {seq[i + n - 1] for i in range(len(seq) - n + 1) if tuple(seq[i : i + n - 1]) == tail}


def generate(sessions, prompt, picture, most=MOST):
    """The tokens the model writes about a picture (its pixels), and how many it read (the picture's features and the
    prompt's tokens). `sessions` are the four ONNX models, by PARTS."""
    import numpy as np

    def embed(ids):
        return sessions["embed_tokens"].run(None, {"input_ids": np.array([ids], dtype=np.int64)})[0]

    image = sessions["vision_encoder"].run(None, {"pixel_values": picture})[0]
    x = np.concatenate([image, embed(prompt)], axis=1)
    mask = np.ones(x.shape[:2], dtype=np.int64)
    hidden = sessions["encoder_model"].run(None, {"inputs_embeds": x, "attention_mask": mask})[0]
    dec = sessions["decoder_model_merged"]
    outs = [o.name for o in dec.get_outputs()]
    # the attention cache: empty at first; the encoder's part is kept from the first step, the decoder's grows
    cache = {
        i.name: np.zeros((1, i.shape[1], 0, i.shape[3]), dtype=np.float32)
        for i in dec.get_inputs()
        if i.name.startswith("past_key_values.")
    }
    seq = [EOS]
    for step in range(most):
        feed = {
            "encoder_attention_mask": mask,
            "encoder_hidden_states": hidden,
            "inputs_embeds": embed(seq[-1:]),
            "use_cache_branch": np.array([step > 0]),
            **cache,
        }
        got = dict(zip(outs, dec.run(None, feed)))
        if step == 0:
            token = BOS
        else:
            logits = np.array(got["logits"][0, -1], dtype=np.float64)
            for b in _banned(seq):
                logits[b] = -np.inf
            token = int(np.argmax(logits))
        for k in cache:
            if step == 0 or ".decoder." in k:
                cache[k] = got["present." + k[len("past_key_values.") :]]
        seq.append(token)
        if token == EOS:
            break
    return seq[1:], int(image.shape[1]) + len(prompt)


class Florence:
    """Florence-2, base or large (llm.florence_model), on ONNX Runtime."""

    def __init__(self, cfg):
        self.cfg, self.size = cfg, size(cfg)
        self.name = name(self.size)
        components.activate(cfg)
        if not components.importable("onnxruntime", "tokenizers"):
            raise RuntimeError("ONNX Runtime isn't installed (it's fetched on first use, or pip install \"lens[florence]\")")
        if not all(components.model_file(cfg, n) for n in files(self.size)):
            raise RuntimeError(f"the {self.name} model isn't here yet (it's fetched the first time a job needs it)")
        self.m = _load(cfg, self.size)

    def describe(self, path, about=None):
        """What the model says the picture at `path` shows (`about`, what it is, isn't read: Florence-2 takes no
        instructions)."""
        x = pixels(path)
        with activity.call(
            "model.describe", self.cfg, self.name, detail={"engine": "florence-2"}, price=activity.model_cost(self.cfg)
        ) as ledger:
            try:
                ids, read = generate(self.m["sessions"], self.m["prompt"], x)
            except Exception as e:  # noqa: BLE001 - ONNX Runtime's errors aren't one type
                raise Failed(f"{self.name} couldn't read it: {e}") from e
            ledger.usage(tokens_in=read, tokens_out=len(ids))
        return self.m["tok"].decode(ids, skip_special_tokens=True).strip()
