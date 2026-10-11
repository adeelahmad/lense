#!/usr/bin/env python3
"""Lens's web installer: the install, in the browser.

install.sh starts this on a first install at a terminal (or with LENS_WIZARD=1). It serves a small wizard on this
machine that shows what the computer has and what Lens will install, asks how Lens should run, the AI model and its
key, and the optional extras (search by meaning, the decision model, cloud transcription, email, remote access,
anytopdf), then runs install.sh with those answers (LENS_WIZARD=0, so it asks nothing) and shows its progress, and
finally hands over the web app's setup page.

Python 3.8+ and its standard library only (this file and page.html), so install.sh can fetch it and run it anywhere Lens runs (a
Raspberry Pi included). The answers that are app settings travel as LENS_SETUP_SETTINGS, which Lens saves once as if
they were made in the app (fastapi_backend/app/domain/setup.py), so they stay editable there; that line is taken out
of .env again once Lens has started.

Only someone holding the link it prints can use it: the link carries a one-time token, kept in a cookie, and every
change also needs a header a page from elsewhere can't send. An administrator password, when the install needs one,
is kept in memory for the run and handed to sudo through SUDO_ASKPASS; it is never written down.

  python3 lens_installer.py [--dir ~/lens] [--repo adeelahmad/lense] [--ref main] [--port 3990] [--no-browser]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = 1
WINDOWS = sys.platform == "win32"
COOKIE = "lens_installer"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
NS_RX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
REF_RX = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
REPO_RX = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# Model servers people run themselves, on their usual ports (as the web app's own setup looks for them)
LOCAL_SERVERS = (("Ollama", 11434), ("LM Studio", 1234), ("llama.cpp", 8080), ("vLLM", 8000), ("LocalAI", 8081))
NOT_CHAT = ("embed", "rerank", "whisper", "tts", "clip", "bge-", "minilm")
# The settings the wizard may send, by section: what Lens itself accepts there (settings.EDITABLE), kept short
SETTINGS = {
    "llm": {"base_url", "model", "api_key", "vision_model"},
    "embeddings": {"enabled", "base_url", "model", "api_key"},
    "decisions": {"api_key", "engine"},
    "transcribe": {"engine"},
    "speech": {"openai_api_key", "elevenlabs_api_key", "assemblyai_api_key", "deepgram_api_key"},
    "mail": {"server", "port", "username", "password", "from_address", "security"},
    "tunnel": {"mode", "token", "hostname", "api_token"},
    "documents": {"anytopdf_url", "anytopdf_token"},
    "telemetry": {"enabled", "endpoint"},
    # not a settings section: Lens's overall budget (Settings › Budgets), with the chat model's price
    "budget": {"hard_usd", "soft_usd", "period", "on_over", "price_in", "price_out"},
}
# Things the installer can do for an existing install besides installing again, by id: {"label", "detail", "run":
# fn(installer) -> None}. Empty for now: the upgrade and backup work adds its actions here, and the page shows a
# button for each.
ACTIONS: dict = {}
# Answers that go to install.sh as they are (it writes them to .env), with what they may be
PASSED = ("LENS_BACKEND", "LENS_TARGET", "LENS_PORT", "LENS_PUBLIC", "LENS_REF", "LENS_DIR", "LENS_NAMESPACE")
# .env keys shown back for an existing install (never the secrets)
SHOWN_ENV = ("LENS_BACKEND", "LENS_TARGET", "LENS_PORT", "LENS_BIND", "FRONTEND_URL")


def have(cmd):
    return shutil.which(cmd) is not None


def run(args, timeout=15, **kw):
    """Exit status and output of a command; 127 when it isn't there or doesn't finish."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, **kw)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def port_free(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) != 0


def first_free(start, stop):
    return next((p for p in range(start, stop) if port_free(p)), None)


def read_env(path):
    """The KEY=value lines of a .env file."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                k, sep, v = line.rstrip("\n").partition("=")
                if sep and re.match(r"^[A-Z_][A-Z0-9_]*$", k):
                    out[k] = v
    except OSError:
        pass
    return out


def drop_env(path, key):
    """Take one line out of a .env file, keeping its mode."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False
    keep = [line for line in lines if not line.startswith(key + "=")]
    if keep == lines:
        return False
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(keep)
    os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
    os.replace(tmp, path)
    return True


