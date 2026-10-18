"""Looking back over the stream in an automation's If (docs/automations.md, Match trees: Looking back): `seen` finds
activities of some kinds in a window before now, with the same sender as the one being tested, in the automation's
namespaces only; it is refused anywhere but an If, and reads in words."""

from __future__ import annotations

import pytest

from app.domain import automation_spec, events, match

NOW = "2026-10-15T16:00:00Z"


@pytest.fixture(autouse=True)
def kinds():
    before = dict(events._KINDS)
    events.kind("test_mail.came", label="Mail came", source="log", as_type="Create", object_type="Note")
    events.kind("test_mail.sent", label="Mail sent", source="log", as_type="Create", object_type="Note")
    yield
    events._KINDS.clear()
    events._KINDS.update(before)


def mail(db, kind, sender, when, space=1, subject="Trip"):
    obj = {"type": "Note", "name": subject, "from": [sender]}
    return events.emit(db, kind, actor=events.LENS, obj=obj, space=space, summary=f"{sender}: {subject}", published=when)


def env(db, cfg, spaces=(1,), now=NOW):
    return {"db": db, "cfg": cfg, "now": now, "spaces": list(spaces), "account": 1}


def look(**s):
    return match.check_seen({"kinds": ["test_mail.came"], "within": "2d", **s})


def test_seen_finds_what_happened_in_the_window(db, cfg):
    mine = mail(db, "test_mail.came", "office@stmarys.org", "2026-10-15T15:00:00Z")
    mail(db, "test_mail.came", "office@stmarys.org", "2026-10-14T09:00:00Z", subject="Outing")
    mail(db, "test_mail.came", "office@stmarys.org", "2026-10-12T09:00:00Z")  # before the window
    mail(db, "test_mail.came", "club@example.com", "2026-10-15T10:00:00Z")
    mail(db, "test_mail.came", "office@stmarys.org", "2026-10-15T11:00:00Z", space=2)  # another namespace
    mail(db, "test_mail.came", "office@stmarys.org", "2026-10-15T17:00:00Z")  # after now
    event = events.get(db, cfg, mine, events.viewer(1, None, admin=True))
    ctx = {"event": event}
    e = env(db, cfg)
    every = look(count={"at_least": 10})  # it reads just enough to count, so ask for more than there is to list them
    assert [a["object"]["from"] for a in match.found(every, ctx, e)] == [["club@example.com"], ["office@stmarys.org"]]
    assert len(match.found(look(), ctx, e)) == 1
    same = look(same="object.from", count={"at_least": 1})
    assert [a["object"]["name"] for a in match.found(same, ctx, e)] == ["Outing"]  # not the one being tested
    assert match.seen(same, ctx, e)
    assert not match.seen(look(same="object.from", count={"at_least": 2}), ctx, e)
    assert match.seen(look(same="object.from", count={"at_least": 2}), ctx, env(db, cfg, spaces=(1, 2)))
    assert not match.seen(look(same="object.from", within="1h"), ctx, e)
    where = {
        "all": [{"path": "object.name", "op": "matches", "value": "trip|outing"}, {"path": "published", "op": "within", "value": "1d"}]
    }
    names = [a["summary"] for a in match.found(look(where=where), ctx, e)]
    assert names == ["club@example.com: Trip"]  # the outing is two days old, past a day of env's now
    names = [a["summary"] for a in match.found(look(where=where), ctx, {**e, "now": "2026-10-15T08:00:00Z"})]
    assert names == ["office@stmarys.org: Outing"]
    assert match.found(look(same="object.to"), ctx, e) == []  # the activity has nothing there: nothing is the same
    assert match.found(same, ctx, {**e, "db": None}) == []
    assert match.found(same, ctx, {**e, "spaces": []}) == []
    # "if I haven't replied within 2 days": none of the kind in the window
    no_reply = look(count={"at_most": 0})
    assert not match.seen(no_reply, ctx, e)
    assert match.seen(match.check_seen({"kinds": ["test_mail.sent"], "within": "2d", "count": {"at_most": 0}}), ctx, e)
    mail(db, "test_mail.sent", "me@home", "2026-10-15T12:00:00Z")
    assert not match.seen(match.check_seen({"kinds": ["test_mail.sent"], "within": "2d", "count": {"at_most": 0}}), ctx, e)


