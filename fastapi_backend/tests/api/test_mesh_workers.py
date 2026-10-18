"""Worker nodes (docs/mesh.md, Worker nodes): a machine that joined as a worker runs `lens worker` against this
install's database through the mesh, signed in as its own user of that one database, never the server's root. It
never gets the archive's secret: its jobs read their files from here over the mesh, plain, and a vault's never leave.
Removing the node removes its user; an undo makes it again with a new password. `lens worker` is played by Proc."""

from __future__ import annotations

import json

import pytest

from app.domain import ingest, keyring, mesh, mesh_node, store
from tests.api.test_mesh import admin, nebula, server_db  # noqa: F401 - fixtures
from tests.api.test_mesh_node import Proc, agent, due, running


def quiet(*a, **k):
    pass


def users(db):
    return set((db.one("INFO FOR DB") or {}).get("users") or {})


def on_mesh(client, cfg, h, folders, role="worker", namespaces=()):
    made = client.post("/api/v1/mesh/tokens", json={"role": role, "namespaces": list(namespaces)}, headers=h).json()
    a = agent(client, folders, made["secret"], name="gpu box")
    a.tick()
    (folders[1] / "host.pub").write_text(mesh.keygen(cfg)[1])
    running(folders[1])
    a.tick()
    return a


@pytest.fixture
def folders(tmp_path):
    return tmp_path / "node", tmp_path / "mesh"


@pytest.fixture(autouse=True)
def no_procs():
    Proc.started.clear()


