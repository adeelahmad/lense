"""What Lens needs to do its work, and getting it without anyone running a command.

Each worker looks after its own machine: what the settings ask for (the transcription engine, voice IDs, faces,
objects, the models on an Ollama server) is checked against what's installed, and what's missing is fetched in the
background: Python packages into the data folder (at the versions in uv.lock; PyTorch's CPU build when there's no
GPU), and models into data_dir/models, where they outlast container rebuilds. Steps that need something still being
fetched wait for it instead of failing. Programs the image provides (ffmpeg, Chromium, LibreOffice) are only checked:
a container can't install them, and the full image has them all.

`components.auto` (on by default) lets it fetch on its own; `components.also` names optional ones (Outlook .msg
support, which is GPL-3.0) to fetch too. What a job step needs is fetched on first use: when a job is waiting for that
step, so an archive nobody has put recordings into fetches nothing (a Raspberry Pi with cloud models stays small).
`components.ahead` fetches everything the settings could need straight away instead. Settings → Components shows each
worker's machine and where everything is.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

from . import machine, store

R = store.R
PROJECT = pathlib.Path(__file__).resolve().parents[2]  # fastapi_backend: pyproject.toml and uv.lock
TORCH_CPU = "https://download.pytorch.org/whl/cpu"
GPU_ONLY = re.compile(r"^(nvidia-|cuda-|triton$)")
TORCH_FAMILY = {"torch", "torchaudio", "torchvision"}  # PyPI's builds need CUDA on Linux
RETRY_SECONDS = 3600  # a failed fetch is tried again after this, or when the settings change
CHECK_SECONDS = 300
# files fetched by URL, checked against their hash: (url, sha256, size in MB)
FILES = {
    "yolox_s.onnx": (
        "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx",
        "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063",
        36,
    ),
    "face_detection_yunet_2023mar.onnx": (
        "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        1,
    ),
    "face_recognition_sface_2021dec.onnx": (
        "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        39,
    ),
}


class Unavailable(RuntimeError):
    """It can't be fetched here; the message says what to do instead."""


# ---------- where things go ----------
def models_dir(cfg):
    return pathlib.Path(cfg["data_dir"]) / "models"


def packages_dir(cfg):
    m = machine.probe()
    return pathlib.Path(cfg["data_dir"]) / "python" / f"py{m['python']}-{m['os']}-{m['arch']}"


def model_file(cfg, name):
    """A fetched model file's path, or None."""
    p = models_dir(cfg) / name
    return str(p) if p.is_file() else None


_ACTIVE = set()


def activate(cfg):
    """Make what was fetched usable in this process: the packages folder on the import path, and the model caches
    (Hugging Face, ModelScope, PyTorch) in the data folder, unless the environment chose them."""
    root = models_dir(cfg)
    for var, sub in (("HF_HOME", "huggingface"), ("MODELSCOPE_CACHE", "modelscope"), ("TORCH_HOME", "torch")):
        os.environ.setdefault(var, str(root / sub))
    pkgs = str(packages_dir(cfg))
    if pkgs not in _ACTIVE:
        _ACTIVE.add(pkgs)
        if pkgs not in sys.path:
            sys.path.append(pkgs)  # after the image's own packages: those win
    importlib.invalidate_caches()


def importable(*modules):
    for m in modules:
        try:
            importlib.import_module(m)
        except Exception:  # noqa: BLE001 - missing, or broken
            return False
    return True


# ---------- fetching ----------
def _run(cmd, cwd=None, env=None, timeout=3600):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-600:] or f"{cmd[0]} failed")
    return r.stdout


