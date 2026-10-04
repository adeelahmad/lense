"""A chat model run by Lens itself: a GGUF file from Hugging Face, served by llama.cpp (Settings › LLM provider ›
Run a model here; docs/local-models.md).

The catalog lists chat models in GGUF with what each needs; the ones this machine can run (memory, disk) are offered.
Picking one downloads it into data_dir/models/gguf (pinned to a revision and checked against its SHA-256), fetches
llama.cpp's server when it isn't installed (`llama-server` on PATH, local_llm.server, or llama.cpp's GitHub release
for this platform into data_dir/bin/llama.cpp), and runs it with its own API key. Once it answers, it becomes the LLM
provider (local_llm.use_as_provider); turning it off puts the provider that was there before back.

Any other GGUF on Hugging Face works too: local_llm.model "hf:<owner>/<repo>/<file>.gguf".

One process runs the server at a time, holding a lease on the `app_service:local_llm` row like the tunnel does, and
keeps its state there (downloading, starting, running, its address, the last lines it printed).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import platform
import re
import secrets
import shutil
import socket
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from . import machine, settings, store

R = store.R
ROW = R("app_service", "local_llm")
LEASE_SECONDS = 30
TICK_SECONDS = 5
RETRY = (5, 30, 120, 600)
LOG_LINES = 40
HF = "https://huggingface.co"
RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
GB = 1024**3
HF_REF = re.compile(r"^hf:([\w.-]+/[\w.-]+)/([\w./-]+\.gguf)$")
# llama.cpp's release builds, by (system, machine): the CPU (and, on a Mac, Metal) one
ASSETS = {
    ("linux", "x86_64"): re.compile(r"-bin-ubuntu-x64\.(zip|tar\.gz)$"),
    ("linux", "arm64"): re.compile(r"-bin-ubuntu-arm64\.(zip|tar\.gz)$"),
    ("darwin", "arm64"): re.compile(r"-bin-macos-arm64\.(zip|tar\.gz)$"),
    ("darwin", "x86_64"): re.compile(r"-bin-macos-x64\.(zip|tar\.gz)$"),
}


def _m(id_, label, repo, rev, file, size, sha, license_, about, tools=True):
    return {"id": id_, "label": label, "repo": repo, "revision": rev, "file": file, "size": size, "sha256": sha,
            "license": license_, "about": about, "tools": tools}  # fmt: skip


# Chat models in GGUF (Q4_K_M unless said), smallest first, pinned to the revision whose file was checked.
CATALOG = [
    _m("qwen2.5-0.5b", "Qwen2.5 0.5B Instruct", "Qwen/Qwen2.5-0.5B-Instruct-GGUF", "9217f5db79a29953eb74d5343926648285ec7e67",
       "qwen2.5-0.5b-instruct-q4_k_m.gguf", 491400032, "74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db",
       "apache-2.0", "Tiny and quick; fine for routine choices on a Raspberry Pi, weak at long answers."),
    _m("gemma-3-1b", "Gemma 3 1B", "ggml-org/gemma-3-1b-it-GGUF", "f9c28bcd85737ffc5aef028638d3341d49869c27",
       "gemma-3-1b-it-Q4_K_M.gguf", 806058240, "8ccc5cd1f1b3602548715ae25a66ed73fd5dc68a210412eea643eb20eb75a135",
       "gemma", "Small, many languages.", tools=False),
    _m("llama-3.2-1b", "Llama 3.2 1B Instruct", "bartowski/Llama-3.2-1B-Instruct-GGUF", "067b946cf014b7c697f3654f621d577a3e3afd1c",
       "Llama-3.2-1B-Instruct-Q4_K_M.gguf", 807694464, "6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83",
       "llama3.2", "Small, good at following instructions."),
    _m("smollm2-1.7b", "SmolLM2 1.7B Instruct", "HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF", "2d4a76a30b4af41ecd395c35725ac11688d4cfe4",
       "smollm2-1.7b-instruct-q4_k_m.gguf", 1055609536, "decd2598bc2c8ed08c19adc3c8fdd461ee19ed5708679d1c54ef54a5a30d4f33",
       "apache-2.0", "Small and open; English."),
    _m("qwen2.5-1.5b", "Qwen2.5 1.5B Instruct", "Qwen/Qwen2.5-1.5B-Instruct-GGUF", "91cad51170dc346986eccefdc2dd33a9da36ead9",
       "qwen2.5-1.5b-instruct-q4_k_m.gguf", 1117320736, "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
       "apache-2.0", "A good small all-rounder with tool calls; fits 4 GB machines."),
    _m("qwen3-1.7b", "Qwen3 1.7B (Q8_0)", "Qwen/Qwen3-1.7B-GGUF", "90862c4b9d2787eaed51d12237eafdfe7c5f6077",
       "Qwen3-1.7B-Q8_0.gguf", 1834426016, "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a",
       "apache-2.0", "Thinks before it answers; tool calls."),
    _m("llama-3.2-3b", "Llama 3.2 3B Instruct", "bartowski/Llama-3.2-3B-Instruct-GGUF", "5ab33fa94d1d04e903623ae72c95d1696f09f9e8",
       "Llama-3.2-3B-Instruct-Q4_K_M.gguf", 2019377696, "6c1a2b41161032677be168d354123594c0e6e67d2b9227c84f296ad037c728ff",
       "llama3.2", "Solid summaries on 8 GB machines."),
    _m("qwen2.5-3b", "Qwen2.5 3B Instruct", "Qwen/Qwen2.5-3B-Instruct-GGUF", "7dabda4d13d513e3e842b20f0d435c732f172cbe",
       "qwen2.5-3b-instruct-q4_k_m.gguf", 2104932768, "626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d",
       "qwen-research", "Good summaries and tool calls on 8 GB machines; research licence."),
    _m("gemma-3-4b", "Gemma 3 4B", "ggml-org/gemma-3-4b-it-GGUF", "d0976223747697cb51e056d85c532013931fe52e",
       "gemma-3-4b-it-Q4_K_M.gguf", 2489757856, "882e8d2db44dc554fb0ea5077cb7e4bc49e7342a1f0da57901c0802ea21a0863",
       "gemma", "Strong for its size, many languages.", tools=False),
    _m("phi-4-mini", "Phi-4 mini Instruct", "bartowski/microsoft_Phi-4-mini-instruct-GGUF", "7ff82c2aaa4dde30121698a973765f39be5288c0",
       "microsoft_Phi-4-mini-instruct-Q4_K_M.gguf", 2491874688, "01999f17c39cc3074afae5e9c539bc82d45f2dd7faa3917c66cbef76fce8c0c2",
       "mit", "Good at reasoning and English text."),
    _m("qwen3-4b", "Qwen3 4B", "Qwen/Qwen3-4B-GGUF", "bc640142c66e1fdd12af0bd68f40445458f3869b",
       "Qwen3-4B-Q4_K_M.gguf", 2497280256, "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5",
       "apache-2.0", "The best small model for the assistant's tools."),
    _m("qwen2.5-7b", "Qwen2.5 7B Instruct", "bartowski/Qwen2.5-7B-Instruct-GGUF", "8911e8a47f92bac19d6f5c64a2e2095bd2f7d031",
       "Qwen2.5-7B-Instruct-Q4_K_M.gguf", 4683074240, "65b8fcd92af6b4fefa935c625d1ac27ea29dcb6ee14589c55a8f115ceaaa1423",
       "apache-2.0", "Good everywhere; wants 16 GB or a GPU."),
    _m("llama-3.1-8b", "Llama 3.1 8B Instruct", "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF", "bf5b95e96dac0462e2a09145ec66cae9a3f12067",
       "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf", 4920739232, "7b064f5842bf9532c91456deda288a1b672397a54fa729aa665952863033557c",
       "llama3.1", "Good everywhere; wants 16 GB or a GPU."),
    _m("qwen3-8b", "Qwen3 8B", "Qwen/Qwen3-8B-GGUF", "7c41481f57cb95916b40956ab2f0b139b296d974",
       "Qwen3-8B-Q4_K_M.gguf", 5027783488, "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785",
       "apache-2.0", "Strong reasoning and tools; wants 16 GB or a GPU."),
    _m("gemma-3-12b", "Gemma 3 12B", "ggml-org/gemma-3-12b-it-GGUF", "ec0cbabd8dbff316f659876a50202295c3c4a314",
       "gemma-3-12b-it-Q4_K_M.gguf", 7300574976, "7bb69bff3f48a7b642355d64a90e481182a7794707b3133890646b1efa778ff5",
       "gemma", "Large; 16 GB at least.", tools=False),
    _m("mistral-nemo-12b", "Mistral Nemo 12B Instruct", "bartowski/Mistral-Nemo-Instruct-2407-GGUF", "a2dd64a0a76ea1bdb2bb6ab6fa5496b003c7c908",
       "Mistral-Nemo-Instruct-2407-Q4_K_M.gguf", 7477208192, "7c1a10d202d8788dbe5628dc962254d10654c853cae6aaeca0618f05490d4a46",
       "apache-2.0", "Large, long context; 16 GB at least."),
    _m("qwen3-14b", "Qwen3 14B", "Qwen/Qwen3-14B-GGUF", "530227a7d994db8eca5ab5ced2fb692b614357fd",
       "Qwen3-14B-Q4_K_M.gguf", 9001752960, "500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0",
       "apache-2.0", "Large; 24 GB or a GPU with 12 GB."),
]  # fmt: skip
BY_ID = {m["id"]: m for m in CATALOG}


class LocalModelError(RuntimeError):
    """The model or llama.cpp can't be had here; the message says what to do instead."""


