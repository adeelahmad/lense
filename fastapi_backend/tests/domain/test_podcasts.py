"""Podcasts: picked sources become numbered excerpts, the model's outline and script keep only citations to them, the
fact-check rewrites or drops what they don't support, and the episode is a recording like any other."""

from __future__ import annotations

import pytest

from app.domain import auth, jobs, podcasts, search, settings, store, templates
from tests import fake_llm
from tests.helpers import drain, make_user, seed

R = store.R


@pytest.fixture
def llm(cfg):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    fake_llm.Handler.podcast_script = None
    yield fake_llm.Handler
    fake_llm.Handler.podcast_script = None
    srv.shutdown()


def _admin(db):
    uid = make_user(db, "admin@x.io", "admin password 1", admin=True)
    return uid, auth.roles(db, {"id": uid, "admin": True})


# ---------- selection ----------
def test_selection_is_cleaned():
    sel = podcasts.clean_selection(
        {"recordings": [3, 3, 1], "excerpts": [{"recording": 3, "idx": 2}, {"recording": 5, "idx": 1}, {"recording": 5, "idx": 1}]}
    )
    # a line of a recording picked whole adds nothing; the same line twice counts once
    assert sel == {"recordings": [3, 1], "excerpts": [{"recording": 5, "idx": 1}]}
    assert podcasts.selected_recordings(sel) == [1, 3, 5]
    with pytest.raises(podcasts.Problem):
        podcasts.clean_selection({})


def test_options_are_checked(cfg):
    assert podcasts.options(cfg) == {"length": 10, "style": "deep-dive", "prompt": None}
    assert podcasts.options(cfg, 5, "beginner", "  explain it simply ")["prompt"] == "explain it simply"
    for bad in ({"length": 0}, {"length": 31}, {"style": "opera"}, {"prompt": "x" * 2001}):
        with pytest.raises(podcasts.Problem):
            podcasts.options(cfg, **bad)


