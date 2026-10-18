"""Import runs (import_runs.py, /import/runs): a bulk import is one run, listed with its counts and undone whole,
through the normal deletion path and its rights, leaving every other asset alone."""

from __future__ import annotations

import json

import pytest

from app.domain import convert, import_runs, ingest, settings, store
from tests.api._assist import Assist, sse, start_llm
from tests.api.test_anytopdf import here, lean  # noqa: F401 - fixtures: the fake anytopdf makes an archive's page
from tests.api.test_documents import _upload
from tests.helpers import drain, login, make_user, quiet, write_wav

R = store.R


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setattr(convert, "chromium", lambda cfg: "/usr/bin/chromium")  # web pages are queued, never captured here


@pytest.fixture
def env(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "other@x.io", "other password 1", roles={"calls": "owner"})
    return {
        "ha": login(client, "root@x.io", "root password 1"),
        "ho": login(client, "own@x.io", "owner password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hx": login(client, "other@x.io", "other password 1"),
    }


def _links(client, h, *urls):
    r = client.post("/api/v1/import/web/links", headers=h, json={"namespace": "pods", "text": "\n".join(urls)})
    assert r.status_code == 200, r.text
    return r.json()


def _ids(db):
    return set(db.values("SELECT VALUE record::id(id) FROM recording"))


def test_a_link_import_is_one_run_undone_whole(web, client, env, db):
    ho, he, hx = env["ho"], env["he"], env["hx"]
    first = _links(client, ho, "https://8.8.8.8/a", "https://8.8.4.4/b")
    second = _links(client, he, "https://1.1.1.1/c")
    run, other = first["import_run"], second["import_run"]
    assert run and other and run != other
    mine = {r["recording"] for r in first["results"]}
    assert all(r["import_run"] == run for r in first["results"])
    assert set(db.values("SELECT VALUE record::id(id) FROM recording WHERE import_run = $r", r=run)) == mine
    assert client.get(f"/api/v1/recordings/{min(mine)}", headers=ho).json()["import_run"] == run

    listed = client.get("/api/v1/import/runs", headers=ho).json()  # an owner of pods sees both, newest first
    assert [(x["id"], x["kind"], x["namespace"], x["assets"], x["left"], x["status"]) for x in listed] == [
        (other, "links", "pods", 1, 1, "imported"),
        (run, "links", "pods", 2, 2, "imported"),
    ]
    assert listed[1]["created_by"] == "own@x.io" and listed[1]["source"] == "pasted links"
    assert [x["id"] for x in client.get("/api/v1/import/runs", headers=he).json()] == [other]  # an editor: their own
    assert client.get("/api/v1/import/runs", headers=hx).json() == []
    assert client.get(f"/api/v1/import/runs/{run}", headers=hx).status_code == 404

    # without the rights a delete needs, nothing goes: an editor who started the run, someone of another namespace
    r = client.post(f"/api/v1/import/runs/{other}/undo", headers=he)
    assert r.status_code == 403 and "owner" in r.json()["detail"], r.text
    assert client.post(f"/api/v1/import/runs/{run}/undo", headers=hx).status_code == 404
    assert len(_ids(db)) == 3

    before = _ids(db)
    r = client.post(f"/api/v1/import/runs/{run}/undo", headers=ho)
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out["deleted"]) == mine and out["busy"] == []
    assert (out["run"]["left"], out["run"]["status"], out["run"]["undone_by"]) == (0, "undone", "own@x.io")
    assert _ids(db) == before - mine  # exactly the run's assets: the other run's page stays
    assert not db.values("SELECT VALUE id FROM job WHERE recording IN $r AND status = 'queued'", r=sorted(mine))
    actions = db.rows("SELECT action, target, detail FROM audit_log WHERE action IN ['recording.delete', 'import.undo']")
    assert sorted(a["target"] for a in actions if a["action"] == "recording.delete") == sorted(f"recording:{i}" for i in mine)
    assert [a["detail"]["deleted"] for a in actions if a["action"] == "import.undo"] == [2]
    # undoing it again finds nothing left; the other run is still there to undo
    assert client.post(f"/api/v1/import/runs/{run}/undo", headers=ho).json()["deleted"] == []
    assert client.post(f"/api/v1/import/runs/{other}/undo", headers=ho).json()["deleted"] == [second["results"][0]["recording"]]


