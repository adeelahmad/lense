"""Lens fetching what it needs: packages at uv.lock's versions (PyTorch's CPU build without a GPU), checked model
files, models pulled on Ollama, steps waiting while their engine arrives, and the admin's view of it all."""

from __future__ import annotations

import hashlib
import http.server
import json
import threading

import pytest

from app.domain import components, jobs, machine
from tests.helpers import login, make_user, seed


class Fake(components.Component):
    """A component that's fetched by flipping a flag; `fail` makes the fetch raise."""

    def __init__(self, id, steps=("transcribe",), fail=False):
        super().__init__(id, id.title(), "for tests", steps=steps)
        self.here, self.fail, self.fetched = False, fail, 0
        self.gate = threading.Event()
        self.gate.set()

    def needed(self, cfg, m):
        return True

    def present(self, cfg):
        return self.here

    def fetch(self, cfg, m, say):
        say(f"fetching {self.id}: 50%")
        self.gate.wait(5)
        self.fetched += 1
        if self.fail:
            raise RuntimeError("no network")
        self.here = True


@pytest.fixture
def fakes(monkeypatch):
    engine, model = Fake("engine"), Fake("model", steps=("faces",), fail=True)
    monkeypatch.setattr(components, "COMPONENTS", [engine, model])
    monkeypatch.setattr(components, "BY_ID", {"engine": engine, "model": model})
    return engine, model


def keeper(db, cfg, can=("transcribe", "faces"), ahead=True):
    cfg["components"]["auto"] = True
    cfg["components"]["ahead"] = ahead
    db.q("UPSERT worker:w1 SET steps = []")
    return components.Keeper(db, lambda: cfg, "w1", can)


def test_what_is_missing_is_fetched_and_reported(db, cfg, fakes):
    engine, model = fakes
    k = keeper(db, cfg)
    state = k.check()
    assert state["engine"] == {"state": "ready"}
    assert state["model"]["state"] == "failed" and "no network" in state["model"]["error"]
    row = db.one("SELECT components, machine FROM worker:w1")
    assert row["components"]["engine"]["state"] == "ready" and row["machine"]["cpus"] >= 1
    # a failure isn't tried again at once; asking (Check now in the app) or new settings does
    k.check()
    assert model.fetched == 1
    components.poke(db)
    assert components._poked(db, 0)
    k.failed_at.clear()
    k.check()
    assert model.fetched == 2
    # what isn't this worker's (its steps) isn't fetched for it
    other = components.Keeper(db, lambda: cfg, "w2", ["summarize"])
    assert other.wanted(cfg, machine.probe()) == []


def test_steps_wait_while_their_engine_is_fetched(db, cfg, folder, fakes):
    engine, _ = fakes
    a, _, _ = seed(db, cfg, folder)
    jobs.enqueue(db, a, ["transcribe"])
    w = jobs.Worker(db, lambda: cfg, name="w1", steps=["transcribe"])
    w.keeper = keeper(db, cfg, ["transcribe"])
    engine.gate.clear()
    th = threading.Thread(target=w.keeper.check)
    th.start()
    for _ in range(100):
        if (w.keeper.state.get("engine") or {}).get("state") == "fetching":
            break
        threading.Event().wait(0.02)
    assert w.keeper.blocked() == {"transcribe"}
    assert w.run_once() is False  # the job waits
    assert db.values("SELECT VALUE status FROM job")[0] == "queued"
    engine.gate.set()
    th.join()
    assert w.keeper.blocked() == set()


def test_engines_are_fetched_on_first_use(db, cfg, folder, fakes):
    engine, model = fakes
    k = keeper(db, cfg, ahead=False)
    # nothing has asked for them: nothing is fetched, and their steps wait for a job to need them
    assert k.check() == {"engine": {"state": "later"}, "model": {"state": "later"}}
    assert engine.fetched == model.fetched == 0 and k.blocked() == {"transcribe", "faces"}
    assert db.one("SELECT components FROM worker:w1")["components"]["engine"] == {"state": "later"}
    # a recording to transcribe: the transcription engine is fetched, the face model still waits
    a, _, _ = seed(db, cfg, folder)
    jobs.enqueue(db, a, ["transcribe"])
    assert k.demand() == {"transcribe"}
    state = k.check()
    assert state["engine"] == {"state": "ready"} and state["model"] == {"state": "later"}
    assert engine.fetched == 1 and model.fetched == 0 and k.blocked() == {"faces"}
    # asked for by name, it's fetched without waiting for a job
    cfg["components"]["also"] = ["model"]
    assert k.check()["model"]["state"] == "failed" and model.fetched == 1


def test_with_fetching_off_it_only_reports(db, cfg, fakes):
    engine, _ = fakes
    k = keeper(db, cfg)
    cfg["components"]["auto"] = False
    assert k.check()["engine"] == {"state": "missing"} and engine.fetched == 0
    cfg["components"]["also"] = ["engine"]  # asked for by name
    assert k.check()["engine"] == {"state": "ready"}