# ---------- settings and the catalog ----------
def conf(cfg):
    return {**store.DEFAULTS["local_llm"], **(cfg.get("local_llm") or {})}


def resolve(ref):
    """A catalog entry for a catalog id or an "hf:<owner>/<repo>/<file>.gguf" reference."""
    if ref in BY_ID:
        return BY_ID[ref]
    m = HF_REF.match(ref or "")
    if not m:
        raise LocalModelError(f"unknown model {ref!r}: pick one from the list, or give hf:<owner>/<repo>/<file>.gguf")
    repo, file = m.groups()
    name = pathlib.PurePosixPath(file).name
    return {"id": ref, "label": name.removesuffix(".gguf"), "repo": repo, "revision": "main", "file": file, "size": None,
            "sha256": None, "license": None, "about": "From Hugging Face", "tools": True}  # fmt: skip


def memory_needed(size, context=4096):
    """GB of memory a model of this many bytes needs to run with this context: the weights, the context cache, and
    room for Lens itself."""
    return round(size / GB * 1.15 + context / 4096 * 0.5 + 1.0, 1)


def models_dir(cfg):
    return pathlib.Path(cfg["data_dir"]) / "models" / "gguf"


def model_path(cfg, m):
    return models_dir(cfg) / m["repo"].replace("/", "__") / pathlib.PurePosixPath(m["file"]).name


