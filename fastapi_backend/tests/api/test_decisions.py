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
    asked = len(llm.seen)
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
    assert len(llm.seen) == asked  # the language model wasn't asked


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
    assert ev["step"][0]["summary"] == "Not sure where to put memo.wav"
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


# ---------- Laya, a decision model on the machine itself ----------
class FakeAgent:
    """Stands in for laya_mlx.Agent: answers each choice with its last option, and remembers what it was asked."""

    def __init__(self, model):
        self.model, self.seen = model, []

    def system_one(self, state, questions):
        self.seen.append((state, questions))
        answers = {}
        for name, q in questions.items():
            last = list(q["criteria"])[-1]
            answers[name] = {
                "type": "choice",
                "choice": last,
                "confidence": 0.93,
                "probabilities": {k: (0.93 if k == last else 0.07) for k in q["criteria"]},
            }
        return {"model": "laya-rl-agent", "answers": answers, "usage": {"input_tokens": 30, "output_tokens": 0}}


@pytest.fixture
def mac(monkeypatch):
    """This machine is an Apple Silicon Mac with laya-mlx and the model fetched."""
    import sys
    import types

    from app.domain import components

    loaded = {}
    fake = types.SimpleNamespace(load=lambda model, **kw: loaded.setdefault(model, FakeAgent(model)))
    monkeypatch.setitem(sys.modules, "laya_mlx", fake)
    monkeypatch.setattr(decide, "laya_here", lambda: True)
    monkeypatch.setattr(components.LAYA, "present", lambda cfg: True)
    monkeypatch.setattr(decide, "_AGENTS", {})
    return loaded


def test_laya_decides_on_this_mac(cfg, llm, mac):
    cfg["decisions"]["engine"] = "laya"
    asked = len(llm.seen)
    d = decide.choose(cfg, "Where does this go?", OPTIONS, {"subject": "Quarterly targets"})
    assert (d["choice"], d["by"], d["confidence"]) == ("work", "laya", 0.93)
    agent = mac["aac6fef/laya-mlx"]  # the default model
    state, questions = agent.seen[-1]
    assert json.loads(state)["subject"] == "Quarterly targets" and questions["q"]["criteria"] == OPTIONS
    assert len(llm.seen) == asked
    cfg["decisions"]["laya_model"] = "aac6fef/laya-multilingual-mlx"
    decide.choose(cfg, "Where?", OPTIONS, "x")
    assert "aac6fef/laya-multilingual-mlx" in mac


def test_laya_asks_a_laya_server_where_mlx_cant_run(cfg, jev, llm, monkeypatch):
    monkeypatch.setattr(decide, "laya_here", lambda: False)
    url = cfg["decisions"]["base_url"]
    cfg["decisions"].update(
        engine="laya", base_url="https://api.typesafe.ai/v1", laya_url=url, laya_model="aac6fef/laya-typed-decisions-mlx"
    )
    jev.answer = {"type": "choice", "choice": "kids", "confidence": 0.88, "probabilities": {"kids": 0.88, "work": 0.12}}
    d = decide.choose(cfg, "Where does this go?", OPTIONS, "the school trip form")
    assert (d["choice"], d["by"]) == ("kids", "laya")
    headers, body = jev.seen[-1]
    assert "Authorization" not in headers  # Jev's key stays with Jev
    assert body["model"] == "aac6fef/laya-typed-decisions-mlx" and body["state"] == "the school trip form"
    assert decide.laya_status(cfg)["where"] == "server"


def test_laya_says_it_cant_run_here_and_the_llm_decides(cfg, llm, monkeypatch):
    monkeypatch.setattr(decide, "laya_here", lambda: False)
    cfg["decisions"]["engine"] = "laya"
    st = decide.laya_status(cfg)
    assert not st["available"] and "Apple Silicon" in st["reason"]
    llm.decision = {"choice": "kids", "confidence": 0.9}
    assert decide.choose(cfg, "Where?", OPTIONS, "x")["by"] == "llm"


def test_nothing_set_up_decides_as_before(cfg, llm, monkeypatch):
    called = []
    monkeypatch.setattr(decide, "_laya", lambda *a: called.append(a))
    assert cfg["decisions"]["engine"] == "auto" and decide.engine(cfg) == "llm"
    llm.decision = {"choice": "kids", "confidence": 0.9}
    assert decide.choose(cfg, "Where?", OPTIONS, "x")["by"] == "llm" and not called


def test_lens_fetches_laya_only_where_it_runs(cfg):
    from app.domain import components

    mac_m, linux_m = {"apple_silicon": True}, {"apple_silicon": False}
    assert not components.LAYA.needed(cfg, mac_m)  # Jev stays the default
    cfg["decisions"]["engine"] = "laya"
    assert components.LAYA.needed(cfg, mac_m) and not components.LAYA.needed(cfg, linux_m)
    cfg["decisions"]["laya_url"] = "http://host.docker.internal:8790/v1"
    assert not components.LAYA.needed(cfg, mac_m)  # a server answers instead


def test_the_decide_server_speaks_system_one(cfg, mac, monkeypatch):
    import threading
    import urllib.request

    monkeypatch.setattr(decide, "ready_laya", lambda *a, **kw: None)
    srv = decide.laya_server(cfg, port=0, say=lambda *a: None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
        cfg["decisions"].update(engine="laya", laya_url=url, laya_model="aac6fef/laya-multilingual-mlx")
        monkeypatch.setattr(decide, "laya_here", lambda: False)  # as Lens in Docker sees it
        d = decide.choose(cfg, "Where does this go?", OPTIONS, "x")
        assert (d["choice"], d["by"]) == ("work", "laya") and "aac6fef/laya-multilingual-mlx" in mac
        with urllib.request.urlopen(url + "/health") as r:
            assert json.load(r)["ok"]
        bad = urllib.request.Request(url + "/systemone", data=b'{"state": "x"}', method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad)
        assert e.value.code == 400
    finally:
        srv.shutdown()


def test_laya_settings_status_and_test_in_the_app(app, db, cfg, folder, new_client, llm, monkeypatch):
    monkeypatch.setattr(decide, "laya_here", lambda: False)
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    st = c.get("/api/v1/settings/decisions/status", headers=h).json()
    assert st["engine"] == "auto" and st["by"] == "llm" and not st["laya"]["available"]
    assert [m["id"] for m in st["laya_models"]][0] == "aac6fef/laya-mlx"
    r = c.put("/api/v1/settings/decisions", headers=h, json={"engine": "laya", "laya_model": "aac6fef/laya-multilingual-mlx"})
    assert r.status_code == 200, r.text
    for bad in ({"laya_model": "someone/else"}, {"laya_url": "ftp://mac"}):
        assert c.put("/api/v1/settings/decisions", headers=h, json=bad).status_code == 400, bad
    st = c.get("/api/v1/settings/decisions/status", headers=h).json()
    assert st["by"] == "laya" and st["laya"]["model"] == "aac6fef/laya-multilingual-mlx" and "Apple Silicon" in st["laya"]["reason"]
    llm.decision = {"choice": "billing", "confidence": 0.97}
    t = c.post("/api/v1/settings/decisions/test", headers=h).json()
    assert t["ok"] and t["by"] == "llm" and t["choice"] == "billing"  # Laya can't run here, so the language model answered
    assert c.put("/api/v1/settings/decisions", headers=h, json={"laya_url": ""}).status_code == 200