def memory_mb():
    try:
        if sys.platform == "darwin":
            return int(run(["sysctl", "-n", "hw.memsize"])[1].strip()) // 1048576
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return None


def free_gb(path):
    while path and not os.path.exists(path):
        path = os.path.dirname(path)
    try:
        return round(shutil.disk_usage(path or "/").free / 1e9, 1)
    except OSError:
        return None


def os_name():
    if sys.platform == "darwin":
        return "macOS " + platform.mac_ver()[0]
    if WINDOWS:
        build = platform.version().rsplit(".", 1)[-1]
        return "Windows " + ("11" if build.isdigit() and int(build) >= 22000 else platform.release())
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system() + " " + platform.release()


def board():
    """A Raspberry Pi (or another board) says what it is here."""
    try:
        with open("/proc/device-tree/model", "rb") as f:
            return f.read().rstrip(b"\0").decode(errors="replace")
    except OSError:
        return None


def desktop():
    return WINDOWS or sys.platform == "darwin" or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def msb_ok():
    """Whether microsandbox can run here (as install.sh decides): KVM on 64-bit Linux, Apple silicon on a Mac."""
    machine = platform.machine()
    if sys.platform.startswith("linux") and machine == "x86_64":
        if os.path.exists("/dev/kvm"):
            return True
        try:
            with open("/proc/cpuinfo") as f:
                return bool(re.search(r"\b(vmx|svm)\b", f.read()))
        except OSError:
            return False
    if sys.platform.startswith("linux") and machine == "aarch64":
        return os.path.exists("/dev/kvm")
    return sys.platform == "darwin" and machine == "arm64"


def sudo_state():
    """root, passwordless (sudo works without asking), password (sudo asks), or none (no sudo)."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return "root"
    if WINDOWS or not have("sudo"):  # Windows asks for permission itself (UAC)
        return "none"
    return "passwordless" if run(["sudo", "-n", "true"], timeout=5)[0] == 0 else "password"


def docker_facts():
    facts = {"installed": have("docker"), "running": False, "needs_sudo": False, "compose": False}
    if not facts["installed"]:
        return facts
    if run(["docker", "info"], timeout=20)[0] == 0:
        facts["running"] = True
    elif sys.platform.startswith("linux") and run(["sudo", "-n", "docker", "info"], timeout=20)[0] == 0:
        facts["running"] = facts["needs_sudo"] = True  # not in the docker group yet: install.sh uses sudo for this run
    elif sys.platform.startswith("linux"):
        facts["needs_sudo"] = True  # stopped, or only root may use it: sudo finds out
    prefix = ["sudo", "-n"] if facts["needs_sudo"] and facts["running"] else []
    facts["compose"] = run(prefix + ["docker", "compose", "version"])[0] == 0 or have("docker-compose")
    return facts


def plan(backend, docker, sudo):
    """What install.sh will install for a backend, as [{what, admin}], admin: needs the administrator password.
    Mirrors install.sh's own list."""
    items = []
    linux = sys.platform.startswith("linux")
    asks = sudo == "password"
    if backend == "microsandbox":
        if not have("msb") and not os.path.exists(os.path.expanduser("~/.microsandbox/bin/msb")):
            items.append({"what": "microsandbox (from install.microsandbox.dev, into your home folder)", "admin": False})
        if linux:
            if not os.path.exists("/dev/kvm"):
                items.append({"what": "the KVM kernel module", "admin": asks})
            elif not (os.access("/dev/kvm", os.R_OK) and os.access("/dev/kvm", os.W_OK)):
                items.append({"what": "access to KVM for you (the kvm group)", "admin": asks})
            if os.path.isdir("/run/systemd/system"):
                items.append({"what": "a service that starts Lens with the computer", "admin": asks, "optional": True})
        return items
    if WINDOWS:
        if not docker["installed"]:
            items.append({"what": "Docker Desktop (with winget; Windows asks for permission)", "admin": False})
            if run(["wsl", "--status"], timeout=20)[0] != 0:
                items.append({"what": "WSL, which Docker Desktop runs on (Windows may need a restart)", "admin": False})
        return items
    if not docker["installed"]:
        if linux:
            items.append({"what": "Docker Engine with Compose (from get.docker.com)", "admin": asks})
        elif sys.platform == "darwin":
            if not have("brew"):
                items.append({"what": "Homebrew (from brew.sh)", "admin": sudo != "root"})
            items.append({"what": "OrbStack, which runs Docker on a Mac (with Homebrew)", "admin": False})
    else:
        if linux and docker["needs_sudo"] and sudo != "root":
            items.append({"what": "using Docker as administrator for this install (you're not in the docker group)", "admin": asks})
        if not docker["compose"] and linux:
            items.append({"what": "the Docker Compose plugin", "admin": asks})
    return items


