"""The stream of activities (docs/automations.md, Contract): the activity shape, the kinds catalog, the event log,
following the stream once across processes, the timeline read newest first with who may see what, and tidying."""

from __future__ import annotations

import datetime as dt
import threading
import time

import pytest

from app.domain import events, match, store

R = store.R


@pytest.fixture(autouse=True)
def kinds():
    """Test kinds, gone again after each test; nothing this process handled or claimed carries over."""
    before = dict(events._KINDS)
    events.kind(
        "test_thing.happened",
        label="Thing happened",
        source="log",
        as_type="Announce",
        object_type="Note",
        fields={"object": {"type": "object", "properties": {"size": {"type": "integer", "title": "Size"}}}},
        untrusted=("object.name",),
    )
    events.kind("test_thing.ticked", label="Thing ticked", source="log", as_type="Update", object_type="Note", noisy=True)
    events._seen.clear()
    events._claimed.clear()
    yield
    events._KINDS.clear()
    events._KINDS.update(before)
    events._seen.clear()
    events._claimed.clear()


def at(seconds=0):
    """A time as store.now() writes it, `seconds` from now."""
    return (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def next_second():
    start = store.now()
    while store.now() == start:
        time.sleep(0.05)


def recording(db, sid, title, when, **extra):
    rid = db.next_id("recording")
    db.q("CREATE $r CONTENT $d", r=R("recording", rid), d={"space": sid, "title": title, "source": "audio", "created_at": when, **extra})
    return rid


def thing(db, name, space, **kw):
    return events.emit(
        db, "test_thing.happened", actor=events.LENS, obj={"type": "Note", "name": name}, space=space, summary=f"Lens noted {name}", **kw
    )


ADMIN = events.viewer(1, None, admin=True)


# ---------- the shape ----------
def test_activities_have_one_shape():
    sample = events.kind_info("asset.added")["sample"]
    assert events.check_activity(sample) is sample
    bad = [
        ({"id": "Create-7"}, "an activity's id"),
        ({"type": "Created"}, "type is one of Create"),
        ({"kind": "email.nope"}, "no kind email.nope"),
        ({"published": "2026-10-15T08:12:03+00:00"}, "published is an ISO 8601 time in UTC"),
        ({"actor": {"type": "Entity", "id": "lens:entity/1", "name": "x"}}, "the actor is an object whose type"),
        ({"actor": {"type": "Person", "id": "account/1", "name": "x"}}, "the actor's id is a Lens reference"),
        ({"actor": {"type": "Person", "id": "lens:account/1", "name": " "}}, "the actor has a name"),
        ({"object": {"id": "lens:recording/7"}}, "the object is an object with a type"),
        ({"tag": [{"id": "lens:entity/1"}]}, "tag is a list of at most 50 objects"),
        ({"summary": "x" * 301}, "the summary is one line"),
        ({"space": True}, "space is a namespace id"),
        ({"lens": {"pos": [1, 2]}}, "lens is an object naming the source"),
        ({"lens": {**sample["lens"], "untrusted": ["object subject"]}}, "lens.untrusted is a list of dotted paths"),
        ({"lens": {**sample["lens"], "cause": {"automation": 4}}}, "lens.cause is"),
        ({"lens": {**sample["lens"], "audience": "owner"}}, "names the owner's account"),
        ({"lens": {**sample["lens"], "audience": "friends"}}, "lens.audience is one of space, owner, admins"),
    ]
    for change, said in bad:
        with pytest.raises(ValueError, match=said):
            events.check_activity({**sample, **change})


def test_the_catalog_knows_each_kind_its_parents_and_fields():
    events.kind(
        "test_mail.received",
        label="Mail received",
        source="recordings",
        as_type="Create",
        object_type="Document",
        parent="asset.added",
        untrusted=("object.name", "object.subject"),
        fields={
            "object": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "title": "Subject"},
                    "from": {"type": "array", "title": "Sender", "items": {"type": "string", "title": "Address"}},
                },
            }
        },
    )
    events.kind(
        "test_mail.flagged", label="Flagged", source="recordings", as_type="Create", object_type="Document", parent="test_mail.received"
    )
    assert events.descendants("test_mail.received") == {"test_mail.received", "test_mail.flagged"}
    assert {"asset.added", "email.received", "test_mail.received", "test_mail.flagged"} <= events.descendants("asset.added")
    assert events.expand(["test_mail.received"]) == {"test_mail.received", "test_mail.flagged"} and events.expand(None) is None
    with pytest.raises(ValueError, match="no kind test_nope.x"):
        events.expand(["test_nope.x"])
    info = events.kind_info("asset.added")
    assert "test_mail.received" in info["children"] and info["source"] == "recordings" and info["type"] == "Create"
    assert info["untrusted"] == ["object.name"] and info["sample"]["kind"] == "asset.added"
    # a child's schema has every activity's fields, its parents' and its own
    assert events.field_at("test_mail.flagged", "object.subject")["title"] == "Subject"
    assert events.field_at("test_mail.flagged", "object.name")["title"] == "Title"
    assert events.field_at("test_mail.flagged", "object.from.0")["title"] == "Address"
    assert events.field_at("test_mail.flagged", "actor.name")["title"] == "Actor's name"
    assert events.field_at("test_mail.flagged", "object.nope") is None and events.field_at("test_nope.x", "object") is None
    assert {"asset.added", "test_thing.happened"} <= {k["name"] for k in events.kinds()}
    tree = {"all": [{"path": "object.subject", "op": "contains", "value": "trip"}, {"path": "object.weird", "op": "exists"}]}
    assert match.describe(tree, "test_mail.received") == "the subject contains “trip” and object.weird is set"
    bad = [
        (dict(name="Email.Received"), "named like noun.past_verb"),
        (dict(source=""), "name the source"),
        (dict(as_type="Sent"), "its type is one of"),
        (dict(parent="test_nope.x"), "register its parent test_nope.x first"),
        (dict(fields={"body": {}}), "fields are JSON Schemas"),
        (dict(untrusted=("object subject",)), "untrusted paths are dotted"),
        (dict(sample={**info["sample"], "kind": "asset.added"}), "its sample is a asset.added"),
        (dict(sample={**info["sample"], "kind": "test_bad.added", "summary": ""}), "the summary is one line"),
    ]
    for change, said in bad:
        args = {"name": "test_bad.added", "label": "Bad", "source": "log", "as_type": "Create", "object_type": "Note", **change}
        with pytest.raises(ValueError, match=said):
            events.kind(args.pop("name"), **args)
    assert "test_bad.added" not in events._KINDS  # a kind whose sample is wrong isn't left registered