def catalog(cfg):
    """The catalog, with what this machine makes of each: downloaded, fits (memory), room (disk)."""
    mach = machine.probe(cfg["data_dir"])
    mem, disk, ctx = mach.get("memory_gb"), mach.get("disk_free_gb"), conf(cfg)["context"]
    vram = max([g.get("memory_gb") or 0 for g in mach.get("gpus") or []] or [0])
    out = []
    for m in CATALOG:
        need = memory_needed(m["size"], ctx)
        here = model_path(cfg, m).is_file()
        out.append(
            {
                **{k: m[k] for k in ("id", "label", "repo", "file", "license", "about", "tools")},
                "size_gb": round(m["size"] / GB, 2),
                "memory_gb": need,
                "downloaded": here,
                "fits": mem is None or need <= mem or (vram and m["size"] / GB * 1.15 <= vram),
                "room": here or disk is None or m["size"] / GB + 0.5 <= disk,
            }
        )
    return out, mach


def downloaded(cfg):
    """GGUF files in data_dir/models/gguf: [{path, size_gb}]."""
    root = models_dir(cfg)
    return (
        [{"path": str(p.relative_to(root)), "size_gb": round(p.stat().st_size / GB, 2)} for p in sorted(root.glob("*/*.gguf"))]
        if root.is_dir()
        else []
    )