def _locked(extra):
    """The pinned requirements an extra adds (from uv.lock), without what this environment has already."""
    uv = shutil.which("uv")
    if not uv or not (PROJECT / "uv.lock").is_file():
        raise Unavailable(f"needs uv and uv.lock to fetch; install it yourself with: uv sync --extra {extra}")
    out = _run(
        [uv, "export", "--frozen", "--no-hashes", "--no-dev", "--no-emit-project", "--extra", extra, "--format", "requirements-txt"],
        cwd=PROJECT,
    )
    reqs = []
    for line in out.splitlines():
        line = line.strip()
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", line)
        if not m:
            continue
        try:
            if importlib.metadata.version(m.group(1)) == m.group(2):
                continue  # the image has it
        except importlib.metadata.PackageNotFoundError:
            pass
        reqs.append((m.group(1).lower().replace("_", "-"), line))
    return reqs


def pip_install(cfg, extra, say=None):
    """Install an extra's packages into the data folder, at uv.lock's versions; PyTorch's CPU build without a GPU."""
    m = machine.probe()
    reqs = _locked(extra)
    if not m["cuda"] and m["os"] == "linux":
        reqs = [(n, line) for n, line in reqs if not GPU_ONLY.match(n)]
    torch = [line for n, line in reqs if n in TORCH_FAMILY]
    rest = [line for n, line in reqs if n not in TORCH_FAMILY]
    target = packages_dir(cfg)
    target.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "UV_NO_CACHE": "1"}  # a second copy of every wheel would only fill the disk
    base = [shutil.which("uv"), "pip", "install", "--python", sys.executable, "--target", str(target), "--no-deps", "--quiet"]
    cpu_torch = not m["cuda"] and m["os"] == "linux"
    for group, index in ((torch, TORCH_CPU if cpu_torch else None), (rest, None)):
        if not group:
            continue
        reqfile = target.parent / f".{extra}-requirements.txt"
        lines = [re.sub(r"^(torch(?:audio|vision)?)==([^\s;+]+)", r"\1==\2+cpu", x) if index else x for x in group]
        reqfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if say:
            say(f"installing {len(group)} package(s) for {extra}")
        _run(base + (["--index-url", index] if index else []) + ["-r", str(reqfile)], env=env)
        reqfile.unlink(missing_ok=True)
    activate(cfg)


def fetch_file(cfg, name, say=None):
    url, sha, _ = FILES[name]
    dest = models_dir(cfg) / name
    if dest.is_file():
        return str(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    h = hashlib.sha256()
    if say:
        say(f"downloading {name}")
    with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as f:
        while chunk := r.read(1 << 20):
            h.update(chunk)
            f.write(chunk)
    if h.hexdigest() != sha:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"{name} didn't match its checksum")
    part.replace(dest)
    return str(dest)


def _mark(cfg, name):
    p = models_dir(cfg) / ".ready" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="ascii")


def _marked(cfg, name):
    return (models_dir(cfg) / ".ready" / name).is_file()


# ---------- models on an Ollama server ----------
def _ollama_root(base_url):
    """The Ollama server behind an OpenAI-compatible address, or None when it isn't one."""
    if not base_url:
        return None
    u = urllib.parse.urlsplit(base_url)
    root = f"{u.scheme}://{u.netloc}"
    try:
        with urllib.request.urlopen(root + "/api/version", timeout=3) as r:
            return root if "version" in json.load(r) else None
    except Exception:  # noqa: BLE001 - not Ollama, or not reachable
        return None


def ollama_models(root):
    with urllib.request.urlopen(root + "/api/tags", timeout=5) as r:
        rows = json.load(r).get("models") or []
    names = set()
    for m in rows:
        for n in (m.get("name"), m.get("model")):
            if n:
                names.add(n)
                if n.endswith(":latest"):
                    names.add(n[: -len(":latest")])
    return names