# ---------- the event log ----------
def test_emit_logs_once_per_key_and_carries_its_cause(db, cfg):
    sid = store.ns_id(db, "pods")
    a = thing(db, "one", sid, key="hook-17")
    assert a.startswith("log-") and thing(db, "one again", sid, key="hook-17") == a  # a retry never doubles it
    assert len(db.values("SELECT VALUE id FROM event_log")) == 1
    with events.caused_by({"automation": 4, "run": 51, "depth": 1}):
        assert events.cause() == {"automation": 4, "run": 51, "depth": 1}
        b = thing(db, "two", sid, tag=[{"type": "Mention", "id": "lens:entity/9", "name": "St Mary's"}])
    assert events.cause() is None
    got = events.get(db, cfg, b, ADMIN)
    assert got["kind"] == "test_thing.happened" and got["type"] == "Announce" and got["actor"] == events.LENS
    assert got["lens"]["cause"] == {"automation": 4, "run": 51, "depth": 1} and got["lens"]["untrusted"] == ["object.name"]
    assert got["tag"][0]["id"] == "lens:entity/9" and got["summary"] == "Lens noted two" and got["published"].endswith("Z")
    assert events.check_activity(got) and events.get(db, cfg, a, ADMIN)["lens"]["cause"] is None
    with pytest.raises(ValueError, match="asset.added is read from its own rows"):
        events.emit(db, "asset.added", actor=events.LENS, obj={"type": "Audio"}, space=sid, summary="x")
    with pytest.raises(ValueError, match="no kind test_nope.x"):
        events.emit(db, "test_nope.x", actor=events.LENS, obj={"type": "Note"}, space=sid, summary="x")
    with pytest.raises(ValueError, match="a cause is"):
        with events.caused_by({"automation": 4}):
            pass