def remove(cfg, ref):
    """Delete a downloaded model's file. Not the one running."""
    m = resolve(ref)
    if conf(cfg)["enabled"] and conf(cfg)["model"] == ref:
        raise LocalModelError("that model is running; pick another or turn the local model off first")
    p = model_path(cfg, m)
    if not p.is_file():
        return False
    p.unlink()
    for part in p.parent.glob("*.part"):
        part.unlink()
    if not any(p.parent.iterdir()):
        p.parent.rmdir()
    return True


# ---------- downloading ----------
def _open(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "lens", **(headers or {})})
    token = os.environ.get("HF_TOKEN")
    if token and urllib.parse.urlsplit(url).hostname == "huggingface.co":
        req.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=timeout)


def download(cfg, m, progress=None, stop=None):
    """The model's file into data_dir/models/gguf, resuming a part already there; checked against its SHA-256 when the
    catalog knows it. Returns the path."""
    dest = model_path(cfg, m)
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".gguf.part")
    have = tmp.stat().st_size if tmp.exists() else 0
    url = f"{HF}/{m['repo']}/resolve/{m['revision']}/{urllib.parse.quote(m['file'])}"
    free = shutil.disk_usage(dest.parent).free
    if m["size"] and m["size"] - have > free:
        raise LocalModelError(f"not enough disk for {m['label']}: needs {m['size'] / GB:.1f} GB, {free / GB:.1f} GB free")
    try:
        r = _open(url, {"Range": f"bytes={have}-"} if have else None)
    except urllib.error.HTTPError as e:
        if e.code == 416:  # the part is already whole
            r = None
        else:
            raise LocalModelError(
                f"Hugging Face said {e.code} for {m['repo']}/{m['file']}"
                + (" (a gated model: set HF_TOKEN)" if e.code in (401, 403) else "")
            ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LocalModelError(f"can't reach Hugging Face: {getattr(e, 'reason', e)}") from None
    if r is not None:
        with r:
            if have and r.status != 206:
                have = 0  # the server sent the whole file
            total = have + int(r.headers.get("Content-Length") or 0) or m["size"]
            last = 0.0
            with open(tmp, "ab" if have else "wb") as f:
                done = have
                while chunk := r.read(1 << 20):
                    if stop is not None and stop():
                        raise LocalModelError("stopped")
                    f.write(chunk)
                    done += len(chunk)
                    if progress and time.monotonic() - last > 1:
                        progress(done, total)
                        last = time.monotonic()
            if progress:
                progress(done, total)
    if m["sha256"]:
        h = hashlib.sha256()
        with open(tmp, "rb") as f:
            while chunk := f.read(1 << 22):
                h.update(chunk)
        if h.hexdigest() != m["sha256"]:
            tmp.unlink()
            raise LocalModelError(f"the download of {m['label']} didn't match its checksum; it will be fetched again")
    tmp.replace(dest)
    return dest


# ---------- llama.cpp ----------
def _platform():
    system = platform.system().lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower(), platform.machine())
    return system, arch


def server_binary(cfg):
    """llama.cpp's server: local_llm.server, llama-server on PATH, or the copy Lens fetched; None when there's none."""
    own = conf(cfg).get("server")
    if own:
        return own if os.access(own, os.X_OK) else None
    found = shutil.which("llama-server")
    if found:
        return found
    root = pathlib.Path(cfg["data_dir"]) / "bin" / "llama.cpp"
    hit = next((p for p in sorted(root.rglob("llama-server")) if p.is_file()), None) if root.is_dir() else None
    return str(hit) if hit else None


