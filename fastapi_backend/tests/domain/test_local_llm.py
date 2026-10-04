"""A chat model Lens runs itself: the catalog against this machine, downloads from Hugging Face (resumed, checked),
llama.cpp's server fetched and run, and the LLM provider pointed at it and put back."""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import socket
import sys
import threading
import zipfile

import pytest

from app.domain import local_llm, machine, settings, store

R = store.R
BLOB = b"GGUF" + os.urandom(4000)
FAKE_SERVER = """#!{python}
import http.server, json, os, sys
args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
key = os.environ.get("LLAMA_ARG_API_KEY")
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        ok = self.path == "/health" or self.headers.get("Authorization") == f"Bearer {{key}}"
        body = json.dumps({{"status": "ok", "args": args, "keyed": bool(key)}}).encode()
        self.send_response(200 if ok else 401)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
print("llama server listening", flush=True)
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
"""


class Hub(http.server.BaseHTTPRequestHandler):
    """Hugging Face's resolve URLs and GitHub's release API, as far as Lens uses them."""

    ranges = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/releases/latest":
            host = f"http://127.0.0.1:{self.server.server_address[1]}"
            body = json.dumps({"tag_name": "b9999", "assets": [{"name": "llama-b9999-bin-win-cpu-x64.zip", "browser_download_url": "x"},
                                                              {"name": "llama-b9999-bin-ubuntu-x64.zip", "browser_download_url": f"{host}/llama.zip"}]}).encode()  # fmt: skip
            return self._send(body)
        if self.path == "/llama.zip":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                info = zipfile.ZipInfo("build/bin/llama-server")
                info.external_attr = 0o755 << 16
                z.writestr(info, "#!/bin/sh\necho hi\n")
                z.writestr("build/bin/libllama.so", b"lib")
                z.writestr("../evil", b"no")
            return self._send(buf.getvalue())
        if "/resolve/" in self.path:
            rng = self.headers.get("Range")
            Hub.ranges.append(rng)
            start = int(rng.split("=")[1].rstrip("-")) if rng else 0
            self.send_response(206 if rng else 200)
            self.send_header("Content-Length", str(len(BLOB) - start))
            self.end_headers()
            self.wfile.write(BLOB[start:])
            return None
        self.send_error(404)

    def _send(self, body):
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def hub(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setattr(local_llm, "HF", base)
    monkeypatch.setattr(local_llm, "RELEASES_API", base + "/releases/latest")
    Hub.ranges.clear()
    yield base
    srv.shutdown()


def tiny(sha=None):
    return {"id": "tiny", "label": "Tiny", "repo": "acme/tiny-GGUF", "revision": "abc", "file": "tiny-q4.gguf", "size": len(BLOB),
            "sha256": sha or hashlib.sha256(BLOB).hexdigest(), "license": "mit", "about": "test", "tools": True}  # fmt: skip


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_catalog_says_what_this_machine_can_run(cfg, monkeypatch):
    real = machine.probe
    monkeypatch.setattr(machine, "probe", lambda data_dir=None: {**real(data_dir), "memory_gb": 4.0, "gpus": [], "disk_free_gb": 3.0})
    models, mach = local_llm.catalog(cfg)
    by = {m["id"]: m for m in models}
    assert by["qwen2.5-0.5b"]["fits"] and by["qwen2.5-1.5b"]["fits"]
    assert not by["qwen2.5-7b"]["fits"] and not by["qwen3-14b"]["fits"]
    assert by["qwen2.5-1.5b"]["room"] and not by["qwen2.5-7b"]["room"]
    assert not any(m["downloaded"] for m in models)
    assert all(m["sha256"] and len(m["revision"]) == 40 for m in local_llm.CATALOG)  # pinned and checked
    assert local_llm.memory_needed(1024**3) == 2.6


def test_settings_take_a_listed_model_or_any_gguf_on_hugging_face(db, cfg):
    settings.save(db, cfg, "local_llm", {"model": "qwen3-4b", "enabled": True, "context": 8192}, "t")
    settings.save(db, cfg, "local_llm", {"model": "hf:acme/My-Model-GGUF/my-model-Q4_K_M.gguf"}, "t")
    m = local_llm.resolve("hf:acme/My-Model-GGUF/my-model-Q4_K_M.gguf")
    assert (m["repo"], m["file"], m["sha256"]) == ("acme/My-Model-GGUF", "my-model-Q4_K_M.gguf", None)
    for bad in ({"model": "gpt-9"}, {"model": "hf:acme/x/notes.txt"}, {"context": 10}, {"port": 80}, {"host": "a b"}):
        with pytest.raises(ValueError):
            settings.save(db, cfg, "local_llm", bad, "t")
    with pytest.raises(ValueError):
        settings.save(db, cfg, "local_llm", {"server": "/bin/sh"}, "t")  # what the server runs is set at startup only
    eff = settings.effective(db, cfg)
    assert eff["local_llm"]["enabled"] and eff["local_llm"]["context"] == 8192
    assert not cfg["local_llm"]["enabled"]  # off by default


def test_downloads_resume_and_are_checked(cfg, hub):
    m = tiny()
    part = local_llm.model_path(cfg, m).with_suffix(".gguf.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(BLOB[:1000])
    seen = []
    path = local_llm.download(cfg, m, progress=lambda d, t: seen.append((d, t)))
    assert path.read_bytes() == BLOB and not part.exists()
    assert Hub.ranges == ["bytes=1000-"] and seen[-1] == (len(BLOB), len(BLOB))
    assert local_llm.downloaded(cfg) == [{"path": "acme__tiny-GGUF/tiny-q4.gguf", "size_gb": 0.0}]
    bad = {**tiny("0" * 64), "file": "other.gguf"}
    with pytest.raises(local_llm.LocalModelError, match="checksum"):
        local_llm.download(cfg, bad)
    assert not local_llm.model_path(cfg, bad).exists()


def test_llama_cpp_is_fetched_for_this_platform(cfg, hub, monkeypatch):
    monkeypatch.setattr(local_llm.shutil, "which", lambda name: None)
    assert local_llm.server_binary(cfg) is None
    monkeypatch.setattr(local_llm, "_platform", lambda: ("linux", "x86_64"))
    exe = local_llm.fetch_server(cfg)
    assert exe.endswith("build/bin/llama-server") and os.access(exe, os.X_OK)
    assert local_llm.server_binary(cfg) == exe
    assert not (local_llm.pathlib.Path(cfg["data_dir"]) / "bin" / "evil").exists()
    monkeypatch.setattr(local_llm, "_platform", lambda: ("windows", "x86_64"))
    with pytest.raises(local_llm.LocalModelError, match="install llama.cpp"):
        local_llm.fetch_server(cfg)


def test_the_server_runs_becomes_the_provider_and_puts_the_old_one_back(db, cfg, folder, hub, monkeypatch):
    exe = folder / "llama-server"
    exe.write_text(FAKE_SERVER.format(python=sys.executable))
    exe.chmod(0o755)
    monkeypatch.setitem(local_llm.BY_ID, "tiny", tiny())
    monkeypatch.setattr(local_llm.machine, "probe", lambda data_dir=None: {"container": False})
    cfg["local_llm"]["server"] = str(exe)
    port = free_port()
    settings.save(db, cfg, "llm", {"base_url": "https://api.example/v1", "model": "big", "api_key": "sk-old"}, "t")
    settings.save(db, cfg, "local_llm", {"enabled": True, "model": "tiny", "port": port}, "t")
    s = settings.Settings(db, cfg)
    run = local_llm.Runner(db, s.current, "test")
    try:
        run.tick()
        run.worker.join(30)
        assert run.phase == "running", (run.error, run.lines)
        url = f"http://127.0.0.1:{port}/v1"
        llm = s.current()["llm"]
        assert (llm["base_url"], llm["model"]) == (url, "tiny") and llm["api_key"].startswith("lens-")
        st = local_llm.status(db, s.current())
        assert st["phase"] == "running" and st["url"] == url and st["process"] == "test"
        assert "--alias" in run.proc.args and str(local_llm.model_path(cfg, tiny())) in run.proc.args
        assert llm["api_key"] not in " ".join(run.proc.args)  # the key isn't on the command line
        with pytest.raises(local_llm.LocalModelError, match="running"):
            local_llm.remove(s.current(), "tiny")
        settings.save(db, cfg, "local_llm", {"enabled": False}, "t")
        run.tick()
        llm = s.current()["llm"]
        assert (llm["base_url"], llm["model"], llm["api_key"]) == ("https://api.example/v1", "big", "sk-old")
        assert run.proc is None and local_llm.status(db, s.current())["phase"] == "off"
        assert local_llm.remove(s.current(), "tiny") is True
    finally:
        run.halt = True
        run.stop_process()


def test_status_and_removal_over_the_api(client, db, cfg, monkeypatch):
    from tests.helpers import login, make_user

    make_user(db, "ad@x.io", "admin password 1", admin=True)
    h = login(client, "ad@x.io", "admin password 1")
    r = client.get("/api/v1/settings/local-llm/status", headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["phase"] == "off" and len(j["catalog"]) == len(local_llm.CATALOG) and j["files"] == []
    assert client.delete("/api/v1/settings/local-llm/models?model=qwen3-4b", headers=h).json() == {"removed": False}
    assert client.delete("/api/v1/settings/local-llm/models?model=nope", headers=h).status_code == 409