def test_an_uploaded_archive_and_its_files_are_one_run(lean, here, client, env, db, cfg):  # noqa: F811 - the fixtures
    from tests.api.test_archives import zip_of

    ho, he = env["ho"], env["he"]
    alone = _upload(client, ho, b"Harbour log. Nothing to undo here.\n", "log.txt")
    up = _upload(client, ho, zip_of({"notes.txt": "Tide notes. High water at noon.", "plan.md": "# Plan\n\nLamps."}), "export.zip")
    drain(db, settings.effective(db, cfg))
    rid = up["recording"]
    rec = db.one("SELECT status, error FROM $r", r=R("recording", rid))
    assert rec["status"] == "analyzed", rec
    made = set(db.values("SELECT VALUE record::id(id) FROM recording WHERE attached_to.resource = $r", r=rid))
    assert len(made) == 2
    run = db.one("SELECT import_run FROM $r", r=R("recording", rid))["import_run"]
    assert run and set(db.values("SELECT VALUE record::id(id) FROM recording WHERE import_run = $r", r=run)) == made | {rid}
    assert not db.one("SELECT import_run FROM $r", r=R("recording", alone["recording"])).get("import_run")  # one upload: no run
    got = client.get(f"/api/v1/import/runs/{run}", headers=ho).json()
    assert (got["kind"], got["source"], got["assets"], got["created_by"]) == ("archive", "export", 3, "own@x.io")

    assert client.post(f"/api/v1/import/runs/{run}/undo", headers=he).status_code == 404  # not theirs, and not an owner
    out = client.post(f"/api/v1/import/runs/{run}/undo", headers=ho).json()
    assert set(out["deleted"]) == made | {rid} and out["deleted"][-1] == rid  # the files, then the archive
    assert _ids(db) == {alone["recording"]}


def test_a_folder_scan_is_one_run(db, cfg, folder):
    audio = folder / "audio"
    audio.mkdir()
    for name, seconds in (("a.wav", 1.0), ("b.wav", 1.5)):
        write_wav(audio / name, seconds=seconds)
    cfg["namespaces"]["pods"]["paths"] = [str(audio)]
    assert ingest.scan(db, cfg, only="pods", log=quiet, by="root@x.io")["new"] == 2
    (run,) = import_runs.listing(db)
    assert (run["kind"], run["assets"], run["left"], run["created_by"]) == ("scan", 2, 2, "root@x.io")
    assert run["source"] == f"scan of {audio}"
    assert ingest.scan(db, cfg, only="pods", log=quiet)["new"] == 0
    assert len(import_runs.listing(db)) == 1  # a scan that finds nothing new makes no run


def test_chosen_files_of_a_source_are_one_run(client, env, db, folder, monkeypatch):
    from tests.api.test_sources import _lsjson_without_rclone

    _lsjson_without_rclone(monkeypatch)
    ha, ho = env["ha"], env["ho"]
    inbox = folder / "inbox" / "talks"
    inbox.mkdir(parents=True)
    for name, seconds in (("ep1.wav", 1.0), ("ep2.wav", 1.5)):
        write_wav(inbox / name, seconds=seconds)
    sid = client.post("/api/v1/sources", json={"name": "inbox", "type": "local"}, headers=ha).json()["id"]
    paths = [str(inbox / "ep1.wav"), str(inbox / "ep2.wav")]
    r = client.post("/api/v1/import/source", json={"source": sid, "paths": paths, "namespace": "pods"}, headers=ha).json()
    run = r["import_run"]
    assert run and [x["import_run"] for x in r["results"]] == [run, run]
    got = client.get(f"/api/v1/import/runs/{run}", headers=ho).json()  # an owner of pods sees an admin's run there
    assert (got["kind"], got["assets"], got["source"], got["created_by"]) == ("source", 2, "inbox: 2 chosen file(s)", "root@x.io")
    # chosen again: already there, so no new run, and nothing already here joins one
    again = client.post("/api/v1/import/source", json={"source": sid, "paths": paths, "namespace": "pods"}, headers=ha).json()
    assert again["import_run"] is None and [x["status"] for x in again["results"]] == ["already", "already"]
    since = import_runs.mark(db)
    old = r["results"][0]["recording"]
    assert import_runs.record(db, "source", store.ns_id(db, "pods"), [old], after=since) is None


@pytest.fixture
def llm(cfg):
    from tests import fake_llm

    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


def test_the_assistant_lists_and_undoes_an_import_with_approval(web, llm, app, db, cfg, folder, new_client):
    from tests.api.test_setup_assistant import call

    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    r = c.post("/api/v1/import/web/links", headers=h, json={"namespace": "pods", "text": "https://8.8.8.8/a\nhttps://8.8.4.4/b"})
    run = r.json()["import_run"]
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "import_runs", {})]},
        {"content": "", "tool_calls": [call(2, "undo_import", {"run": run})]},
        {"content": "I asked to undo it."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={"kind": "setup"}).json()["id"]  # even in setup, it waits for a yes
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "undo that import"}).text)
    said = llm.seen[-2]["messages"][-1]["content"]
    listed = json.loads(said[said.index("{") : said.rindex("}") + 1])["runs"]
    assert [(x["id"], x["left"]) for x in listed] == [(run, 2)]
    appr = ev["approval"][0]
    assert appr["summary"] == f"Undo import run {run} (pasted links): delete its 2 asset(s)"
    assert import_runs.get(db, run)["left"] == 2  # nothing until approved
    done = c.post(f"/api/v1/approvals/{appr['id']}", headers=h, json={"decision": "approve"}).json()
    assert (done["status"], done["deleted"]) == ("done", 2), done
    assert import_runs.get(db, run)["status"] == "undone"