def test_packages_come_at_the_locked_versions(cfg, monkeypatch):
    ran = []
    monkeypatch.setattr(
        components,
        "_locked",
        lambda extra: [
            ("torch", "torch==2.14.0"),
            ("torchaudio", "torchaudio==2.11.0"),
            ("nvidia-cublas", "nvidia-cublas==13.1.1.3 ; sys_platform == 'linux'"),
            ("speechbrain", "speechbrain==1.1.1"),
        ],
    )

    def run(cmd, **kw):
        ran.append((cmd, open(cmd[-1]).read()))
        return ""

    monkeypatch.setattr(components, "_run", run)
    monkeypatch.setattr(components.shutil, "which", lambda n: "/usr/bin/" + n)
    real = machine._fixed()
    monkeypatch.setattr(machine, "_fixed", lambda: {**real, "os": "linux", "gpus": []})
    components.pip_install(cfg, "voices")
    (torch_cmd, torch_reqs), (rest_cmd, rest_reqs) = ran
    assert torch_cmd[torch_cmd.index("--index-url") + 1] == components.TORCH_CPU
    assert torch_reqs.split() == ["torch==2.14.0+cpu", "torchaudio==2.11.0+cpu"]
    assert rest_reqs.split() == ["speechbrain==1.1.1"] and "--index-url" not in rest_cmd  # no CUDA libraries
    assert "--no-deps" in rest_cmd and rest_cmd[rest_cmd.index("--target") + 1] == str(components.packages_dir(cfg))
    # with an NVIDIA GPU: PyPI's builds, with their CUDA libraries
    ran.clear()
    monkeypatch.setattr(machine, "_fixed", lambda: {**real, "os": "linux", "gpus": [{"name": "RTX"}]})
    components.pip_install(cfg, "voices")
    assert "nvidia-cublas" in ran[1][1] and "--index-url" not in ran[0][0]


class Files(http.server.BaseHTTPRequestHandler):
    body = b"onnx bytes"
    pulled = []
    models = ["llama3.2:latest"]

    def log_message(self, *a):
        pass

    def _send(self, data, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/version":
            return self._send(b'{"version": "0.9.0"}')
        if self.path == "/api/tags":
            return self._send(json.dumps({"models": [{"name": m, "model": m} for m in Files.models]}).encode())
        return self._send(Files.body, "application/octet-stream")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Files.pulled.append(body["model"])
        lines = [{"status": "pulling", "total": 100, "completed": 40}, {"status": "success"}]
        Files.models.append(body["model"])
        self._send(b"\n".join(json.dumps(x).encode() for x in lines), "application/x-ndjson")


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Files)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_model_files_are_checked_against_their_hash(cfg, server, monkeypatch):
    good = hashlib.sha256(Files.body).hexdigest()
    monkeypatch.setitem(components.FILES, "yolox_s.onnx", (server + "/y.onnx", good, 1))
    monkeypatch.setitem(components.FILES, "face_detection_yunet_2023mar.onnx", (server + "/f.onnx", "0" * 64, 1))
    path = components.fetch_file(cfg, "yolox_s.onnx")
    assert open(path, "rb").read() == Files.body
    from app.domain import objects

    assert objects.yolox_model(cfg) == path  # found where it was fetched
    with pytest.raises(RuntimeError, match="checksum"):
        components.fetch_file(cfg, "face_detection_yunet_2023mar.onnx")
    assert components.model_file(cfg, "face_detection_yunet_2023mar.onnx") is None


def test_models_are_pulled_on_ollama(cfg, server):
    Files.pulled, Files.models = [], ["llama3.2:latest"]
    cfg["llm"].update(base_url=server + "/v1", model="llama3.2")
    cfg["embeddings"].update(base_url=None, model="nomic-embed-text")
    llm, emb = components.BY_ID["llm-model"], components.BY_ID["embedding-model"]
    m = machine.probe()
    assert llm.needed(cfg, m) and llm.present(cfg)  # llama3.2 is llama3.2:latest
    assert emb.needed(cfg, m) and not emb.present(cfg)  # on the LLM provider's server
    said = []
    emb.fetch(cfg, m, said.append)
    assert Files.pulled == ["nomic-embed-text"] and emb.present(cfg)
    assert said == ["pulling nomic-embed-text: 40%"]
    cfg["llm"]["base_url"] = "http://127.0.0.1:9/v1"  # not Ollama: nothing to pull
    assert not llm.needed(cfg, m)


def test_the_hardware_chooses_the_engine():
    base = {"cuda": False, "apple_silicon": False, "memory_gb": 16, "disk_free_gb": 100}
    assert components.recommend(base) == {"engine": "sensevoice"}
    assert components.recommend({**base, "cuda": True})["whisper"]["compute_type"] == "float16"
    assert components.recommend({**base, "apple_silicon": True}) == {"engine": "mlx-whisper"}
    assert components.recommend({**base, "memory_gb": 4})["whisper"]["model"] == "small"


def test_admins_see_components_and_ask_for_a_check(client, db, cfg):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    db.q("UPSERT worker:w1 SET steps = ['transcribe'], components = {sensevoice: {state: 'fetching', detail: 'half'}}")
    h = login(client, "root@x.io", "root password 1")
    r = client.get("/api/v1/components", headers=h)
    assert r.status_code == 200, r.text
    v = r.json()
    ids = {c["id"]: c for c in v["components"]}
    assert ids["sensevoice"]["needed"] and ids["sensevoice"]["steps"] == ["transcribe"]
    assert ids["ffmpeg"]["kind"] == "program" and isinstance(ids["ffmpeg"]["here"], bool)
    assert ids["msg"]["optional"] and ids["msg"]["license"] == "GPL-3.0"
    assert v["workers"][0]["components"]["sensevoice"] == {"state": "fetching", "detail": "half", "error": None}
    assert v["auto"] is False and v["machine"]["cpus"] >= 1
    assert client.post("/api/v1/components/check", headers=h).status_code == 200
    assert components._poked(db, 0)
    assert client.put("/api/v1/settings/components", headers=h, json={"also": ["msg"]}).status_code == 200
    assert client.put("/api/v1/settings/components", headers=h, json={"also": ["ffmpeg"]}).status_code == 400
    hv = login(client, "vi@x.io", "viewer password 1")
    assert client.get("/api/v1/components", headers=hv).status_code == 403