def test_an_if_looks_back_and_reads_the_context(db, cfg):
    mine = mail(db, "test_mail.came", "office@stmarys.org", "2026-10-15T15:00:00Z")
    mail(db, "test_mail.came", "office@stmarys.org", "2026-10-15T12:00:00Z")
    tree = automation_spec.check_if(
        {
            "all": [
                {"path": "event.object.name", "op": "equals", "value": "Trip"},
                {"seen": {"kinds": ["test_mail.came"], "within": "1d", "same": "object.from"}},
            ]
        }
    )
    ctx = {"event": events.get(db, cfg, mine, events.viewer(1, None, admin=True))}
    assert match.test(tree, ctx, env(db, cfg))
    assert not match.test(tree, ctx, env(db, cfg, now="2026-10-17T00:00:00Z"))
    assert not match.test({"not": tree["all"][1]}, ctx, env(db, cfg))
    assert match.paths(tree) == ["event.object.name"] and match.lookbacks(tree) == [tree["all"][1]["seen"]]


def test_seen_is_only_for_an_automations_if():
    s = {"seen": {"kinds": ["test_mail.came"], "within": "1d"}}
    with pytest.raises(ValueError, match="the condition: only an automation's If looks back"):
        match.check(s)
    with pytest.raises(ValueError, match="trigger mail's where, all.0: only an automation's If looks back"):
        automation_spec.check_trigger({"as": "mail", "on": "event", "kinds": ["test_mail.came"], "where": {"all": [s]}})
    assert automation_spec.check_if({"any": [s]}) == {
        "any": [{"seen": {"kinds": ["test_mail.came"], "within": "1d", "count": {"at_least": 1}}}]
    }
    bad = [
        ({"kinds": ["test_mail.came"]}, "seen says how far back it looks"),
        ({"kinds": [], "within": "1d"}, "seen's kinds lists 1 to 10 kinds"),
        ({"kinds": ["nope.never"], "within": "1d"}, "no kind nope.never"),
        ({"kinds": ["test_mail.came"], "within": "-1d"}, "seen's within can't point back"),
        ({"kinds": ["test_mail.came"], "within": "0s"}, "seen's within is more than 0"),
        ({"kinds": ["test_mail.came"], "within": "1d", "same": "a b"}, "seen's same is a path"),
        ({"kinds": ["test_mail.came"], "within": "1d", "count": {"at_least": 0}}, "needs at_least 1 or more"),
        ({"kinds": ["test_mail.came"], "within": "1d", "count": {"at_least": 3, "at_most": 2}}, "at_least is at most its at_most"),
        ({"kinds": ["test_mail.came"], "within": "1d", "count": {"exactly": 2}}, "seen's count is"),
        ({"kinds": ["test_mail.came"], "within": "1d", "count": {"at_least": 101}}, "from 0 to 100"),
        ({"kinds": ["test_mail.came"], "within": "1d", "where": {"path": "x", "op": "changed"}}, "can't test changed"),
        ({"kinds": ["test_mail.came"], "within": "1d", "where": {"seen": {}}}, "seen's where: only an automation's If"),
        ({"kinds": ["test_mail.came"], "within": "1d", "when": 1}, "seen has no when"),
    ]
    for spec, said in bad:
        with pytest.raises(ValueError, match=said):
            automation_spec.check_if({"seen": spec})
    with pytest.raises(ValueError, match="seen stands alone"):
        automation_spec.check_if({"seen": {"kinds": ["test_mail.came"], "within": "1d"}, "path": "x"})


def test_seen_reads_in_words():
    def words(**s):
        return match.describe(automation_spec.check_if({"seen": {"kinds": ["test_mail.came"], "within": "2d", **s}}))

    assert words() == "at least one of “Mail came” in the last 2 days"
    assert words(same="object.from", count={"at_least": 3}) == "at least 3 of “Mail came” in the last 2 days, with the same object.from"
    assert words(count={"at_most": 0}, where={"path": "object.name", "op": "contains", "value": "trip"}) == (
        "none of “Mail came” in the last 2 days, where the name contains “trip”"
    )
    assert words(count={"at_least": 1, "at_most": 4}) == "1 to 4 of “Mail came” in the last 2 days"
    negated = automation_spec.check_if({"not": {"seen": {"kinds": ["test_mail.came", "test_mail.sent"], "within": "1h"}}})
    assert match.describe(negated) == "none of “Mail came” or “Mail sent” in the last 1 hour"