def test_a_worker_node_runs_lens_worker_as_its_own_user_of_the_database(client, db, cfg, nebula, server_db, folders, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LENS_JOIN_TOKEN", "lensjoin1.1.spent")
    monkeypatch.setenv("ARCHIVE_SECRET_KEY", "the archive's own")
    h = admin(client, db)
    a = on_mesh(client, cfg, h, folders)
    nid = a.node["node"]["id"]
    assert f"lens_node_{nid}" in users(db)

    (w,) = Proc.started
    assert w.cmd[1:] == ["worker", "--name", "gpu box", "--steps", "transcribe,diarize", "--no-schedule"]
    assert w.env["SURREAL_URL"] == "ws://10.73.0.1:8000" and w.env["SURREAL_AUTH"] == "database"
    assert w.env["SURREAL_USER"] == f"lens_node_{nid}" and len(w.env["SURREAL_PASS"]) > 30
    assert (w.env["SURREAL_NS"], w.env["SURREAL_DB"]) == tuple(db._target) and w.env["LENS_MESH_NODE"] == str(nid)
    assert "LENS_JOIN_TOKEN" not in w.env and "ARCHIVE_SECRET_KEY" not in w.env  # nothing it doesn't need
    assert w.env["SURREAL_PASS"] not in json.dumps(a.node)  # asked for, never kept on the node's disk

    # a minute later nothing changed: it keeps running
    due(a)
    a.tick()
    assert Proc.started == [w] and not w.stopped

    # it stopped by itself: started again (after a short wait), and the Nodes page says so meanwhile
    w.returncode = 1
    due(a)
    a.tick()
    assert len(Proc.started) == 1 and "exit 1" in a.worker.error
    me = [n for n in client.get("/api/v1/mesh/nodes", headers=h).json() if n["id"] == nid][0]
    assert "exit 1" in me["status"]["error"] and any("stopped (exit 1)" in x for x in me["status"]["log"])
    a.worker.not_before = 0.0
    due(a)
    a.tick()
    w2 = Proc.started[-1]
    assert len(Proc.started) == 2 and w2.env["SURREAL_PASS"] == w.env["SURREAL_PASS"] and a.worker.error is None

    # removed: its worker stops and its user is gone; undone: a new password, and the worker starts with it
    client.delete(f"/api/v1/mesh/nodes/{nid}", headers=h)
    assert f"lens_node_{nid}" not in users(db)
    due(a)
    a.tick()
    assert w2.stopped and a.removed
    client.post(f"/api/v1/mesh/nodes/{nid}/restore", headers=h)
    assert f"lens_node_{nid}" in users(db)
    due(a)
    a.tick()
    w3 = Proc.started[-1]
    assert len(Proc.started) == 3 and w3.env["SURREAL_PASS"] != w2.env["SURREAL_PASS"]

    # the agent stopping stops it too
    import threading

    stop = threading.Event()
    stop.set()
    a.loop(stop)
    assert w3.stopped


def test_a_scaling_unit_names_its_worker_and_its_steps(client, db, cfg, nebula, server_db, folders, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LENS_SCALING_UNIT", "hetzner-3")
    monkeypatch.setenv("LENS_WORKER_STEPS", "transcribe")
    on_mesh(client, cfg, admin(client, db), folders)
    assert Proc.started[-1].cmd[1:6] == ["worker", "--name", "hetzner-3", "--steps", "transcribe"]


def test_a_node_runs_only_the_steps_a_node_can(client, db, cfg, nebula, server_db, folders, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LENS_WORKER_STEPS", "summarize,diarize")
    a = on_mesh(client, cfg, admin(client, db), folders)
    assert Proc.started[-1].cmd[5] == "diarize"  # summarize needs the archive key, which a node never has
    assert any("skipping summarize" in line for line in a.worker.log)
    monkeypatch.setenv("LENS_WORKER_STEPS", "summarize")
    a.worker.stop()
    a.worker.proc, a.worker.key = None, None
    n = len(Proc.started)
    a.worker.sync()
    assert len(Proc.started) == n and "none of LENS_WORKER_STEPS" in a.worker.error


def test_only_a_worker_node_gets_the_database(client, db, cfg, nebula, server_db, folders):  # noqa: F811
    a = on_mesh(client, cfg, admin(client, db), folders, role="compute")
    assert not Proc.started and a.worker.error is None  # a compute node runs no worker, and never asks
    r = client.get("/api/v1/mesh/node/worker", headers=a._headers())
    assert r.status_code == 403
    assert client.get("/api/v1/mesh/node/worker").status_code == 401


def test_a_worker_on_an_embedded_database_is_told_why_it_waits(client, db, cfg, nebula, server_db, folders, monkeypatch):  # noqa: F811
    a = on_mesh(client, cfg, admin(client, db), folders)
    Proc.started[-1].returncode = 0
    monkeypatch.setattr(db, "url", "surrealkv:///data/surrealdb")
    r = client.get("/api/v1/mesh/node/worker", headers=a._headers())
    assert r.status_code == 409 and "SurrealDB server" in r.json()["detail"]


def test_its_jobs_read_files_from_here_over_the_mesh(client, db, cfg, nebula, server_db, folders, folder, monkeypatch):  # noqa: F811
    h = admin(client, db)
    transcript = folder / "talk.txt"
    transcript.write_text("Alice: hello there.\n")
    audio = folder / "talk.mp3"
    audio.write_bytes(b"ID3 not really audio" * 100)
    rid = ingest.import_transcript(db, cfg, "pods", transcript, audio=str(audio), log=quiet)
    a = on_mesh(client, cfg, h, folders)

    r = client.get(f"/api/v1/mesh/node/media/{rid}", headers=a._headers())
    assert r.status_code == 200 and r.content == audio.read_bytes()
    assert client.get(f"/api/v1/mesh/node/media/{rid}", headers={**a._headers(), "If-None-Match": r.headers["etag"]}).status_code == 304
    assert client.get("/api/v1/mesh/node/media/999999", headers=a._headers()).status_code == 404

    # in the node's `lens worker`, a job's file is that, kept until it changes
    node_cfg = {**cfg, "data_dir": str(folder / "node-data")}
    monkeypatch.setenv("LENS_NODE_DIR", str(folders[0]))
    monkeypatch.setenv("LENS_MESH_NODE", str(a.node["node"]["id"]))
    monkeypatch.setattr(mesh_node.httpx, "Client", lambda **k: client)
    monkeypatch.setattr(client, "close", lambda: None, raising=False)
    rec = db.one("SELECT record::id(id) AS id, path, remote, space FROM $r", r=store.R("recording", rid))
    rec["path"] = "/srv/lens/uploads/pods/1/talk.mp3"  # where it is on the primary, not here
    got = ingest.audio_path(db, node_cfg, rec)
    assert got.endswith(f"/cache/mesh/{rid}.mp3") and open(got, "rb").read() == audio.read_bytes()
    assert mesh_node.media(node_cfg, rec) == mesh_node.media(node_cfg, rec)

    # encrypted here, it comes plain; a vault's never comes; nor one in a namespace the node doesn't serve
    keyring.encrypt_file(db, cfg, store.ns_id(db, "pods"), str(audio), force=True)
    assert keyring.is_encrypted(str(audio))
    r = client.get(f"/api/v1/mesh/node/media/{rid}", headers=a._headers())
    assert r.status_code == 200 and r.content == b"ID3 not really audio" * 100
    db.q("UPSERT data_key SET vault = true WHERE space = $s", s=store.ns_id(db, "pods"))
    assert client.get(f"/api/v1/mesh/node/media/{rid}", headers=a._headers()).status_code == 403
    db.q("UPSERT data_key SET vault = false WHERE space = $s", s=store.ns_id(db, "pods"))
    store.ns_id(db, "calls")
    client.patch(f"/api/v1/mesh/nodes/{a.node['node']['id']}", json={"namespaces": ["calls"]}, headers=h)
    assert client.get(f"/api/v1/mesh/node/media/{rid}", headers=a._headers()).status_code == 403


def test_a_node_serving_some_namespaces_takes_only_their_jobs(client, db, cfg, nebula, server_db, folders, folder, monkeypatch):  # noqa: F811
    from app.domain import jobs

    h = admin(client, db)
    rids = {}
    for ns in ("pods", "calls"):
        t = folder / f"{ns}.txt"
        t.write_text("Alice: hello there.\n")
        rids[ns] = ingest.import_transcript(db, cfg, ns, t, log=quiet)
        jobs.enqueue(db, rids[ns], steps=[{"type": "analyze"}])
    a = on_mesh(client, cfg, h, folders, namespaces=["calls"])
    monkeypatch.setattr(jobs, "MESH_NODE", a.node["node"]["id"])
    got = jobs.claim(db, "gpu box", {"analyze"})
    assert got and got["space"] == store.ns_id(db, "calls")
    assert jobs.claim(db, "gpu box", {"analyze"}) is None  # the other is pods', which this node doesn't serve
    monkeypatch.setattr(jobs, "MESH_NODE", None)
    assert jobs.claim(db, "here", {"analyze"})  # this machine's worker takes it