# ---------- following ----------
def test_following_reads_each_source_in_order_once(db, cfg):
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    recording(db, pods, "Before", at())
    assert events.follow(db, cfg, "test.a")[0] == []  # the first look only marks where it starts
    next_second()
    a1, a2 = thing(db, "one", pods), thing(db, "two", calls)
    late = db.next_id("recording")  # given out now, written after the looks below (a slow transaction)
    now = store.now()
    r1, r2 = recording(db, pods, "Call one", now), recording(db, calls, "Call two", now)  # equal times: the id breaks the tie
    got, commit = events.follow(db, cfg, "test.a", limit=1)  # one at a time from each source
    assert [a["id"] for a in got] == [a1, f"create-recording-{r1}"]
    commit()
    got, commit = events.follow(db, cfg, "test.a", limit=1)
    assert [a["id"] for a in got] == [a2, f"create-recording-{r2}"]
    commit()
    got, commit = events.follow(db, cfg, "test.a")
    assert got == []  # caught up: the lookback reads them again, but this process handled them already
    commit()
    events._seen.clear()  # another process: the lookback gives them again, and claims keep them from being done twice
    again, _ = events.follow(db, cfg, "test.a")
    assert {a["id"] for a in again} == {a1, a2, f"create-recording-{r1}", f"create-recording-{r2}"}
    assert [a["id"] for a in again if a["lens"]["source"] == "log"] == [a1, a2]  # oldest first, in each source's order
    # a row written as the last look ran (before the cursor, inside the lookback) is still seen
    db.q("CREATE $r CONTENT $d", r=R("recording", late), d={"space": pods, "title": "Late", "source": "audio", "created_at": now})
    got, _ = events.follow(db, cfg, "test.a", kinds=["asset.added"], spaces=[pods])
    assert f"create-recording-{late}" in {a["id"] for a in got} and {a["space"] for a in got} == {pods}
    assert all(a["kind"] == "asset.added" for a in got)
    # a consumer seen for the first time starts at now: nothing before replays, not even what the lookback would see
    assert events.follow(db, cfg, "test.b")[0] == []
    got, _ = events.follow(db, cfg, "test.b")
    assert got == []
    with pytest.raises(ValueError, match="a consumer is named"):
        events.follow(db, cfg, "Test B")


def test_a_kind_listened_to_later_does_not_replay_a_backlog(db, cfg):
    pods = store.ns_id(db, "pods")
    events.follow(db, cfg, "test.c", kinds=["test_thing.happened"])
    next_second()
    recording(db, pods, "Unwanted", store.now())
    got, commit = events.follow(db, cfg, "test.c", kinds=["test_thing.happened"])
    assert got == []  # recordings make no wanted kind: not read, and their cursor moves to now
    commit()
    next_second()
    events._seen.clear()
    got, _ = events.follow(db, cfg, "test.c", kinds=["asset.added"])
    assert got == []  # not the recording from before, not even through the lookback
    rid = recording(db, pods, "Wanted", at(1))
    got, commit = events.follow(db, cfg, "test.c", kinds=["asset.added"])
    assert [a["id"] for a in got] == [f"create-recording-{rid}"]
    events.idle(db, "test.c")  # nothing to do: every cursor at now
    assert db.one("SELECT pos FROM $c", c=R("event_cursor", "test.c"))["pos"]["recordings"][1] is None


def test_a_claim_is_won_once(db):
    wins = []

    def go():
        wins.append(events.claim(db, "test.d", "create-recording-7"))

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(wins) == [False] * 5 + [True]
    events._claimed.clear()  # another process, with nothing claimed in memory
    assert events.claim(db, "test.d", "create-recording-7") is False
    assert events.claim(db, "test.other", "create-recording-7") is True  # claims are per consumer
    events._claimed.clear()
    assert events.unclaimed(db, "test.d", ["create-recording-7", "create-recording-8"]) == ["create-recording-8"]


