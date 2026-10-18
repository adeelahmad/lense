"""Moondream describes pictures on this machine (docs/configuration.md#descriptions): with llm.describe_engine
"moondream", M87 Labs' Moondream captions each page and shot, through Hugging Face transformers on PyTorch (the
optional `moondream` extra), on the worker's NVIDIA GPU, a Mac's GPU, or its CPU. No picture leaves the machine and no
model provider is called.

Which Moondream is llm.moondream_model, each pinned to a revision of its Hugging Face repository:

* `moondream3-preview`, Moondream 3 (Preview): 9B parameters, 2B of them active for each token (a mixture of experts),
  about 19 GB of weights in bfloat16. It wants an NVIDIA GPU with 24 GB or a Mac with 32 GB or more: not a Raspberry
  Pi. Its weights and code are under the Business Source License 1.1 with an additional use grant (production use is
  allowed, except offering it to others in competition with M87 Labs' paid versions), and become Apache-2.0 two years
  after its release. That isn't an open-source licence, so nothing fetches it unless an admin picks it.
* `moondream2`, Moondream 2 (Apache-2.0): 1.9B parameters, about 3.7 GB; it runs on a CPU, slowly.

Moondream 3.1 runs only on M87 Labs' Photon engine, whose kernels are proprietary and licensed only under a separate
agreement with them, so Lens doesn't install it.

The model's code comes with its weights (transformers' trust_remote_code), at the pinned revision, so what runs can't
change under Lens; it loads Moondream's tokenizer (moondream/starmie-v1) from Hugging Face itself. components.py
fetches PyTorch (its CPU build when there's no NVIDIA GPU), transformers and the weights the first time a job waits at
the describe step with Moondream chosen. Each picture is a row in the activity ledger (`model.describe`), with the time
it took.
"""

from __future__ import annotations

import os
import threading

from . import activity, components, machine
from .descriptions import Failed

MODELS = {
    "moondream3-preview": {
        "repo": "moondream/moondream3-preview",
        "revision": "5112966d1a723413b1c9a1e8bea272b72e647b35",
        "name": "Moondream-3-Preview",
        "license": "BSL-1.1 (Moondream 3 Preview)",
        "gb": 19,
        "cpu": False,  # 19 GB of bfloat16 weights on a CPU: too slow to be any use
        "gpu_gb": 20,  # an NVIDIA GPU's memory to hold them (24 GB cards do)
        "mac_gb": 32,  # a Mac's memory, which its GPU shares
    },
    "moondream2": {
        "repo": "vikhyatk/moondream2",
        "revision": "5d6c926f44e26b07957b0dd315bbedcb4c17a5fe",
        "name": "Moondream-2",
        "license": "Apache-2.0",
        "gb": 3.7,
        "cpu": True,
        "memory_gb": 10,  # on a CPU it runs in float32: about 7.5 GB of weights, and room to work
    },
}
DEFAULT = "moondream3-preview"
TOKENIZER = "moondream/starmie-v1"  # both models' tokenizer, which their code loads from Hugging Face
LENGTH = "normal"  # caption length: short, normal or long

_lock = threading.Lock()
_loaded: dict = {}


def choice(cfg):
    m = (cfg.get("llm") or {}).get("moondream_model") or DEFAULT
    return m if m in MODELS else DEFAULT


def name(key):
    return MODELS[key]["name"]


def cannot_run(cfg, m=None):
    """Why this machine can't run the chosen Moondream, or None when it can. `m` is machine.probe()."""
    m = m or machine.probe()
    key = choice(cfg)
    need, nm = MODELS[key], name(key)
    gpus = [g.get("memory_gb") for g in m.get("gpus") or []]
    if gpus and any(g is None or g >= need.get("gpu_gb", 0) for g in gpus):
        return None  # a GPU's memory nvidia-smi didn't say is given the benefit of the doubt
    if not need["cpu"]:
        if m.get("apple_silicon") and (m.get("memory_gb") or 0) >= need["mac_gb"]:
            return None
        return (
            f"{nm} needs an NVIDIA GPU with {need['gpu_gb']} GB, or a Mac with {need['mac_gb']} GB of memory, and this "
            "server has neither: pick moondream2 (llm.moondream_model) or Florence-2"
        )
    if m.get("memory_gb") is not None and m["memory_gb"] < need["memory_gb"]:
        return f"{nm} needs about {need['memory_gb']} GB of memory on a CPU and this server has {m['memory_gb']} GB: pick Florence-2"
    return None


def device():
    """Where the model runs: an NVIDIA GPU, a Mac's GPU, else the CPU."""
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _mark(key):
    return f"moondream-{key}"


def ready(cfg):
    """Whether PyTorch, transformers and the chosen model's weights are here."""
    components.activate(cfg)
    return components.importable("torch", "transformers") and components._marked(cfg, _mark(choice(cfg)))


def fetch(cfg, say=None):
    """Download the chosen model's weights and code, at its revision, into the Hugging Face cache."""
    from huggingface_hub import snapshot_download

    key = choice(cfg)
    m = MODELS[key]
    if say:
        say(f"downloading {m['name']} (about {m['gb']} GB)")
    snapshot_download(m["repo"], revision=m["revision"], token=components.hf_token(cfg))
    snapshot_download(TOKENIZER, token=components.hf_token(cfg))  # its code loads it by name, from the same cache
    components._mark(cfg, _mark(key))


def _load(cfg, key):
    with _lock:  # one per process: loading gigabytes for every picture would take far longer than describing it
        if _loaded.get("key") != key:
            import torch
            from transformers import AutoModelForCausalLM

            m, where = MODELS[key], device()
            limit = os.environ.get("OMP_THREAD_LIMIT", "")
            if limit.isdigit() and 0 < int(limit) < torch.get_num_threads():
                # more threads than OpenMP will give makes PyTorch's CPU kernels write nonsense: "and, (, (, (…"
                torch.set_num_threads(int(limit))
            _loaded.clear()
            model = AutoModelForCausalLM.from_pretrained(
                m["repo"],
                revision=m["revision"],
                trust_remote_code=True,
                dtype=torch.float32 if where == "cpu" else torch.bfloat16,
                token=components.hf_token(cfg),
            ).to(where)
            model.eval()
            _loaded.update(key=key, model=model, device=where)
        return _loaded


class Moondream:
    """Moondream 3 (Preview) or 2 (llm.moondream_model), through transformers."""

    def __init__(self, cfg):
        self.cfg, self.key = cfg, choice(cfg)
        self.name = name(self.key)
        if why := cannot_run(cfg):
            raise RuntimeError(why)
        components.activate(cfg)
        if not components.importable("torch", "transformers"):
            raise RuntimeError(
                "PyTorch and transformers aren't installed (they're fetched on first use, or pip install \"lens[moondream]\")"
            )
        if not components._marked(cfg, _mark(self.key)):
            raise RuntimeError(f"the {self.name} model isn't here yet (it's fetched the first time a job needs it)")
        self.m = _load(cfg, self.key)

    def describe(self, path, about=None):
        """What the model says the picture at `path` shows (`about` isn't read: its caption skill takes no prompt)."""
        from PIL import Image

        with Image.open(path) as im:
            picture = im.convert("RGB")
        with activity.call("model.describe", self.cfg, self.name, detail={"engine": "moondream"}, price=activity.model_cost(self.cfg)):
            try:
                with __import__("torch").inference_mode():
                    return self.m["model"].caption(picture, length=LENGTH)["caption"]
            except Exception as e:  # noqa: BLE001 - the model's own code raises what it raises
                raise Failed(f"{self.name} couldn't read it: {e}") from e
