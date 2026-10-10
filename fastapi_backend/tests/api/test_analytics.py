"""Usage analytics: the numbers worked out locally, and Google Analytics off by default, opt-in, counts only."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest

from app.domain import __version__, analytics, routines, store
from tests.helpers import login, make_user

NOW = dt.datetime(2026, 10, 10, 12, tzinfo=dt.UTC)


def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def act(db, who, day, action="POST /api/v1/namespaces/{ns}/recordings", n=1):
    for _ in range(n):
        db.q("CREATE activity CONTENT $d", d={"at": f"{day}T10:00:00.000+00:00", "kind": "in", "actor": who, "action": action, "ok": True})


def account(db, uid, created):
    db.q("UPSERT $r CONTENT $d", r=store.R("account", uid), d={"email": f"u{uid}@x.io", "created_at": f"{created}T09:00:00+00:00"})


class Sent(list):
    state: dict


@pytest.fixture
def ga(monkeypatch):
    """A fake Google Analytics: records each request; `status` sets its answer."""
    sent = Sent()
    state = {"status": 204, "messages": []}

    def post(url, params=None, json=None, timeout=None):
        sent.append({"url": url, "params": params, "body": json})
        return httpx.Response(state["status"], json={"validationMessages": state["messages"]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(analytics.httpx, "post", post)
    sent.state = state
    return sent


def on(cfg, **more):
    cfg["analytics"].update(enabled=True, measurement_id="G-TEST1234", api_secret="s3cret", **more)
    return cfg


def names(sent):
    return [e["name"] for s in sent for e in s["body"]["events"]]


def test_feature_names_come_from_route_templates():
    assert analytics.feature("POST /api/v1/namespaces/{ns}/recordings") == "recordings"
    assert analytics.feature("PUT /api/v1/chats/{cid}") == "chats"
    assert analytics.feature("POST /api/v1/namespaces") == "namespaces"
    assert analytics.feature("POST /api/v1/{weird}") == "other"
    assert analytics.feature("") == "other"


def test_metrics_active_churn_retention(db):
    today = NOW.date()

    def d(n):
        return (today - dt.timedelta(days=n)).isoformat()

    # 1 and 2 were active in the previous 7 days; only 1 came back; 3 is new this week
    account(db, 1, d(40))
    account(db, 2, d(40))
    account(db, 3, d(2))
    act(db, 1, d(10))
    act(db, 2, d(9))
    act(db, 1, d(1), "PUT /api/v1/chats/{cid}", n=2)
    act(db, 3, d(0))
    db.q("CREATE audit_log CONTENT $d", d={"at": f"{d(0)}T08:00:00+00:00", "account": 1, "action": "login"})
    m = analytics.metrics(db, days=7, now=NOW)
    assert (m["since"], m["until"], len(m["daily"])) == (d(6), d(0), 7)
    assert m["daily"][-1] == {"day": d(0), "active_users": 2, "actions": 2}
    assert (m["dau"], m["wau"], m["mau"], m["active_users"], m["accounts"], m["new_users"]) == (2, 2, 3, 2, 3, 1)
    assert m["churn"] == {"previous_active": 2, "retained": 1, "churned": 1, "rate": 0.5, "returning": 1}
    assert m["stickiness"] == round((1 + 2) / 7 / 3, 4)
    assert [f["feature"] for f in m["features"]] == ["chats", "recordings"]
    assert m["features"][0] == {"feature": "chats", "actions": 2, "users": 1}
    assert (m["last_active_day"], m["days_since_active"]) == (d(0), 0)
    week = [c for c in m["retention"] if c["users"] == 1]
    assert week and week[-1]["retention"][0] == 1.0  # 3 was active in its sign-up week


def test_events_kept_locally_with_ga_off(db, cfg, ga):
    act(db, 1, (NOW.date() - dt.timedelta(days=1)).isoformat())
    assert cfg["analytics"]["enabled"] is False
    assert analytics.tick(db, cfg, NOW) == ["lens_install", "lens_daily_active", "lens_feature_used"]
    routines.run_due(db, cfg, log=None, now=NOW)  # once a day
    assert ga == []
    kept = analytics.recent_events(db)
    assert sorted(e["name"] for e in kept) == ["lens_daily_active", "lens_feature_used", "lens_install"]
    assert all(e["sent"] is None and e["day"] == "2026-10-09" for e in kept)
    # on without a secret isn't set up either, and turning it on later doesn't send what was kept while off
    cfg["analytics"].update(enabled=True, measurement_id="G-TEST1234")
    analytics.tick(db, cfg, NOW + dt.timedelta(days=1))
    on(cfg)
    analytics.tick(db, cfg, NOW + dt.timedelta(days=1, hours=1))
    assert ga == []


def test_daily_events_once_a_day_counts_only(db, cfg, ga):
    on(cfg)
    yesterday = (NOW.date() - dt.timedelta(days=1)).isoformat()
    act(db, 7, yesterday, n=3)
    act(db, 8, yesterday, "PUT /api/v1/chats/{cid}")
    db.q("UPSERT setup:wizard CONTENT {done_at: '2026-10-01T00:00:00+00:00', skipped: false}")
    got = analytics.tick(db, cfg, NOW)
    assert got == ["lens_install", "lens_setup_done", "lens_daily_active", "lens_feature_used", "lens_feature_used"]
    assert analytics.tick(db, cfg, NOW + dt.timedelta(hours=3)) == []  # once a day
    (req,) = ga
    assert req["url"] == "https://www.google-analytics.com/mp/collect"
    assert req["params"] == {"measurement_id": "G-TEST1234", "api_secret": "s3cret"}
    body = req["body"]
    assert body["client_id"] == analytics.client_id(db)
    assert body["user_properties"]["lens_version"] == {"value": __version__}
    ev = {e["name"]: e["params"] for e in body["events"]}
    assert ev["lens_daily_active"]["active_users"] == 2 and ev["lens_daily_active"]["actions"] == 4
    assert ev["lens_daily_active"]["engagement_time_msec"] == 1
    feats = [e["params"] for e in body["events"] if e["name"] == "lens_feature_used"]
    assert [(f["feature"], f["actions"], f["users"]) for f in feats] == [("recordings", 3, 1), ("chats", 1, 1)]
    # nothing personal: no emails, account ids or paths
    text = str(body)
    assert "x.io" not in text and "{cid}" not in text and "'actor'" not in text
    # the next day: no install or setup again; a new version is an upgrade
    db.q("UPDATE $r SET version = '0.0.1'", r=analytics.ROW)
    assert analytics.tick(db, cfg, NOW + dt.timedelta(days=1)) == ["lens_upgrade"]
    assert ga[-1]["body"]["events"][0]["params"]["from_version"] == "0.0.1"


def test_inactive_then_return(db, cfg, ga):
    on(cfg, inactive_days=5)
    act(db, 1, (NOW.date() - dt.timedelta(days=9)).isoformat())
    assert analytics.tick(db, cfg, NOW) == ["lens_install", "lens_inactive"]
    assert ga[-1]["body"]["events"][1]["params"]["days"] == 9
    assert "lens_inactive" not in analytics.tick(db, cfg, NOW + dt.timedelta(days=1))  # once per stretch
    act(db, 1, (NOW.date() + dt.timedelta(days=1)).isoformat())
    got = analytics.tick(db, cfg, NOW + dt.timedelta(days=2))
    assert "lens_return" in got and "lens_daily_active" in got


def test_events_can_be_left_out_and_failures_retry(db, cfg, ga):
    on(cfg, events=["lens_install"])
    act(db, 1, (NOW.date() - dt.timedelta(days=1)).isoformat())
    ga.state["status"] = 500
    analytics.tick(db, cfg, NOW)
    assert names(ga) == ["lens_install"]
    assert "500" in analytics.status(db, cfg)["last_error"]
    ga.state["status"] = 204
    analytics.tick(db, cfg, NOW + dt.timedelta(minutes=5))  # waits a while after a failure
    assert names(ga) == ["lens_install"]
    analytics.tick(db, cfg, NOW + dt.timedelta(minutes=11))  # then tries again
    assert names(ga) == ["lens_install", "lens_install"]
    analytics.tick(db, cfg, NOW + dt.timedelta(minutes=20))  # and sends each once
    assert len(ga) == 2
    kept = {e["name"]: e["sent"] for e in analytics.recent_events(db)}
    assert kept == {"lens_install": True, "lens_daily_active": None, "lens_feature_used": None}
    st = analytics.status(db, cfg)
    assert st["last_error"] is None and st["last_sent"] == ["lens_install"]


def test_settings_api_status_and_test(client, db, ga):
    h = admin(client, db)
    a = client.get("/api/v1/settings", headers=h).json()["analytics"]
    assert a["values"]["enabled"] is False and a["values"]["api_secret"] == {"secret": True, "set": False}

    def put(body):
        return client.put("/api/v1/settings/analytics", json=body, headers=h)

    for bad in ({"measurement_id": "UA-1234-1"}, {"events": ["page_view"]}, {"inactive_days": 0}, {"region": "mars"}, {"enabled": "yes"}):
        assert put(bad).status_code == 400, bad
    r = client.post("/api/v1/settings/analytics/test", headers=h).json()
    assert not r["ok"] and "measurement id" in r["error"]
    assert put({"measurement_id": "g-test1234", "api_secret": "s3cret", "region": "eu"}).status_code == 200
    a = client.get("/api/v1/settings", headers=h).json()["analytics"]["values"]
    assert a["measurement_id"] == "G-TEST1234" and a["api_secret"] == {"secret": True, "set": True}
    ga.state["messages"] = [{"description": "event name is bad"}]
    r = client.post("/api/v1/settings/analytics/test", headers=h).json()
    assert r == {"ok": False, "error": None, "messages": ["event name is bad"]}
    assert ga[-1]["url"] == "https://region1.google-analytics.com/debug/mp/collect"
    assert ga[-1]["body"]["events"][0]["name"] == "lens_test"
    s = client.get("/api/v1/settings/analytics/status", headers=h).json()
    assert s["enabled"] is False and s["configured"] is False and len(s["events"]) == len(analytics.EVENTS)
    assert all(e["on"] for e in s["events"])
    analytics.tick(db, client.app.state.settings.current())
    ev = client.get("/api/v1/admin/analytics/events", headers=h).json()
    assert [e["name"] for e in ev] == ["lens_install"] and ev[0]["sent"] is None
    m = client.get("/api/v1/admin/analytics?days=7", headers=h).json()
    assert m["days"] == 7 and m["accounts"] == 1 and m["dau"] == 1  # the sign-in above
    make_user(db, "ed@x.io", "ed password 12")
    h2 = login(client, "ed@x.io", "ed password 12")
    assert client.get("/api/v1/admin/analytics", headers=h2).status_code == 403


def test_setup_wizard_opt_in(client, db):
    h = admin(client, db)
    r = client.put("/api/v1/setup/telemetry", json={"enabled": False, "analytics": True}, headers=h)
    assert r.status_code == 400 and "measurement id" in r.json()["detail"]
    r = client.put(
        "/api/v1/setup/telemetry",
        json={"enabled": False, "analytics": True, "measurement_id": "G-TEST1234", "api_secret": "s3cret"},
        headers=h,
    )
    assert r.status_code == 200 and "analytics.enabled" in r.json()["saved"]
    v = client.get("/api/v1/setup", headers=h).json()["telemetry"]["analytics"]
    assert v == {"enabled": True, "measurement_id": "G-TEST1234", "secret_set": True, "locked": []}
    # off again keeps the id and secret, and no secret is needed to turn it back on
    client.put("/api/v1/setup/telemetry", json={"enabled": False, "analytics": False}, headers=h)
    assert client.put("/api/v1/setup/telemetry", json={"enabled": False, "analytics": True}, headers=h).status_code == 200