def ollama_pull(root, model, say=None):
    req = urllib.request.Request(
        root + "/api/pull", data=json.dumps({"model": model}).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    last = 0.0
    with urllib.request.urlopen(req, timeout=3600) as r:
        for line in r:
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("error"):
                raise RuntimeError(ev["error"])
            if say and ev.get("total") and time.time() - last > 5:
                last = time.time()
                say(f"pulling {model}: {round(100 * (ev.get('completed') or 0) / ev['total'])}%")


# ---------- what there is ----------
class Component:
    """One thing Lens needs. `needed(cfg, m)` says whether the settings ask for it; `present(cfg)` whether it's here;
    `fetch(cfg, m, say)` gets it (None for programs, which the image provides)."""

    def __init__(self, id, label, purpose, steps=(), size_mb=None, optional=False, license=None, hint=None):
        self.id, self.label, self.purpose, self.steps = id, label, purpose, set(steps)
        self.size_mb, self.optional, self.license, self.hint = size_mb, optional, license, hint

    kind = "package"

    def needed(self, cfg, m):
        return False

    def present(self, cfg):
        return False

    def serves(self, cfg, m):
        """The job steps that wait for it: what makes it fetched on first use."""
        return self.steps

    fetch = None


class Program(Component):
    kind = "program"

    def __init__(self, *a, names=(), when=None, **kw):
        super().__init__(*a, **kw)
        self.names, self.when = names, when

    def needed(self, cfg, m):
        return self.when(cfg) if self.when else True

    def present(self, cfg):
        return any(shutil.which(n) for n in self.names)


def _chromium(cfg):
    from . import convert

    return bool(convert.chromium(cfg))


def _soffice(cfg):
    from . import convert

    return bool(convert.soffice(cfg))


class Converter(Program):
    def __init__(self, *a, check=None, **kw):
        super().__init__(*a, **kw)
        self.check = check

    def present(self, cfg):
        return self.check(cfg)


class Pytorch(Component):
    def needed(self, cfg, m):
        return SENSEVOICE.needed(cfg, m) or VOICES.needed(cfg, m)

    def present(self, cfg):
        return importable("torch", "torchaudio")

    def serves(self, cfg, m):
        return set().union(*(c.steps for c in (SENSEVOICE, VOICES) if c.needed(cfg, m)))

    def fetch(self, cfg, m, say):
        pip_install(cfg, "voices", say)  # torch and torchaudio; SenseVoice and voice IDs both need them


class SenseVoice(Component):
    def needed(self, cfg, m):
        return cfg["transcribe"].get("engine") == "sensevoice"

    def present(self, cfg):
        return importable("funasr") and _marked(cfg, "sensevoice")

    def fetch(self, cfg, m, say):
        if not importable("funasr"):
            pip_install(cfg, "sensevoice", say)
        from . import ingest

        if say:
            say("downloading the SenseVoice model")
        ingest.SenseVoice(cfg)  # loading it fetches the model and the voice-activity model
        _mark(cfg, "sensevoice")


class WhisperModel(Component):
    kind = "model"

    def needed(self, cfg, m):
        return cfg["transcribe"].get("engine") == "whisper" and not m["apple_silicon"]

    def _name(self, cfg):
        return cfg["transcribe"]["whisper"]["model"]

    def present(self, cfg):
        return _marked(cfg, "whisper-" + re.sub(r"[^\w.-]", "_", self._name(cfg)))

    def fetch(self, cfg, m, say):
        try:
            from faster_whisper import download_model
        except ImportError:
            pip_install(cfg, "whisper", say)
            from faster_whisper import download_model
        if say:
            say(f"downloading the Whisper {self._name(cfg)} model")
        download_model(self._name(cfg))
        _mark(cfg, "whisper-" + re.sub(r"[^\w.-]", "_", self._name(cfg)))


class Voices(Component):
    def needed(self, cfg, m):
        return cfg["speakers"].get("embedder") == "speechbrain" and cfg["diarize"].get("engine") in ("auto", "cluster")

    def present(self, cfg):
        return importable("speechbrain") and _marked(cfg, "voices")

    def fetch(self, cfg, m, say):
        if not importable("speechbrain"):
            pip_install(cfg, "voices", say)
        from . import speakers

        if say:
            say("downloading the voice ID model")
        speakers.Embedder(cfg)
        _mark(cfg, "voices")


class Faces(Component):
    """OpenCV with YuNet and SFace; for anytopdf (video.face_engine `anytopdf`) only SFace, which its face-id plugin
    describes faces with, since it finds them itself."""

    def _files(self, cfg):
        return FACE_FILES[1:] if cfg["video"].get("face_engine") == "anytopdf" else FACE_FILES

    def needed(self, cfg, m):
        v = cfg["video"]
        if v.get("face_engine") == "anytopdf":
            return not v.get("sface_model")
        return v.get("face_engine") == "opencv" and not (v.get("yunet_model") and v.get("sface_model") and importable("cv2"))

    def present(self, cfg):
        opencv = cfg["video"].get("face_engine") == "anytopdf" or importable("cv2")
        return opencv and all(model_file(cfg, n) for n in self._files(cfg))

    def fetch(self, cfg, m, say):
        if cfg["video"].get("face_engine") != "anytopdf" and not importable("cv2"):
            pip_install(cfg, "faces", say)
        for n in self._files(cfg):
            fetch_file(cfg, n, say)


class Objects(Component):
    def needed(self, cfg, m):
        return cfg["video"].get("object_engine") == "yolox"

    def present(self, cfg):
        from . import objects

        return importable("onnxruntime") and bool(objects.yolox_model(cfg))

    def fetch(self, cfg, m, say):
        if not importable("onnxruntime"):
            pip_install(cfg, "objects", say)
        fetch_file(cfg, "yolox_s.onnx", say)


class Anytopdf(Component):
    """The anytopdf program (anytopdf.py), fetched when Settings → Documents asks for it to run here or it finds faces
    (video.face_engine `anytopdf`)."""

    def needed(self, cfg, m):
        from . import anytopdf

        faces = (cfg.get("video") or {}).get("face_engine") == "anytopdf"
        return (faces or anytopdf.mode(cfg) == "anytopdf" and not anytopdf.node(cfg)) and bool(anytopdf.archive())

    def present(self, cfg):
        from . import anytopdf

        return bool(anytopdf.binary(cfg))

    def fetch(self, cfg, m, say):
        from . import anytopdf

        anytopdf.fetch(cfg, say)


class Msg(Component):
    def needed(self, cfg, m):
        return False  # only when asked for (components.also): it's GPL-3.0

    def present(self, cfg):
        return importable("extract_msg")

    def fetch(self, cfg, m, say):
        pip_install(cfg, "msg", say)


class Laya(Component):
    """laya-mlx and the chosen Laya model, for local decisions (decide.py) on Apple Silicon."""

    kind = "model"

    def _model(self, cfg):
        from . import decide

        return decide.laya_model(cfg)

    def _tag(self, cfg):
        return "laya-" + re.sub(r"[^\w.-]", "_", self._model(cfg))

    def needed(self, cfg, m):
        d = cfg.get("decisions") or {}
        return d.get("engine") == "laya" and not d.get("laya_url") and m["apple_silicon"]

    def present(self, cfg):
        return importable("laya_mlx") and _marked(cfg, self._tag(cfg))

    def fetch(self, cfg, m, say):
        if not importable("laya_mlx"):
            pip_install(cfg, "laya", say)
        from huggingface_hub import snapshot_download

        if say:
            say(f"downloading the Laya model {self._model(cfg)}")
        snapshot_download(self._model(cfg))
        _mark(cfg, self._tag(cfg))

    def detail(self, cfg):
        return self._model(cfg)


class OllamaModel(Component):
    kind = "server-model"

    def __init__(self, *a, section, **kw):
        super().__init__(*a, **kw)
        self.section = section

    def _where(self, cfg):
        s = cfg.get(self.section) or {}
        base = s.get("base_url") or (cfg["llm"].get("base_url") if self.section == "embeddings" else None)
        return base, s.get("model")

    def needed(self, cfg, m):
        if self.section == "embeddings" and not (cfg.get("embeddings") or {}).get("enabled", True):
            return False
        base, model = self._where(cfg)
        return bool(model and _ollama_root(base))

    def present(self, cfg):
        base, model = self._where(cfg)
        root = _ollama_root(base)
        if not (root and model):
            return True
        try:
            return model in ollama_models(root)
        except Exception:  # noqa: BLE001 - the server went away
            return True

    def fetch(self, cfg, m, say):
        base, model = self._where(cfg)
        ollama_pull(_ollama_root(base), model, say)

    def detail(self, cfg):
        return self._where(cfg)[1]


FACE_FILES = ("face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx")
PYTORCH = Pytorch("pytorch", "PyTorch", "runs SenseVoice and voice IDs (the CPU build when there's no NVIDIA GPU)", size_mb=900)
SENSEVOICE = SenseVoice("sensevoice", "SenseVoice", "transcribes speech, fast on a CPU", steps={"transcribe"}, size_mb=1100)
WHISPER = WhisperModel("whisper-model", "Whisper model", "transcribes speech with Whisper", steps={"transcribe"}, size_mb=1600)
VOICES = Voices("voices", "Voice IDs", "tells speakers apart and recognises them across recordings", steps={"diarize"}, size_mb=100)
LAYA = Laya(
    "laya",
    "Laya decision model",
    "takes routine decisions on this Mac (laya-mlx on Apple Silicon)",
    size_mb=900,
    hint="Apple Silicon only; elsewhere run `lens decide-server` on a Mac",
)
COMPONENTS = [
    Program(
        "ffmpeg",
        "ffmpeg",
        "reads audio and video",
        names=("ffmpeg",),
        steps={"transcribe", "shots"},
        hint="in every Lens image; on your own machine: install ffmpeg",
    ),
    Program(
        "tesseract",
        "Tesseract",
        "reads text on screen and in scans",
        names=("tesseract",),
        when=lambda c: c["video"].get("ocr_engine") in ("auto", "tesseract"),
        hint="in every Lens image; on your own machine: install tesseract-ocr",
    ),
    Converter(
        "chromium",
        "Chromium",
        "reads web pages, text and emails",
        check=_chromium,
        hint="in the full Lens image (LENS_TARGET=full, the default)",
    ),
    Converter(
        "libreoffice",
        "LibreOffice",
        "reads Word, PowerPoint and spreadsheet files",
        check=_soffice,
        hint="in the full Lens image (LENS_TARGET=full, the default)",
    ),
    Anytopdf(
        "anytopdf",
        "anytopdf",
        "makes documents and photographed pages into searchable PDFs (documents.converter), and can find faces",
        steps={"transcribe", "faces"},
        size_mb=50,
        license="MIT OR Apache-2.0",
    ),
    PYTORCH,
    SENSEVOICE,
    WHISPER,
    VOICES,
    Faces("faces", "Face models", "finds and recognises faces in video (YuNet and SFace)", steps={"faces"}, size_mb=80),
    Objects("objects", "Object model", "finds objects in frames and pages (YOLOX-s)", steps={"objects"}, size_mb=50),
    LAYA,
    OllamaModel("llm-model", "Chat model", "the LLM provider's model, pulled on Ollama", section="llm"),
    OllamaModel("embedding-model", "Embedding model", "search by meaning's model, pulled on Ollama", section="embeddings", steps={"embed"}),
    Msg("msg", "Outlook .msg emails", "reads Outlook .msg files (extract-msg)", optional=True, license="GPL-3.0", size_mb=5),
]
BY_ID = {c.id: c for c in COMPONENTS}


def recommend(m):
    """The transcription settings that suit a machine: Whisper on an NVIDIA GPU, small Whisper with little memory,
    else SenseVoice (fast and accurate on a CPU)."""
    if m["cuda"]:
        return {"engine": "whisper", "device": "cuda", "whisper": {"model": "large-v3-turbo", "compute_type": "float16"}}
    if m["apple_silicon"]:
        return {"engine": "mlx-whisper"}
    if (m["memory_gb"] or 8) < 6 or (m["disk_free_gb"] or 50) < 5:
        return {"engine": "whisper", "whisper": {"model": "small", "compute_type": "int8"}}
    return {"engine": "sensevoice"}


# ---------- looking after this machine ----------
class Keeper:
    """Fetches what this worker's settings need, one at a time, in the background; reports on the worker's row."""

    def __init__(self, db, cfg_fn, name, can, log=None):
        self.db, self.cfg_fn, self.name, self.can, self.log = db, cfg_fn, name, set(can), log
        self.state = {}  # id -> {state, detail, error, at}
        self.lock = threading.Lock()
        self.failed_at = {}
        self.seen = None
        self.later = {}  # id -> the steps that wait for it: fetched on first use, once a job needs one of them

    def _say(self, cid):
        def say(msg):
            with self.lock:
                self.state[cid] = {**self.state.get(cid, {}), "state": "fetching", "detail": msg}
            self.report()
            if self.log:
                self.log(f"components: {msg}")

        return say

    def wanted(self, cfg, m):
        also = set((cfg.get("components") or {}).get("also") or [])
        out = []
        for c in COMPONENTS:
            if c.kind == "program":
                continue
            if c.steps and not c.steps & self.can and c.id not in also:
                continue
            if c.id in also or c.needed(cfg, m):
                out.append(c)
        return out

    def blocked(self):
        """Steps waiting for something being fetched, or to be fetched on first use."""
        with self.lock:
            busy = [cid for cid, s in self.state.items() if s.get("state") in ("waiting", "fetching")]
            later = set().union(*self.later.values())
        return set().union(later, *(BY_ID[c].steps for c in busy))

    def demand(self):
        """The steps queued jobs are waiting to run next."""
        try:
            return {r["next_step"] for r in self.db.rows("SELECT next_step FROM job WHERE status = 'queued' GROUP BY next_step")}
        except Exception:  # noqa: BLE001 - asked again on the next pass
            return set()

    def _first_use(self, cfg, c, m, demand):
        """Whether it waits for first use: not fetched ahead, not asked for by name, and no queued job needs it yet."""
        opts = cfg.get("components") or {}
        if opts.get("ahead", False) or c.id in set(opts.get("also") or []):
            return set()
        steps = c.serves(cfg, m)
        return set() if not steps or steps & demand else steps

    def report(self):
        with self.lock:
            snap = json.loads(json.dumps(self.state))
        try:
            self.db.q(
                "UPDATE $w SET components = $c, machine = $m", w=R("worker", self.name), c=snap, m=machine.probe(self.cfg_fn()["data_dir"])
            )
        except Exception:  # noqa: BLE001 - reporting never stops the work
            pass

    def check(self):
        """One pass: what's wanted and missing is fetched, unless fetching is off."""
        cfg = self.cfg_fn()
        activate(cfg)
        m = machine.probe(cfg["data_dir"])
        auto = (cfg.get("components") or {}).get("auto", True)
        also = set((cfg.get("components") or {}).get("also") or [])
        demand = self.demand()
        todo = []
        state = {}
        later = {}
        for c in self.wanted(cfg, m):
            try:
                here = c.present(cfg)
            except Exception:  # noqa: BLE001
                here = False
            if here:
                state[c.id] = {"state": "ready"}
            elif (auto or c.id in also) and (wait := self._first_use(cfg, c, m, demand)):
                state[c.id] = {"state": "later"}
                later[c.id] = wait
            elif (auto or c.id in also) and time.time() - self.failed_at.get(c.id, 0) > RETRY_SECONDS:
                state[c.id] = {"state": "waiting"}
                todo.append(c)
            else:
                prev = self.state.get(c.id) or {}
                state[c.id] = {
                    "state": "failed" if prev.get("error") else "missing",
                    **({"error": prev["error"]} if prev.get("error") else {}),
                }
        with self.lock:
            self.state, self.later = state, later
        self.report()
        for c in todo:
            say = self._say(c.id)
            say(f"fetching {c.label}")
            try:
                c.fetch(cfg, m, say)
                with self.lock:
                    self.state[c.id] = {"state": "ready"}
                self.failed_at.pop(c.id, None)
                if self.log:
                    self.log(f"components: {c.label} is ready")
            except Exception as e:  # noqa: BLE001 - recorded; the step falls back as before
                self.failed_at[c.id] = time.time()
                with self.lock:
                    self.state[c.id] = {"state": "failed", "error": f"{type(e).__name__}: {e}"[:400]}
                if self.log:
                    self.log(f"components: {c.label} failed: {e}")
            self.report()
        return self.state

    def settings_version(self):
        return self.db.values("SELECT VALUE n FROM $r", r=R("seq", "settings"))

    def loop(self, stop):
        last = 0.0
        while not stop.is_set():
            ver = self.settings_version()
            poked = _poked(self.db, last)
            with self.lock:
                later = set().union(*self.later.values())
            wanted = bool(later and later & self.demand())  # a job is waiting for something fetched on first use
            if ver != self.seen or poked or wanted or time.time() - last > CHECK_SECONDS:
                if ver != self.seen or poked:
                    self.failed_at.clear()  # new settings, or asked to: try again at once
                self.seen, last = ver, time.time()
                try:
                    self.check()
                except Exception as e:  # noqa: BLE001 - keep looking after the machine
                    if self.log:
                        self.log(f"components: {type(e).__name__}: {e}")
            stop.wait(5)

    def start(self, stop):
        # the first check before the worker takes jobs: steps whose models aren't here yet wait for them
        cfg = self.cfg_fn()
        activate(cfg)
        m = machine.probe(cfg["data_dir"])
        demand = self.demand()
        with self.lock:
            for c in self.wanted(cfg, m):
                try:
                    if not c.present(cfg) and (cfg.get("components") or {}).get("auto", True):
                        if wait := self._first_use(cfg, c, m, demand):
                            self.state[c.id], self.later[c.id] = {"state": "later"}, wait
                        else:
                            self.state[c.id] = {"state": "waiting"}
                except Exception:  # noqa: BLE001
                    pass
        th = threading.Thread(target=self.loop, args=(stop,), daemon=True, name=f"components-{self.name}")
        th.start()
        return th


def poke(db):
    """Ask every worker to check again now (after Install in the app)."""
    db.q("UPSERT $r SET at = $t", r=R("seq", "components"), t=time.time())


def _poked(db, since):
    at = db.values("SELECT VALUE at FROM $r", r=R("seq", "components"))
    return bool(at and at[0] and at[0] > since)


def catalog(cfg):
    """Every component, for the app: what it's for, whether this server's settings need it, and (for programs) whether
    the API's machine has it."""
    m = machine.probe(cfg["data_dir"])
    out = []
    for c in COMPONENTS:
        try:
            needed = c.needed(cfg, m)
        except Exception:  # noqa: BLE001
            needed = False
        row = {
            "id": c.id,
            "label": c.label,
            "purpose": c.purpose,
            "kind": c.kind,
            "steps": sorted(c.steps),
            "size_mb": c.size_mb,
            "optional": c.optional,
            "license": c.license,
            "needed": needed,
            "hint": c.hint,
        }
        if c.kind == "program":
            row["here"] = c.present(cfg)
        out.append(row)
    return out


def recommendation(cfg):
    return recommend(machine.probe(cfg["data_dir"]))