def test_wake_reaches_every_follower_waiting():
    g = events.generation()
    events.wake()  # a wake during the look still counts
    assert events.wait(5, since=g) is True
    woke = []
    waiters = [threading.Thread(target=lambda: woke.append(events.wait(5))) for _ in range(3)]
    for t in waiters:
        t.start()
    time.sleep(0.2)
    events.wake()
    for t in waiters:
        t.join()
    assert woke == [True, True, True]
    stop = threading.Event()
    stop.set()
    assert events.wait(5, stop) is False and events.wait(0.1) is False


# ---------- the timeline ----------
@pytest.fixture
def feed(db):
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    db.q("CREATE $r CONTENT $d", r=R("account", 7), d={"email": "sam@example.org", "name": "Sam"})
    db.q("CREATE $r CONTENT $d", r=R("storage_source", 3), d={"name": "Office share"})
    up = recording(db, pods, "Parents' evening", at(-50))
    db.q("CREATE upload CONTENT $d", d={"recording": up, "account": 7, "email": "sam@example.org"})
    share = recording(db, pods, "Minutes", at(-40), remote={"source": 3, "path": "minutes.docx"}, source="document")
    auto = recording(db, calls, "Call with the school", at(-30))
    db.q("CREATE job CONTENT $d", d={"recording": auto, "created_by": "automation:4", "created_at": at(-30)})
    ids = {
        "up": f"create-recording-{up}",
        "share": f"create-recording-{share}",
        "auto": f"create-recording-{auto}",
        "pods": thing(
            db, "school trip", pods, published=events.stamp(at(-45)), tag=[{"type": "Mention", "id": "lens:entity/9", "name": "St Mary's"}]
        ),
        "both": thing(db, "merge", calls, spaces=[pods, calls], published=events.stamp(at(-35))),
        "admins": thing(db, "backup", None, audience="admins", published=events.stamp(at(-20))),
        "mine": thing(db, "chat", None, audience="owner", owner=7, published=events.stamp(at(-10))),
        "nobody": thing(db, "system", None, published=events.stamp(at(-5))),
    }
    events.emit(db, "test_thing.ticked", actor=events.LENS, obj={"type": "Note"}, space=pods, summary="tick")
    return pods, calls, ids


def everything(db, cfg, who, flt=None, limit=2):
    out, before, pages = [], None, 0
    while True:
        p = events.page(db, cfg, who, before, flt, limit)
        out += [a["id"] for a in p["items"]]
        pages += 1
        if not p["next"]:
            return out, pages
        before = p["next"]
        assert pages < 50


def test_the_timeline_is_newest_first_and_shows_each_person_what_they_may_see(db, cfg, feed):
    pods, calls, ids = feed
    newest = ["nobody", "mine", "admins", "auto", "both", "share", "pods", "up"]
    got, pages = everything(db, cfg, ADMIN)
    assert got == [ids[k] for k in newest if k != "mine"] and pages >= 4  # an admin sees all but someone's own
    member = events.viewer(7, {pods})
    assert everything(db, cfg, member, limit=3)[0] == [ids[k] for k in ["mine", "both", "share", "pods", "up"]]
    other = events.viewer(8, {calls})
    assert everything(db, cfg, other, limit=50)[0] == [ids["auto"], ids["both"]]
    assert events.page(db, cfg, events.viewer(9, set()))["items"] == []
    # one at a time comes to the same, without repeats
    assert everything(db, cfg, ADMIN, limit=1)[0] == got
    assert events.get(db, cfg, ids["auto"], other)["object"]["name"] == "Call with the school"
    assert events.get(db, cfg, ids["auto"], member) is None and events.get(db, cfg, ids["admins"], member) is None
    assert events.get(db, cfg, "create-recording-999", ADMIN) is None and events.get(db, cfg, "Bad id", ADMIN) is None
    with pytest.raises(ValueError, match="before is the `next` of an earlier page"):
        events.page(db, cfg, ADMIN, "nope")


