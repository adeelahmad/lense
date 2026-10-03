"""Routine decisions taken for the person: the decision model when it has a key, the language model when not, acting
when sure and asking when not. The first one is where a file dropped into a conversation goes."""

from __future__ import annotations

import json

import pytest

from app.domain import decide, settings, store
from tests import fake_jev, fake_llm
from tests.api._assist import Assist, sse, start_llm
from tests.api.test_setup_assistant import call, held
from tests.helpers import write_wav

OPTIONS = {"kids": "School and the children.", "work": "The job."}


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def jev(cfg):
    srv = fake_jev.start(cfg)
    yield fake_jev.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


def test_the_decision_model_answers_when_it_has_a_key(cfg, jev, llm):
    jev.answer = {"type": "choice", "choice": "work", "confidence": 0.59, "probabilities": {"kids": 0.27, "work": 0.73}}
    d = decide.choose(cfg, "Where does this go?", OPTIONS, {"subject": "Parents evening", "from": "hr@work.example"})
    assert (d["choice"], d["by"], d["ranked"][0]) == ("work", "jev", {"option": "work", "p": 0.73})
    assert not decide.sure(cfg, d)  # below act_above: ask
    headers, body = jev.seen[-1]
    assert headers["Authorization"] == "Bearer jev-key"
    assert body["model"] == "jev-latest" and body["questions"]["q"] == {
        "type": "choice",
        "instructions": "Where does this go?",
        "criteria": OPTIONS,
    }
    assert json.loads(body["state"])["from"] == "hr@work.example"
    assert not [b for b in llm.seen if b.get("response_format")]  # the language model wasn't asked


def test_the_language_model_decides_without_one_and_when_it_fails(cfg, jev, llm):
    llm.decision = {"choice": "kids", "confidence": 0.95}
    jev.status = 400
    d = decide.choose(cfg, "Where does this go?", OPTIONS, "a school trip form")
    assert (d["choice"], d["by"]) == ("kids", "llm") and decide.sure(cfg, d)
    cfg["decisions"]["api_key"] = None  # auto: no key, so straight to the language model
    n = len(jev.seen)
    assert decide.choose(cfg, "Where?", OPTIONS, "x")["by"] == "llm" and len(jev.seen) == n
    cfg["decisions"]["engine"] = "off"
    with pytest.raises(decide.Undecided):
        decide.choose(cfg, "Where?", OPTIONS, "x")
    assert decide.choose(cfg, "Where?", {"only": "the one"}, "x")["choice"] == "only"  # nothing to decide


def test_the_decision_settings_are_in_the_app(app, db, cfg, folder, new_client, monkeypatch):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    r = c.put("/api/v1/settings/decisions", headers=h, json={"engine": "jev", "api_key": "ts-secret", "act_above": 0.9})
    assert r.status_code == 200, r.text
    v = c.get("/api/v1/settings", headers=h)
    assert "ts-secret" not in v.text and v.json()["decisions"]["values"]["api_key"] == {"secret": True, "set": True}
    assert app.state.settings.current()["decisions"]["act_above"] == 0.9
    for bad in ({"engine": "magic"}, {"act_above": 0.2}, {"base_url": "ftp://x"}, {"model": ""}):
        assert c.put("/api/v1/settings/decisions", headers=h, json=bad).status_code == 400, bad
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-env")
    assert settings.locked("decisions") == ["api_key"]
    assert settings.effective(db, cfg)["decisions"]["api_key"] == "ts-env"


def _attach(c, h, folder, name):
    wav = folder / name
    write_wav(wav, seconds=1)
    return held(c, h, wav.read_bytes(), name)


def test_a_file_goes_where_it_fits_without_asking(app, db, cfg, folder, new_client, llm, jev):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    up = _attach(c, h, folder, "school-trip.wav")
    jev.answer = {"type": "choice", "choice": "pods", "confidence": 0.96, "probabilities": {"calls": 0.04, "pods": 0.96}}
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "import_files", {"upload_ids": [up["id"]]})]},
        {"content": "Put it in pods."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={"kind": "setup"}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "the podcast pilot", "attachments": [up["id"]]}).text)
    assert ev["step"][0]["summary"] == "Import school-trip.wav into pods (chosen, 96% sure): done"
    assert c.get(f"/api/v1/uploads/{up['id']}", headers=h).json()["namespace"] == "pods"
    body = jev.seen[-1][1]
    assert set(body["questions"]["q"]["criteria"]) == {"calls", "pods"}
    assert "Holds recordings such as" in body["questions"]["q"]["criteria"]["pods"]  # what's in it tells them apart
    state = json.loads(body["state"])
    assert state["their_message"] == "the podcast pilot" and state["files"][0]["file"] == "school-trip.wav"
    told = json.loads(llm.seen[-1]["messages"][-1]["content"])
    assert told["namespace_chosen"] == {"by": "jev", "confidence": 0.96}


def test_unsure_it_asks_with_the_options_ranked(app, db, cfg, folder, new_client, llm, jev):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    up = _attach(c, h, folder, "memo.wav")
    jev.answer = {"type": "choice", "choice": "calls", "confidence": 0.4, "probabilities": {"calls": 0.6, "pods": 0.4}}
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "import_files", {"upload_ids": [up["id"]]})]},
        {"content": "Calls or pods?"},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={"kind": "setup"}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "", "attachments": [up["id"]]}).text)
    assert ev["step"][0]["summary"] == "Not sure where memo.wav go"
    told = json.loads(llm.seen[-1]["messages"][-1]["content"])
    assert told["status"] == "ask" and [x["option"] for x in told["namespaces"]] == ["calls", "pods"]
    assert c.get(f"/api/v1/uploads/{up['id']}", headers=h).json()["state"] == "held"


def test_an_editor_with_one_namespace_needs_no_decision(app, db, cfg, folder, new_client, llm, jev):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    up = _attach(c, h, folder, "e.wav")
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "import_files", {"upload_ids": [up["id"]]})]},
        {"content": "I asked to put it in pods."},
    ]
    n = len(jev.seen)
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "file this", "attachments": [up["id"]]}).text)
    assert len(jev.seen) == n  # pods is the only place ed can add to
    appr = ev["approval"][0]
    assert appr["summary"] == "Import e.wav into pods"
    assert c.post(f"/api/v1/approvals/{appr['id']}", headers=h, json={"decision": "approve"}).json()["status"] == "done"
    assert db.values("SELECT VALUE name FROM space WHERE id = $s", s=store.R("space", s.pods)) == ["pods"]
    assert c.get(f"/api/v1/uploads/{up['id']}", headers=h).json()["state"] == "done"