def test_runs_and_picking_within_a_budget():
    segs = [{"idx": i, "t0": i * 1000, "text": ("gpu " if i == 7 else "") + "word " * 20, "page": None} for i in range(10)]
    segs[5]["page"] = 1  # a page break starts a new run
    rs = podcasts.runs(segs, size=250)
    assert all(len(r) <= 3 for r in rs) and [s["idx"] for s in rs[0]] == [0, 1]
    assert any(r[0]["idx"] == 5 for r in rs)
    size = sum(len(s["text"]) + 1 for s in segs)
    assert podcasts.pick(rs, size * 2, []) == rs  # everything fits
    small = podcasts.pick(rs, 300, ["gpu"])
    # the run that mentions the prompt's word comes first; what's left of the budget spreads over the rest
    assert any(s["idx"] == 7 for r in small for s in r)
    assert sum(len(s["text"]) + 1 for r in small for s in r) <= 300
    spread = podcasts.pick(rs, size // 2, [])
    assert spread[0][0]["idx"] == 0 and spread[-1][-1]["idx"] > 5  # covers the start and the end
    assert spread == sorted(spread, key=lambda r: r[0]["idx"])


def test_resolve_numbers_excerpts_and_respects_access(db, cfg, folder):
    a, b, c = seed(db, cfg, folder)
    pods = store.ns_id(db, "pods")
    sel = podcasts.clean_selection({"recordings": [a], "excerpts": [{"recording": b, "idx": 1}, {"recording": c, "idx": 0}]})
    out = podcasts.resolve(db, cfg, sel, readable={pods})
    assert [x["n"] for x in out] == list(range(1, len(out) + 1))
    assert {x["ref"]["recording"] for x in out} == {a, b}  # the call is in a namespace this person can't read
    first = out[0]
    assert first["ref"] == {"kind": "segment", "recording": a, "idx0": 0, "idx1": first["ref"]["idx1"]}
    assert first["title"] == "ep1" and first["namespace"] == "pods" and first["at"] == "0:00" and first["picked"]
    assert first["text"].startswith("Alice: Welcome back.") and len(first["hash"]) == 16
    # a picked line comes with its neighbours (two each side), as one excerpt
    near = [x for x in out if x["ref"]["recording"] == b]
    assert len(near) == 1 and (near[0]["ref"]["idx0"], near[0]["ref"]["idx1"]) == (0, 3)
    with pytest.raises(podcasts.Problem):
        podcasts.resolve(db, cfg, podcasts.clean_selection({"recordings": [c]}), readable={pods})


# ---------- script ----------
def test_tidy_keeps_only_what_is_safe():
    lines, problems = podcasts.tidy(
        [
            {"speaker": "a", "kind": "claim", "text": "  Capsids   are designed. ", "citations": [1, 9, 1]},
            {"speaker": "c", "kind": "claim", "text": "Who?", "citations": []},
            {"speaker": "b", "kind": "rant", "text": "Hm.", "citations": [2]},
            {"speaker": "b", "kind": "question", "text": "", "citations": []},
            {"speaker": "b", "kind": "question", "text": "x" * 900, "citations": []},
        ],
        {1, 2},
    )
    assert lines[0] == {"speaker": "a", "kind": "claim", "text": "Capsids are designed.", "citations": [1], "idx": 0}
    assert lines[1]["kind"] == "claim" and lines[1]["citations"] == [2]  # an unknown kind with a citation states something
    assert len(lines[2]["text"]) == podcasts.LINE_CHARS and [l["idx"] for l in lines] == [0, 1, 2]
    assert problems == ["line 0: dropped citations to excerpts it wasn't given", "line 1: no speaker", "line 3: empty"]


def test_fact_check_rewrites_or_drops():
    L = lambda i, kind, text, cites: {"idx": i, "speaker": "a", "kind": kind, "text": text, "citations": cites}  # noqa: E731
    lines = [
        L(0, "banter", "Hi.", []),
        L(1, "claim", "Supported.", [1]),
        L(2, "claim", "Partly right.", [1]),
        L(3, "claim", "Wrong.", [2]),
        L(4, "claim", "Wrong, no fix.", [2]),
        L(5, "claim", "Uncited.", []),
        L(6, "claim", "Skipped by the checker.", [1]),
        L(7, "claim", "Wrong, fix fails.", [2]),
        L(8, "claim", "Partial, no fix.", [1]),
    ]
    verdicts = {
        1: {"line": 1, "verdict": "supported"},
        2: {"line": 2, "verdict": "partial", "text": "Right.", "citations": [1], "why": "only half"},
        3: {"line": 3, "verdict": "unsupported", "text": "Fixed.", "citations": [2, 7]},
        4: {"line": 4, "verdict": "unsupported"},
        7: {"line": 7, "verdict": "unsupported", "text": "Still wrong.", "citations": [2]},
        8: {"line": 8, "verdict": "partial"},
    }
    asked = []

    def second(new):
        asked.append(sorted(new))
        return {3: {"verdict": "supported"}, 7: {"verdict": "unsupported"}}

    kept, log = podcasts.apply_checks(lines, verdicts, {1, 2}, second)
    assert asked == [[3, 7]]  # only rewrites of unsupported lines are checked again
    assert [l["text"] for l in kept] == ["Hi.", "Supported.", "Right.", "Fixed.", "Skipped by the checker.", "Partial, no fix."]
    assert kept[3]["citations"] == [2]  # the fix's citation to an excerpt it wasn't given is dropped
    assert [(c["idx"], c["verdict"], c["action"]) for c in log] == [
        (2, "partial", "rewritten"),
        (3, "unsupported", "rewritten"),
        (4, "unsupported", "dropped"),
        (5, "unsupported", "dropped"),
        (7, "unsupported", "dropped"),
        (8, "partial", "kept"),
    ]
    assert log[0]["before"] == "Partly right." and log[0]["after"] == "Right." and log[3]["why"] == "no citation"


def test_timing_at_a_speaking_pace():
    lines = [{"text": "one two three"}, {"text": " ".join(["w"] * 150)}, {"text": "ok"}]
    assert podcasts.timed(lines, wpm=150, pause_ms=400) == [(0, 1200), (1600, 61600), (62000, 62800)]


# ---------- where it goes ----------
def test_target_namespace(db, cfg, folder):
    seed(db, cfg, folder)
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    uid, roles = _admin(db)
    # an admin's first episode makes the podcasts namespace
    sid, why = podcasts.target(db, cfg, {pods, calls}, roles, True)
    assert why == "podcasts" and store.space_names(db)[sid] == "podcasts"
    # someone who reads podcasts but not calls: an episode about the call stays with it
    vi = make_user(db, "vi@x.io", "viewer password 1", roles={"podcasts": "viewer", "pods": "viewer"})
    assert podcasts.target(db, cfg, {pods}, roles, True) == (sid, "podcasts")
    assert podcasts.target(db, cfg, {calls}, roles, True) == (calls, "sources")
    with pytest.raises(podcasts.Problem):
        podcasts.target(db, cfg, {pods, calls}, roles, True)
    # an editor of pods who can't add to podcasts keeps episodes in pods
    ed = make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    eroles = auth.roles(db, {"id": ed})
    assert podcasts.target(db, cfg, {pods}, eroles, False) == (pods, "sources")
    with pytest.raises(podcasts.Problem):
        podcasts.target(db, cfg, {calls}, eroles, False)
    # a vault's content never leaves it
    auth.set_role(db, vi, sid, None)
    db.q("UPSERT $r MERGE {vault: true}", r=R("data_key", pods))
    assert podcasts.target(db, cfg, {pods}, roles, True) == (pods, "sources")


# ---------- the whole run ----------
def test_episode_is_made_by_a_job(db, cfg, folder, llm):
    a, b, _c = seed(db, cfg, folder)
    uid, roles = _admin(db)
    out = podcasts.create(
        db, cfg, {"recordings": [a, b]}, by="admin@x.io", roles=roles, admin=True, prompt="how capsids are designed", length=5
    )
    rid = out["episode"]
    assert out["placed"] == "podcasts" and store.space_names(db)[out["namespace"]] == "podcasts"
    job = jobs.get(db, out["job"])
    assert [s.get("name") or s["type"] for s in job["steps"]] == [
        "gathering",
        "planning",
        "writing",
        "fact-checking",
        "publishing",
        "analyze",
        "embed",
        "summarize",
    ]
    rec = db.one("SELECT * FROM $r", r=R("recording", rid))
    assert rec["title"] == "Podcast: ep1" and rec["status"] == "new" and rec["engine"] == "podcast"
    assert (db.one("SELECT name FROM $c", c=R("collection", rec["collection"])) or {})["name"] == "Podcasts"

    drain(db, cfg)
    assert jobs.get(db, out["job"])["status"] == "succeeded"
    ep = podcasts.view(db, rid)
    assert ep["status"] == "script_only" and ep["title"] == "Capsids and shipments"
    n = len(ep["sources"])
    assert n >= 2 and all(s["picked"] for s in ep["sources"])
    # the outline keeps only citations to excerpts the model was given, and connections that have one
    assert ep["outline"]["ideas"][0]["sources"] == [1] and [c["claim"] for c in ep["outline"]["connections"]] == [
        "Both are about Dyno Therapeutics"
    ]
    texts = [l["text"] for l in ep["lines"]]
    assert texts == [
        "Welcome back to the show.",
        "So what did Dyno Therapeutics build?",
        "They trained a model that designs the capsid itself.",
        "It beat the benchmark.",
        "The samples ship on Friday.",
        "So: designed capsids, and samples leave Friday.",
    ]
    assert ep["lines"][2]["sources"] == [{**ep["sources"][0]["ref"], "n": 1}]
    assert [(c["verdict"], c["action"]) for c in ep["checks"]] == [
        ("partial", "rewritten"),
        ("unsupported", "rewritten"),
        ("unsupported", "dropped"),
    ]
    assert ep["checks"][2]["before"] == "Nobody knows who funded it."

    # the episode is a recording: its lines are segments spoken by the hosts, searchable like anything else
    segs = db.rows("SELECT idx, t0, t1, text, speaker FROM segment WHERE recording = $r ORDER BY idx", r=rid)
    assert [s["text"] for s in segs] == texts and segs[1]["t0"] > segs[0]["t1"]
    names = {s["id"]: s["name"] for s in db.rows("SELECT record::id(id) AS id, name FROM speaker WHERE space = $s", s=out["namespace"])}
    assert [names[s["speaker"]] for s in segs[:2]] == ["Alex", "Sam"]
    rec = db.one("SELECT status, duration_ms, analyzed_at FROM $r", r=R("recording", rid))
    assert rec["status"] == "analyzed" and rec["duration_ms"] == segs[-1]["t1"] and rec["analyzed_at"]
    hits = search.search(db, "designs the capsid", recording=rid)["hits"]
    assert hits and hits[0]["recording_id"] == rid

    # the prompts are templates; a new version is what the next run uses
    tid = db.values("SELECT VALUE record::id(id) FROM template WHERE role = 'podcast.write'")[0]
    t = templates.get(db, tid)
    assert t["kind"] == "prompt" and "citations" in t["body"] and ep["templates"]["write"] == {"template": tid, "version": 1}
    templates.save_version(db, tid, t["body"] + "\nKeep it upbeat.", t["schema"], t["system"])
    jid = podcasts.regenerate(db, rid, by="admin@x.io")
    drain(db, cfg)
    assert jobs.get(db, jid)["status"] == "succeeded"
    assert podcasts.view(db, rid)["templates"]["write"]["version"] == 2
    asked = [
        m
        for m in llm.seen
        if "lines" in (((m.get("response_format") or {}).get("json_schema") or {}).get("schema") or {}).get("properties", {})
    ]
    assert asked[-1]["messages"][-1]["content"].count("Keep it upbeat.") == 1
    assert "how capsids are designed" in asked[-1]["messages"][-1]["content"]
    assert len(db.values("SELECT VALUE id FROM template WHERE role = 'podcast.write'")) == 1
    assert len(db.values("SELECT VALUE id FROM segment WHERE recording = $r", r=rid)) == len(texts)

    # deleting the recording deletes the episode
    from app.domain import deletion

    deletion.delete(db, cfg, rid)
    assert not db.one("SELECT id FROM $r", r=R("podcast", rid))
    assert podcasts.list_episodes(db) == []


def test_unusable_script_fails_the_job(db, cfg, folder, llm):
    a, _b, _c = seed(db, cfg, folder)
    _uid, roles = _admin(db)
    llm.podcast_script = [
        {"speaker": "a", "kind": "claim", "text": "Made up.", "citations": [999]},
        {"speaker": "b", "kind": "claim", "text": "Also made up.", "citations": []},
    ]
    out = podcasts.create(db, cfg, {"recordings": [a]}, roles=roles, admin=True)
    drain(db, cfg)
    job = jobs.get(db, out["job"])
    assert job["status"] == "failed" and "held up" in job["error"]
    ep = podcasts.view(db, out["episode"])
    assert ep["status"] == "failed" and ep["error"].startswith("fact-checking: ")


def test_no_llm_fails_with_a_reason(db, cfg, folder):
    a, _b, _c = seed(db, cfg, folder)
    _uid, roles = _admin(db)
    cfg["llm"].update(base_url=None, model=None)
    out = podcasts.create(db, cfg, {"recordings": [a]}, roles=roles, admin=True)
    drain(db, cfg)
    assert "no LLM is configured" in jobs.get(db, out["job"])["error"]


def test_podcast_step_skips_other_recordings(db, cfg, folder):
    a, _b, _c = seed(db, cfg, folder)
    said = []
    with pytest.raises(jobs.Skip):
        podcasts.step(db, cfg, a, said.append, {"stage": "gather"})
    with pytest.raises(ValueError):
        podcasts.step(db, cfg, a, said.append, {"stage": "sing"})


def test_create_checks_the_sources(db, cfg, folder):
    a, _b, c = seed(db, cfg, folder)
    _uid, roles = _admin(db)
    with pytest.raises(KeyError):
        podcasts.create(db, cfg, {"recordings": [a, 99999]}, roles=roles, admin=True)
    with pytest.raises(KeyError):
        podcasts.create(db, cfg, {"recordings": [c]}, roles=roles, admin=False, readable={store.ns_id(db, "pods")})


def test_settings(db, cfg):
    assert cfg["podcasts"]["namespace"] == "podcasts" and cfg["podcasts"]["host_a"] == "Alex"
    assert "podcast" in cfg["workers"]["steps"]
    settings.save(db, cfg, "podcasts", {"host_b": " Jo ", "context_chars": 12000, "model": ""}, "test")
    eff = settings.effective(db, cfg)["podcasts"]
    assert (eff["host_b"], eff["context_chars"], eff["model"]) == ("Jo", 12000, None)
    for bad in ({"context_chars": 10}, {"namespace": "Bad Name"}, {"host_a": ""}, {"max_minutes": 0}, {"voices": 1}):
        with pytest.raises(ValueError):
            settings.save(db, cfg, "podcasts", bad, "test")