def _get_models(base_url, api_key=None, timeout=4.0):
    """The model ids an OpenAI-compatible server lists, or raises with why not."""
    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "lens-installer"})
    if api_key:
        req.add_header("Authorization", "Bearer " + api_key)
    host = urllib.parse.urlsplit(url).hostname or ""
    local = host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as r:
        data = json.load(r).get("data")
    if not isinstance(data, list):
        raise ValueError("that address answers, but not with a list of models")
    return sorted({str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")})


def sort_models(models):
    chat = [m for m in models if not any(w in m.lower() for w in NOT_CHAT)]
    return chat + [m for m in models if m not in chat]


def detect_models():
    """Model servers running on this machine, with their models (chat models first)."""

    def probe(server):
        kind, port = server
        try:
            return kind, f"http://localhost:{port}/v1", sort_models(_get_models(f"http://127.0.0.1:{port}/v1", timeout=1.5))
        except Exception:  # noqa: BLE001 - nothing there
            return None

    with ThreadPoolExecutor(max_workers=len(LOCAL_SERVERS)) as pool:
        return [{"kind": k, "base_url": u, "models": m} for k, u, m in filter(None, pool.map(probe, LOCAL_SERVERS)) if m]


def for_lens(url):
    """An address on this machine as Lens reaches it from its container or sandbox."""
    if not isinstance(url, str):
        return url
    return re.sub(r"^(https?://)(localhost|127\.0\.0\.1|\[::1\])(?=[:/]|$)", r"\1host.docker.internal", url.strip())


def clean_settings(raw):
    """The settings the wizard sent, kept to what SETTINGS allows; empty values left out (unset). Addresses on this
    machine are rewritten so Lens reaches them."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for section, values in raw.items():
        if section not in SETTINGS or not isinstance(values, dict):
            continue
        keep = {}
        for k, v in values.items():
            if k not in SETTINGS[section] or v is None or (isinstance(v, str) and not v.strip()):
                continue
            if not isinstance(v, (str, int, float, bool)):
                continue
            if isinstance(v, str):
                v = v.strip()
                if k.endswith("url") or k in ("base_url", "endpoint"):
                    v = for_lens(v)
            keep[k] = v
        if keep:
            out[section] = keep
    return out


def encode_settings(settings):
    return base64.urlsafe_b64encode(json.dumps(settings, separators=(",", ":")).encode()).decode().rstrip("=")


def validate(answers, facts):
    """The install's environment from the wizard's answers, or raises ValueError with what's wrong."""
    a = answers if isinstance(answers, dict) else {}
    backend = a.get("backend") or "docker"
    if backend not in ("docker", "microsandbox"):
        raise ValueError("Lens runs with Docker or microsandbox")
    if backend == "microsandbox" and not facts.get("msb_ok"):
        raise ValueError("microsandbox can't run on this computer; choose Docker")
    target = a.get("target") or "full"
    if target not in ("full", "lean"):
        raise ValueError("the edition is full or lean")
    try:
        port = int(a.get("port") or 3000)
    except (TypeError, ValueError):
        raise ValueError("the port is a number") from None
    if not 1024 <= port <= 65535:
        raise ValueError("choose a port between 1024 and 65535")
    folder = os.path.abspath(os.path.expanduser(str(a.get("dir") or facts["dir"])))
    lens_there = any(os.path.exists(os.path.join(folder, f)) for f in (".git", "docker-compose.prod.yml"))
    if os.path.exists(folder) and os.listdir(folder) and not lens_there:
        raise ValueError(f"{folder} has other files in it; choose an empty or new folder")
    ref = str(a.get("ref") or facts["ref"])
    if not REF_RX.match(ref):
        raise ValueError("the version is a branch or tag name")
    ns = str(a.get("namespace") or "").strip().lower()
    if ns and not NS_RX.match(ns):
        raise ValueError("namespace names use lowercase letters, digits, - and _")
    env = {
        "LENS_BACKEND": backend,
        "LENS_TARGET": target,
        "LENS_PORT": str(port),
        "LENS_PUBLIC": "1" if a.get("public") else "0",
        "LENS_REF": ref,
        "LENS_DIR": folder,
    }
    if ns:
        env["LENS_NAMESPACE"] = ns
    settings = clean_settings(a.get("settings"))
    if settings:
        env["LENS_SETUP_SETTINGS"] = encode_settings(settings)
    return env


def step_of(line):
    """install.sh's headline for a line it printed in bold (its say), if it is one."""
    return ANSI.sub("", line).strip() if line.startswith("\x1b[1m") else None


# install.sh prints this, then the last lines Lens logged, when it stops waiting for Lens; then why (the last line)
TAIL_HEAD = "The last lines Lens logged:"


def log_tail(lines, error=None):
    """The last lines Lens logged, as install.sh showed them when it stopped waiting, without its reason after them."""
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == TAIL_HEAD:
            tail = lines[i + 1 :]
            while tail and (not tail[-1].strip() or tail[-1] == error):
                tail.pop()
            return tail
    return []


class Installer:
    def __init__(self, folder, repo, ref, script=None, token=None):
        self.dir, self.repo, self.ref, self.script = folder, repo, ref, script
        self.token = token or secrets.token_urlsafe(18)
        self.lock = threading.Lock()
        self.lines, self.steps = [], []
        self.state, self.url, self.error, self.code = "idle", None, None, None
        self.tail = []
        self.password = None
        self.proc = None
        self.tmp = tempfile.mkdtemp(prefix="lens-installer-")
        self.done_at = None
        self._facts = None

    # --- What this computer has --------------------------------------------------------------------------------
    def facts(self, refresh=False):
        if self._facts and not refresh:
            return self._facts
        sudo = sudo_state()
        docker = docker_facts()
        existing = os.path.isfile(os.path.join(self.dir, ".env"))
        env = read_env(os.path.join(self.dir, ".env")) if existing else {}
        port = int(env["LENS_PORT"]) if env.get("LENS_PORT", "").isdigit() else (first_free(3000, 3100) or 3000)
        self._facts = {
            "version": VERSION,
            "os": sys.platform,
            "os_name": os_name(),
            "arch": platform.machine(),
            "board": board(),
            "cpus": os.cpu_count(),
            "memory_mb": memory_mb(),
            "disk_free_gb": free_gb(self.dir),
            "desktop": desktop(),
            "user": os.environ.get("USER") or os.environ.get("USERNAME") or "",
            "windows": WINDOWS,
            "supported": WINDOWS or sys.platform == "darwin" or sys.platform.startswith("linux"),
            "git": have("git"),
            "curl": have("curl"),
            "docker": docker,
            "msb_ok": msb_ok(),
            "msb_installed": have("msb") or os.path.exists(os.path.expanduser("~/.microsandbox/bin/msb")),
            # what to offer first: Lens.app, which carries microsandbox, asks for microsandbox (desktop/README.md)
            "backend_default": "microsandbox" if os.environ.get("LENS_BACKEND_DEFAULT") == "microsandbox" and msb_ok() else "docker",
            "sudo": sudo,
            "plan": {"docker": plan("docker", docker, sudo), "microsandbox": plan("microsandbox", docker, sudo)},
            "dir": self.dir,
            "repo": self.repo,
            "ref": self.ref,
            "existing": {k: env[k] for k in SHOWN_ENV if k in env} if existing else None,
            "port": port,
            "public": (env.get("LENS_BIND") == "0.0.0.0") if existing else not desktop(),
            "actions": [{"id": k, "label": v["label"], "detail": v.get("detail", "")} for k, v in ACTIONS.items()],
        }
        return self._facts

    # --- The administrator password, for sudo ----------------------------------------------------------------------
    def check_password(self, password):
        if not isinstance(password, str) or not password or "\n" in password:
            return False
        try:
            p = subprocess.run(["sudo", "-S", "-k", "-p", "", "-v"], input=password + "\n", capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if p.returncode != 0:
            return False
        self.password = password
        return True

    def _askpass(self):
        path = os.path.join(self.tmp, "askpass")
        with open(path, "w") as f:
            f.write("#!/bin/sh\nprintf '%s\\n' \"$LENS_SUDO_PASSWORD\"\n")
        os.chmod(path, 0o700)
        return path

    # --- The script ------------------------------------------------------------------------------------------------
    def _script(self):
        if self.script and os.path.isfile(self.script):
            return self.script
        if WINDOWS:  # no sh: the same steps in Python, next to this file (install.ps1 fetches it too)
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), "install_windows.py")
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "install.sh")
        if os.path.isfile(here) and not os.environ.get("LENS_INSTALLER_FETCH"):
            return os.path.abspath(here)  # run from a checkout: its own install.sh
        url = f"https://raw.githubusercontent.com/{self.repo}/{self.ref}/install.sh"
        path = os.path.join(self.tmp, "install.sh")
        req = urllib.request.Request(url, headers={"User-Agent": "lens-installer"})
        with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
            f.write(r.read())
        return path

    def _say(self, line):
        with self.lock:
            self.lines.append(line)
            if len(self.lines) > 5000:
                del self.lines[:1000]
                self._dropped = getattr(self, "_dropped", 0) + 1000

    def start(self, answers):
        with self.lock:
            if self.state == "running":
                raise ValueError("the install is already running")
        env_vars = validate(answers, self.facts())
        backend = env_vars["LENS_BACKEND"]
        wants_admin = any(i["admin"] and not i.get("optional") for i in self.facts()["plan"][backend])
        if wants_admin and not self.password:
            raise ValueError("the install needs your computer's administrator password")
        self.dir = env_vars["LENS_DIR"]
        with self.lock:
            self.lines, self.steps, self.state, self.url, self.error, self.code = [], [], "running", None, None, None
            self.tail = []
        threading.Thread(target=self._run, args=(env_vars,), daemon=True, name="install").start()

    def _run(self, env_vars):
        env = dict(os.environ)
        env.update(env_vars)
        env.update({"LENS_WIZARD": "0", "LENS_YES": "1", "LENS_NO_OPEN": "1", "LENS_REPO": self.repo})
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})  # install_windows.py's output, as it comes
        if self.password:
            env["SUDO_ASKPASS"] = self._askpass()
            env["LENS_SUDO_PASSWORD"] = self.password
        try:
            script = self._script()
        except (OSError, urllib.error.URLError) as e:
            self._finish("failed", f"Couldn't download install.sh from GitHub: {e}")
            return
        try:
            # a session of its own: no terminal, so nothing it runs can stop to ask there
            args = [sys.executable, script] if script.endswith(".py") else ["sh", script]
            extra = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if WINDOWS else {"start_new_session": True}
            self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, **extra)
        except OSError as e:
            self._finish("failed", f"Couldn't start install.sh: {e}")
            return
        assert self.proc.stdout is not None
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            step = step_of(line)
            text = ANSI.sub("", line)
            with self.lock:
                if step:
                    self.steps.append(step)
                m = re.search(r"Lens is running: (\S+)", text)
                if m:
                    self.url = m.group(1)
            self._say(text)
        code = self.proc.wait()
        self.code = code
        if code == 0 and self.url:
            # Lens has saved the installer's settings by now: the keys needn't stay in .env
            drop_env(os.path.join(self.dir, ".env"), "LENS_SETUP_SETTINGS")
            self._finish("done")
        else:
            last = next((ln for ln in reversed(self.lines) if ln.strip()), "")
            with self.lock:
                self.tail = log_tail(self.lines, last)
            self._finish("failed", last or f"install.sh stopped (exit {code})")

    def _finish(self, state, error=None):
        with self.lock:
            self.state, self.error, self.done_at = state, error, time.time()
        self.password = None

    def progress(self, since=0):
        with self.lock:
            offset = getattr(self, "_dropped", 0)
            start = max(0, since - offset)
            return {
                "state": self.state,
                "steps": list(self.steps),
                "lines": self.lines[start:],
                "next": offset + len(self.lines),
                "url": self.url,
                "error": self.error,
                "tail": list(self.tail),
            }


PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")


def page():
    with open(PAGE_PATH, encoding="utf-8") as f:  # install.sh fetches it next to this file
        return f.read()


def make_handler(inst, allowed_hosts, stop):
    class Handler(BaseHTTPRequestHandler):
        server_version = "lens-installer"

        def log_message(self, *args):  # quiet: the terminal shows the link and the result
            pass

        def _host_ok(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            if host in allowed_hosts:
                return True
            try:
                socket.inet_pton(socket.AF_INET6 if ":" in host else socket.AF_INET, host)
                return "*" in allowed_hosts  # reachable from the network: any address of this machine
            except OSError:
                return False  # a name that isn't this machine's: DNS rebinding

        def _authed(self):
            cookie = self.headers.get("Cookie") or ""
            m = re.search(COOKIE + r"=([A-Za-z0-9_-]+)", cookie)
            return bool(m) and secrets.compare_digest(m.group(1), inst.token)

        def _send(self, status, body, ctype="application/json", extra=None):
            data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if not ctype.endswith("json") else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:",
            )
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._host_ok():
                return self._send(HTTPStatus.FORBIDDEN, {"error": "unknown host"})
            url = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(url.query)
            if url.path == "/" and q.get("t"):
                if not secrets.compare_digest(q["t"][0], inst.token):
                    return self._send(HTTPStatus.FORBIDDEN, "This link has expired. Use the one the installer printed.", "text/plain")
                cookie = f"{COOKIE}={inst.token}; Path=/; HttpOnly; SameSite=Strict"
                return self._send(HTTPStatus.SEE_OTHER, b"", "text/plain", {"Location": "/", "Set-Cookie": cookie})
            if not self._authed():
                return self._send(HTTPStatus.FORBIDDEN, "Open the link the installer printed (it carries a key).", "text/plain")
            if url.path == "/":
                return self._send(HTTPStatus.OK, page(), "text/html")
            if url.path == "/api/system":
                return self._send(HTTPStatus.OK, inst.facts(refresh="refresh" in q))
            if url.path == "/api/models/detect":
                return self._send(HTTPStatus.OK, {"servers": detect_models()})
            if url.path == "/api/progress":
                try:
                    since = int(q.get("since", ["0"])[0])
                except ValueError:
                    since = 0
                return self._send(HTTPStatus.OK, inst.progress(since))
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._send(HTTPStatus.FORBIDDEN, {"error": "unknown host"})
            # a page from anywhere else can't send this header without asking first, and nothing here says yes
            if not self._authed() or self.headers.get("X-Lens-Installer") != "1":
                return self._send(HTTPStatus.FORBIDDEN, {"error": "not allowed"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}")
            except ValueError:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": "send JSON"})
            path = urllib.parse.urlsplit(self.path).path
            try:
                if path == "/api/models/test":
                    base = str(body.get("base_url") or "").strip()
                    if not re.match(r"^https?://", base):
                        raise ValueError("the address starts with http:// or https://")
                    try:
                        models = sort_models(_get_models(base, str(body.get("api_key") or "").strip() or None, timeout=8))
                    except urllib.error.HTTPError as e:
                        why = "the key was refused" if e.code in (401, 403) else f"it answered {e.code}"
                        raise ValueError(f"Couldn't list the models: {why}") from None
                    except (urllib.error.URLError, OSError, ValueError) as e:
                        raise ValueError(f"Couldn't reach it: {getattr(e, 'reason', e)}") from None
                    return self._send(HTTPStatus.OK, {"models": models})
                if path == "/api/password":
                    if inst.facts()["sudo"] != "password":
                        return self._send(HTTPStatus.OK, {"ok": True})
                    ok = inst.check_password(body.get("password"))
                    return self._send(
                        HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST,
                        {"ok": ok, **({} if ok else {"error": "That password didn't work."})},
                    )
                if path == "/api/install":
                    inst.start(body)
                    return self._send(HTTPStatus.ACCEPTED, {"ok": True})
                if path == "/api/action":
                    action = ACTIONS.get(str(body.get("id")))
                    if not action:
                        raise ValueError("unknown action")
                    action["run"](inst)
                    return self._send(HTTPStatus.ACCEPTED, {"ok": True})
                if path == "/api/quit":
                    self._send(HTTPStatus.OK, {"ok": True})
                    threading.Thread(target=stop, daemon=True).start()
                    return None
            except ValueError as e:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    return Handler


def lan_address():
    """This machine's address on the network (the one its default route uses)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # nothing is sent
            return s.getsockname()[0]
    except OSError:
        return None


def main(argv=None):
    p = argparse.ArgumentParser(description="Lens's web installer")
    p.add_argument("--dir", default=os.environ.get("LENS_DIR") or os.path.expanduser("~/lens"))
    p.add_argument("--repo", default=os.environ.get("LENS_REPO") or "adeelahmad/lense")
    p.add_argument("--ref", default=os.environ.get("LENS_REF") or "main")
    p.add_argument("--port", type=int, default=int(os.environ.get("LENS_INSTALLER_PORT") or 0))
    p.add_argument("--public", action="store_true", help="answer from the network too (default on a server without a desktop)")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--script", help="the install.sh to run (default: the checkout's, else downloaded)")
    args = p.parse_args(argv)
    if not REPO_RX.match(args.repo) or not REF_RX.match(args.ref):
        print("LENS_REPO is owner/name and LENS_REF a branch or tag.", file=sys.stderr)
        return 2
    folder = os.path.abspath(os.path.expanduser(args.dir))
    inst = Installer(folder, args.repo, args.ref, args.script, os.environ.get("LENS_INSTALLER_TOKEN") or None)
    public = args.public or os.environ.get("LENS_INSTALLER_PUBLIC") == "1" or not desktop()
    bind = "0.0.0.0" if public else "127.0.0.1"
    port = args.port or first_free(3990, 4090)
    if not port:
        print("No free port between 3990 and 4090 for the installer; set LENS_INSTALLER_PORT.", file=sys.stderr)
        return 2
    allowed = {"localhost", "127.0.0.1", "::1", socket.gethostname().lower(), socket.gethostname().lower() + ".local"}
    if public:
        allowed.add("*")
    server = ThreadingHTTPServer((bind, port), None)  # type: ignore[arg-type]
    server.RequestHandlerClass = make_handler(inst, allowed, server.shutdown)
    host = (lan_address() or socket.gethostname()) if public else "localhost"
    link = f"http://{host}:{port}/?t={inst.token}"
    print(f"\033[1mLens's installer is open in your browser.\033[0m If it isn't, open:\n\n  {link}\n", flush=True)
    if public:
        print("  (from another computer on this network too; the link carries a one-time key)\n", flush=True)
    if not args.no_browser and desktop() and os.environ.get("LENS_NO_OPEN") != "1":
        threading.Timer(0.3, lambda: webbrowser.open(f"http://localhost:{port}/?t={inst.token}")).start()

    def idle_stop():  # once installed, the page's Open Lens closes it; else it goes after a while on its own
        while True:
            time.sleep(5)
            if inst.done_at and time.time() - inst.done_at > 1800:
                server.shutdown()
                return

    threading.Thread(target=idle_stop, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if inst.proc and inst.proc.poll() is None:
            inst.proc.terminate()
        shutil.rmtree(inst.tmp, ignore_errors=True)
    if inst.state == "done":
        print(f"Lens is running: {inst.url}")
        return 0
    if inst.state == "failed":
        print(f"The install stopped: {inst.error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