def test_recordings_are_asset_added_by_whoever_added_them(db, cfg, feed):
    _, _, ids = feed
    up, share, auto = (events.get(db, cfg, ids[k], ADMIN) for k in ("up", "share", "auto"))
    assert up["actor"] == {"type": "Person", "id": "lens:account/7", "name": "Sam"} and up["summary"] == "Sam added “Parents' evening”"
    assert share["actor"] == {"type": "Service", "id": "lens:source/3", "name": "Office share"}
    assert share["object"]["type"] == "Document" and share["object"]["media"] == "document"
    assert auto["actor"]["id"] == "lens:automation/4" and auto["actor"]["type"] == "Application"
    assert up["object"] == {
        "type": "Audio",
        "id": "lens:recording/" + ids["up"].split("-")[-1],
        "name": "Parents' evening",
        "url": "/resources/" + ids["up"].split("-")[-1],
        "media": "audio",
        "content_type": "audio",  # its content type as Lens recognises it, when nobody chose one
    }
    assert up["type"] == "Create" and up["kind"] == "asset.added" and up["lens"]["untrusted"] == ["object.name"]
    for a in (up, share, auto):
        events.check_activity(a)
    plain = recording(db, store.ns_id(db, "pods"), "Scanned", at(-1))
    assert events.get(db, cfg, f"create-recording-{plain}", ADMIN)["actor"] == events.LENS


def test_timeline_filters(db, cfg, feed):
    pods, calls, ids = feed

    def ids_of(flt, who=ADMIN, limit=50):
        return everything(db, cfg, who, flt, limit)[0]

    assert ids_of({"kinds": ["asset.added"]}) == [ids["auto"], ids["share"], ids["up"]]
    assert len(ids_of({"kinds": ["test_thing.ticked"]})) == 1  # a noisy kind only when named
    assert ids_of({"actor": "lens:automation/4"}) == [ids["auto"]]
    assert ids_of({"spaces": [pods]}) == [ids["both"], ids["share"], ids["pods"], ids["up"]]
    assert ids_of({"tag": "st mary's"}) == [ids["pods"]] and ids_of({"tag": "lens:entity/9"}) == [ids["pods"]]
    assert ids_of({"q": "school"}) == [ids["auto"], ids["pods"]]
    assert ids_of({"where": {"all": [{"path": "object.media", "op": "equals", "value": "document"}]}}) == [ids["share"]]
    a = events.get(db, cfg, ids["share"], ADMIN)["published"]
    b = events.get(db, cfg, ids["auto"], ADMIN)["published"]
    assert ids_of({"since": a, "until": b}) == [ids["auto"], ids["both"]]  # after since, up to and including until
    # filters that thin pages out read on, so a page isn't short while there is more
    assert ids_of({"q": "evening"}, limit=1) == [ids["up"]]
    bad = [
        ({"colour": "red"}, "no filter colour"),
        ({"kinds": ["test_nope.x"]}, "no kind test_nope.x"),
        ({"actor": "sam"}, "actor is a Lens reference"),
        ({"spaces": "pods"}, "spaces is a list of namespace ids"),
        ({"since": "last week"}, "since is a time"),
        ({"where": {"any": []}}, "where: any needs at least one condition"),
    ]
    for flt, said in bad:
        with pytest.raises(ValueError, match=said):
            events.page(db, cfg, ADMIN, flt=flt)


# ---------- keeping it small ----------
def test_tidy_drops_old_log_rows_and_claims(db, cfg):
    sid = store.ns_id(db, "pods")
    keep = thing(db, "recent", sid, key="k-recent")
    old = db.next_id("event_log")
    db.q("CREATE $r CONTENT $d", r=R("event_log", old), d={"kind": "test_thing.happened", "logged_at": at(-31 * 86400)})
    db.q("CREATE $r CONTENT $d", r=R("event_log_key", "old"), d={"log": old, "at": at(-31 * 86400)})
    events.claim(db, "test.e", "new")
    db.q("CREATE $r CONTENT $d", r=R("event_claim", "old"), d={"consumer": "test.e", "key": "old", "at": at(-2 * 86400)})
    assert events.tidy(db, cfg, now=at())
    assert db.values("SELECT VALUE record::id(id) FROM event_log") == [int(keep.split("-")[1])]
    assert len(db.values("SELECT VALUE id FROM event_log_key")) == 1
    assert db.values("SELECT VALUE key FROM event_claim") == ["new"]
    assert events.tidy(db, {**cfg, "events": {"keep_days": 1}}, now=at(2 * 86400)) and db.values("SELECT VALUE id FROM event_log") == []