def fetch_server(cfg, say=None):
    """llama.cpp's latest release build for this platform into data_dir/bin/llama.cpp. Returns llama-server's path."""
    want = ASSETS.get(_platform())
    if not want:
        raise LocalModelError(
            f"no llama.cpp build to fetch for {' '.join(_platform())}: install llama.cpp (llama-server on PATH) or set local_llm.server"
        )
    try:
        with _open(RELEASES_API, {"Accept": "application/vnd.github+json"}, timeout=30) as r:
            rel = json.load(r)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise LocalModelError(f"can't reach llama.cpp's releases on GitHub: {getattr(e, 'reason', e)}") from None
    asset = next((a for a in rel.get("assets") or [] if want.search(a.get("name", ""))), None)
    if not asset:
        raise LocalModelError(f"llama.cpp {rel.get('tag_name')} has no build for {' '.join(_platform())}; install llama-server yourself")
    root = pathlib.Path(cfg["data_dir"]) / "bin" / "llama.cpp"
    tmp = root.parent / asset["name"]
    root.parent.mkdir(parents=True, exist_ok=True)
    if say:
        say(f"downloading llama.cpp {rel.get('tag_name')} ({asset['name']})")
    try:
        with _open(asset["browser_download_url"], timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise LocalModelError(f"couldn't download llama.cpp: {getattr(e, 'reason', e)}") from None
    if root.exists():
        shutil.rmtree(root)
    root.mkdir()
    try:
        if asset["name"].endswith(".zip"):
            with zipfile.ZipFile(tmp) as z:
                for info in z.infolist():
                    target = (root / info.filename).resolve()
                    if root.resolve() not in target.parents and target != root.resolve():
                        continue  # nothing outside the folder
                    z.extract(info, root)
                    mode = info.external_attr >> 16
                    if mode:
                        os.chmod(target, mode & 0o777 or 0o644)
        else:
            with tarfile.open(tmp) as t:
                t.extractall(root, filter="data")
    finally:
        tmp.unlink(missing_ok=True)
    exe = next((p for p in sorted(root.rglob("llama-server")) if p.is_file()), None)
    if not exe:
        raise LocalModelError("the llama.cpp download has no llama-server in it")
    exe.chmod(0o755)
    return str(exe)


def address(cfg):
    """(host to bind, host others reach it at). In a container, other containers (workers) reach it at its address."""
    own = conf(cfg).get("host")
    if machine.probe().get("container"):
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip = "127.0.0.1"
        return "0.0.0.0", own or ip  # noqa: S104 - other containers call it; it has its own API key
    return "127.0.0.1", own or "127.0.0.1"


# ---------- state ----------
def _now():
    return dt.datetime.now(dt.timezone.utc)


def _ts(t=None):
    return (t or _now()).isoformat()


def state(db):
    row = db.one("SELECT * FROM $r", r=ROW) or {}
    row.pop("id", None)
    if isinstance(row.get("log"), str):
        row["log"] = json.loads(row["log"])
    return row


def status(db, cfg):
    s, c = state(db), conf(cfg)
    live = bool(s.get("at") and s["at"] >= _ts(_now() - dt.timedelta(seconds=LEASE_SECONDS)))
    models, mach = catalog(cfg)
    return {
        "enabled": c["enabled"],
        "model": c["model"],
        "phase": (s.get("phase") or "off") if live and c["enabled"] else "off",
        "progress": s.get("progress") if live else None,
        "url": s.get("url") if live and s.get("phase") == "running" else None,
        "error": s.get("error") if c["enabled"] else None,
        "log": s.get("log") or [],
        "process": s.get("owner") if live else None,
        "server": server_binary(cfg),
        "catalog": models,
        "files": downloaded(cfg),
        "machine": {k: mach.get(k) for k in ("os", "arch", "cpus", "memory_gb", "disk_free_gb", "apple_silicon", "cuda")},
    }


class Runner:
    """Holds the lease and runs llama-server with the picked model while local_llm.enabled is on."""

    def __init__(self, db, cfg_fn, name, log=None):
        self.db, self.cfg_fn, self.name, self.log = db, cfg_fn, name, log or (lambda *a: None)
        self.proc: subprocess.Popen | None = None
        self.sig = None
        self.lines: list[str] = []
        self.phase, self.error, self.progress, self.url = "off", None, None, None
        self.fails, self.next_try = 0, 0.0
        self.worker: threading.Thread | None = None
        self.lock = threading.Lock()
        self.halt = False

    # the lease and the row
    def _lease(self):
        now = _now()
        return bool(
            self.db.rows(
                "UPSERT $r MERGE {owner: $me, at: $t} WHERE owner = NONE OR owner = $me OR at < $stale RETURN AFTER",
                r=ROW,
                me=self.name,
                t=_ts(now),
                stale=_ts(now - dt.timedelta(seconds=LEASE_SECONDS)),
            )
        )

    def _save(self, **extra):
        with self.lock:
            d = {"phase": self.phase, "error": self.error, "progress": self.progress, "url": self.url,
                 "log": json.dumps(self.lines[-LOG_LINES:]), **extra}  # fmt: skip
        self.db.q("UPDATE $r MERGE $d WHERE owner = $me", r=ROW, d=d, me=self.name)

    def _say(self, line):
        with self.lock:
            self.lines = (self.lines + [str(line)[:300]])[-LOG_LINES:]

    def _key(self, cfg):
        """The server's API key, made once and kept sealed."""
        s = state(self.db)
        if s.get("key"):
            try:
                return settings.unseal(cfg, s["key"], "local_llm.key")
            except Exception:  # noqa: BLE001 - the secret key changed: make a new one
                pass
        key = "lens-" + secrets.token_urlsafe(24)
        self.db.q("UPDATE $r SET key = $k WHERE owner = $me", r=ROW, k=settings.seal(cfg, key, "local_llm.key"), me=self.name)
        return key

    # the LLM provider
    def _point_provider(self, cfg, url, model_name, key):
        """Make this server the LLM provider, keeping what was there to put back later."""
        if not conf(cfg)["use_as_provider"]:
            return
        llm = cfg.get("llm") or {}
        if llm.get("base_url") == url and llm.get("model") == model_name and llm.get("api_key") == key:
            return
        s = state(self.db)
        if "previous" not in s and llm.get("base_url") != url:
            prev = {"base_url": llm.get("base_url"), "model": llm.get("model")}
            if llm.get("api_key"):
                prev["api_key"] = settings.seal(cfg, llm["api_key"], "local_llm.previous")
            self.db.q("UPDATE $r SET previous = $p WHERE owner = $me", r=ROW, p=prev, me=self.name)
        settings.save(self.db, cfg, "llm", {"base_url": url, "model": model_name, "api_key": key}, "local model")
        self.log("local model: the LLM provider is now %s at %s", model_name, url)

    def _restore_provider(self, cfg):
        s = state(self.db)
        prev = s.get("previous")
        if prev is None:
            return
        llm = cfg.get("llm") or {}
        if s.get("url") and llm.get("base_url") == s.get("url"):  # still ours: put back what was there
            key = None
            if prev.get("api_key"):
                try:
                    key = settings.unseal(cfg, prev["api_key"], "local_llm.previous")
                except Exception:  # noqa: BLE001
                    key = None
            settings.save(
                self.db, cfg, "llm", {"base_url": prev.get("base_url"), "model": prev.get("model"), "api_key": key or ""}, "local model"
            )
        self.db.q("UPDATE $r SET previous = NONE WHERE owner = $me", r=ROW, me=self.name)

    # the process
    def stop_process(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()

    def _read(self, proc):
        for raw in proc.stdout:
            line = raw.rstrip()
            if line:
                self._say(line)
        code = proc.wait()
        if self.proc is proc:
            self.error = f"llama-server stopped (exit {code}); {self.lines[-1] if self.lines else 'no output'}"
            self.phase = "error"
            self.fails += 1
            self.next_try = time.monotonic() + RETRY[min(self.fails - 1, len(RETRY) - 1)]
            self.proc = None
            self._save()

    def _prepare(self, cfg, m, sig):
        """Fetch what's missing and start the server (in a thread: downloads take a while)."""
        try:
            exe = server_binary(cfg)
            if not exe:
                self.phase = "fetching-server"
                self._save()
                exe = fetch_server(cfg, say=self._say)
            path = model_path(cfg, m)
            if not path.is_file():
                self.phase = "downloading"
                self._say(f"downloading {m['repo']}/{m['file']}")
                self._save()

                def progress(done, total):
                    self.progress = {"done": done, "total": total}
                    self._save()

                download(cfg, m, progress, stop=lambda: self.halt or self.sig != sig)
                self.progress = None
            if self.halt or self.sig != sig:
                return
            c = conf(cfg)
            bind, host = address(cfg)
            key = self._key(cfg)
            cmd = [exe, "-m", str(path), "--host", bind, "--port", str(c["port"]), "-c", str(c["context"]), "--alias", m["id"],
                   "--jinja", "-ngl", str(c["gpu_layers"])]  # fmt: skip
            if c.get("threads"):
                cmd += ["-t", str(c["threads"])]
            env = dict(os.environ)
            env["LLAMA_ARG_API_KEY"] = key  # llama-server's --api-key, not on the command line where `ps` shows it
            lib = str(pathlib.Path(exe).parent)
            env["LD_LIBRARY_PATH"] = lib + (os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
            self.phase = "starting"
            self._save()
            self.proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            threading.Thread(target=self._read, args=(self.proc,), daemon=True, name="llama-server-log").start()
            url = f"http://{host}:{c['port']}/v1"
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline and self.proc and self.proc.poll() is None and not self.halt:
                try:
                    with _open(f"http://127.0.0.1:{c['port']}/health", timeout=5) as r:
                        if r.status == 200:
                            break
                except (urllib.error.URLError, OSError):
                    pass
                time.sleep(1)
            if not (self.proc and self.proc.poll() is None):
                return
            self.url, self.phase, self.error, self.fails = url, "running", None, 0
            self._save()
            self._point_provider(self.cfg_fn(), url, m["id"], key)
        except (LocalModelError, OSError) as e:
            self.error, self.phase = str(e), "error"
            self.fails += 1
            self.next_try = time.monotonic() + RETRY[min(self.fails - 1, len(RETRY) - 1)]
            self._save()

    def tick(self):
        cfg = self.cfg_fn()
        c = conf(cfg)
        if not c["enabled"] or not c["model"]:
            if self.proc or self.phase != "off":
                if self._lease():
                    self._restore_provider(cfg)
                self.stop_process()
                self.sig, self.phase, self.url, self.error, self.progress = None, "off", None, None, None
                self._save()
            return
        if not self._lease():
            if self.proc:
                self.stop_process()
            return
        sig = json.dumps([c["model"], c["port"], c["context"], c["threads"], c["gpu_layers"], c["server"], c["host"]])
        if sig != self.sig:
            self.stop_process()
            self.sig, self.fails, self.next_try, self.url, self.error, self.lines = sig, 0, 0.0, None, None, []
        busy = self.worker is not None and self.worker.is_alive()
        if busy or (self.proc and self.proc.poll() is None):
            self._save()
            return
        if time.monotonic() < self.next_try:
            self._save()
            return
        try:
            m = resolve(c["model"])
        except LocalModelError as e:
            self.error, self.phase = str(e), "error"
            self._save()
            return
        self.worker = threading.Thread(target=self._prepare, args=(cfg, m, sig), daemon=True, name="local-llm")
        self.worker.start()

    def loop(self, stop):
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - keep the runner alive; the error shows in Settings
                self.error = f"{type(e).__name__}: {e}"
                try:
                    self._save()
                except Exception:  # noqa: BLE001
                    pass
            stop.wait(TICK_SECONDS)
        self.halt = True
        self.stop_process()
        self.db.q("UPDATE $r SET owner = NONE, phase = 'off' WHERE owner = $me", r=ROW, me=self.name)


def start(db, cfg_fn, stop, name, log=None):
    r = Runner(db, cfg_fn, name, log)
    threading.Thread(target=r.loop, args=(stop,), daemon=True, name="local-llm-runner").start()
    return r
