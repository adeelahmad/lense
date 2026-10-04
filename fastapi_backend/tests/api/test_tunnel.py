"""Reaching Lens from the internet through a Cloudflare Tunnel run by Lens: set up in Settings › Remote access, one
server process runs cloudflared, the address shows in the app, and passkeys and email links use the tunnel's https
address. cloudflared and Cloudflare's API are faked."""

from __future__ import annotations

import pathlib
import sys
import textwrap
import time

import pytest

from app import email
from app.domain import settings, tunnel
from tests.helpers import login, make_user

FAKE = textwrap.dedent(
    """\
    #!{python}
    import os, sys, time
    args = sys.argv[1:]
    print("cloudflared args:", " ".join(args), flush=True)
    if "--url" in args:
        print("INF |  https://tiny-blue-fox.trycloudflare.com  |", flush=True)
        if os.environ.get("FAKE_EDGE") == "blocked":
            print('ERR Failed to dial a quic connection error="timeout: no recent network activity" connIndex=0', flush=True)
            print("INF |  ERROR: Allow outbound QUIC traffic on port 7844 or use HTTP2.   |", flush=True)
            while True:
                time.sleep(0.2)
    elif os.environ.get("TUNNEL_TOKEN") != os.environ.get("FAKE_WANT", "tok-1"):
        print("ERR bad token", flush=True)
        sys.exit(1)
    print("INF Registered tunnel connection connIndex=0", flush=True)
    while True:
        time.sleep(0.2)
    """
)


@pytest.fixture
def cloudflared(tmp_path, monkeypatch):
    """A fake cloudflared on PATH: prints a trycloudflare address for --url, checks TUNNEL_TOKEN for run."""
    d = tmp_path / "bin"
    d.mkdir()
    exe = d / "cloudflared"
    exe.write_text(FAKE.format(python=sys.executable))
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{d}:{__import__('os').environ['PATH']}")
    return exe


def wait(fn, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out")


def runner(db, cfg, name="api-1"):
    return tunnel.Runner(db, lambda: settings.Settings(db, cfg).current(), name)


def test_remote_access_is_set_up_in_the_app(client, db, cfg):
    make_user(db, "root@x.io", "root password 1", admin=True)
    a = login(client, "root@x.io", "root password 1")
    s = client.get("/api/v1/settings/tunnel/status", headers=a).json()
    assert s["mode"] == "off" and s["url"] is None and not s["connected"]
    for bad in (
        {"mode": "sometimes"},
        {"mode": "token", "hostname": "lens.example.com"},  # no token
        {"mode": "token", "hostname": "not a host", "token": "tok-1"},
        {"mode": "managed", "hostname": "lens.example.com"},  # no API token
        {"mode": "quick", "origin": "frontend:3000"},
    ):
        r = client.put("/api/v1/settings/tunnel", headers=a, json=bad)
        assert r.status_code == 400, bad
    r = client.put("/api/v1/settings/tunnel", headers=a, json={"mode": "token", "hostname": "Lens.Example.com", "token": "tok-secret"})
    assert r.status_code == 200, r.text
    assert "tok-secret" not in client.get("/api/v1/settings", headers=a).text  # the token is a secret
    assert settings.Settings(db, cfg).current()["tunnel"]["token"] == "tok-secret"
    s = client.get("/api/v1/settings/tunnel/status", headers=a).json()
    assert s["mode"] == "token" and s["url"] == "https://lens.example.com" and not s["running"]
    # a change that keeps the saved token (the mask echoed back)
    assert (
        client.put("/api/v1/settings/tunnel", headers=a, json={"hostname": "lens2.example.com", "token": {"secret": True}}).status_code
        == 200
    )
    assert client.put("/api/v1/settings/tunnel", headers=a, json={"mode": "off"}).status_code == 200
    viewer = make_user(db, "v@x.io", "viewer password 1")
    assert viewer
    assert client.get("/api/v1/settings/tunnel/status", headers=login(client, "v@x.io", "viewer password 1")).status_code == 403


def test_a_quick_tunnel_runs_in_one_process_and_stops_when_turned_off(db, cfg, cloudflared):
    cfg["tunnel"] = {**cfg["tunnel"], "mode": "quick", "origin": "http://frontend:3000"}
    one, two = runner(db, cfg, "api-1"), runner(db, cfg, "api-2")
    try:
        one.tick()
        two.tick()  # the lease is taken: it doesn't run a second cloudflared
        assert one.proc and not two.proc
        s = wait(lambda: (st := tunnel.status(db, cfg))["connected"] and st)
        assert s["url"] == "https://tiny-blue-fox.trycloudflare.com" and s["process"] == "api-1"
        assert any("--url http://frontend:3000" in line for line in s["log"])
        assert tunnel.public_host(db) in ("tiny-blue-fox.trycloudflare.com", "")  # cached for a few seconds
        tunnel._SEEN.clear()
        assert tunnel.public_host(db) == "tiny-blue-fox.trycloudflare.com"
        cfg["tunnel"]["mode"] = "off"
        one.tick()
        assert one.proc is None and not tunnel.status(db, cfg)["running"]
    finally:
        one.stop_process()
        two.stop_process()
        tunnel._SEEN.clear()


def test_a_dashboard_token_is_passed_in_the_environment_and_a_bad_one_shows_why(db, cfg, cloudflared, monkeypatch):
    cfg["tunnel"] = {**cfg["tunnel"], "mode": "token", "hostname": "lens.example.com", "token": "wrong"}
    r = runner(db, cfg)
    try:
        r.tick()
        s = wait(lambda: (st := tunnel.status(db, cfg))["error"] and st)
        assert "bad token" in s["error"] and not s["connected"]
        assert not any("wrong" in line for line in s["log"])  # not on the command line
        cfg["tunnel"]["token"] = "tok-1"
        r.tick()  # a changed setting starts again straight away
        s = wait(lambda: (st := tunnel.status(db, cfg))["connected"] and st)
        assert s["url"] == "https://lens.example.com" and s["error"] is None
    finally:
        r.stop_process()


class FakeCloudflare:
    """Cloudflare's API, for one account with the zone example.com."""

    def __init__(self):
        self.tunnels, self.records, self.calls = {}, {}, []

    def __call__(self, token, method, path, **kw):
        self.calls.append((method, path))
        if token != "cf-api":
            raise tunnel.TunnelError("Cloudflare said: Invalid API Token")
        params, body = kw.get("params") or {}, kw.get("json")
        if path == "/zones":
            return [{"id": "z1", "name": "example.com", "account": {"id": "acc"}}] if params["name"] == "example.com" else []
        if path == "/accounts/acc/cfd_tunnel" and method == "GET":
            return [{"id": t} for t, v in self.tunnels.items() if v["name"] == params["name"]]
        if path == "/accounts/acc/cfd_tunnel" and method == "POST":
            tid = f"t{len(self.tunnels) + 1}"
            self.tunnels[tid] = {"name": body["name"]}
            return {"id": tid}
        if path.endswith("/configurations"):
            self.tunnels[path.split("/")[4]]["config"] = body["config"]
            return {}
        if path.endswith("/token"):
            return "tok-1"
        if path == "/zones/z1/dns_records" and method == "GET":
            return [dict(r, id=i) for i, r in self.records.items() if r["name"] == params["name"]]
        if path == "/zones/z1/dns_records" and method == "POST":
            self.records[f"r{len(self.records) + 1}"] = body
            return {}
        if path.startswith("/zones/z1/dns_records/") and method == "PUT":
            self.records[path.rsplit("/", 1)[1]] = body
            return {}
        raise AssertionError((method, path))


def test_a_managed_tunnel_is_made_routed_and_given_a_dns_record(db, cfg, cloudflared, monkeypatch):
    cf = FakeCloudflare()
    monkeypatch.setattr(tunnel, "_api", cf)
    cfg["tunnel"] = {
        **cfg["tunnel"],
        "mode": "managed",
        "hostname": "lens.example.com",
        "api_token": "cf-api",
        "origin": "http://frontend:3000",
    }
    r = runner(db, cfg)
    try:
        r.tick()
        s = wait(lambda: (st := tunnel.status(db, cfg))["connected"] and st)
        assert s["url"] == "https://lens.example.com"
        assert cf.tunnels == {
            "t1": {
                "name": "lens-lens-example-com",
                "config": {
                    "ingress": [{"hostname": "lens.example.com", "service": "http://frontend:3000"}, {"service": "http_status:404"}]
                },
            }
        }
        assert list(cf.records.values()) == [
            {"type": "CNAME", "name": "lens.example.com", "content": "t1.cfargotunnel.com", "proxied": True, "comment": "Lens tunnel"}
        ]
        saved = tunnel.state(db)["managed"]
        assert saved["tunnel_id"] == "t1" and saved["token"] != "tok-1"  # the tunnel's token is kept sealed
        # started again (another process, the same settings): the saved tunnel is used, nothing is made again
        r.stop_process()
        n = len(cf.calls)
        assert r._managed(settings.Settings(db, cfg).current())["token"] == "tok-1" and len(cf.calls) == n
    finally:
        r.stop_process()


def test_a_managed_tunnel_wont_take_over_a_hostname_in_use(cfg, monkeypatch):
    cf = FakeCloudflare()
    cf.records["r1"] = {"type": "A", "name": "lens.example.com", "content": "203.0.113.5"}
    monkeypatch.setattr(tunnel, "_api", cf)
    cfg["tunnel"] = {**cfg["tunnel"], "mode": "managed", "hostname": "lens.example.com", "api_token": "cf-api"}
    with pytest.raises(tunnel.TunnelError, match="already has a A record"):
        tunnel.provision(cfg, "lens.example.com", "http://frontend:3000")
    with pytest.raises(tunnel.TunnelError, match="none of your Cloudflare domains"):
        tunnel.provision(cfg, "lens.other.org", "http://frontend:3000")
    cfg["tunnel"]["api_token"] = "nope"
    with pytest.raises(tunnel.TunnelError, match="Invalid API Token"):
        tunnel.provision(cfg, "lens.example.com", "http://frontend:3000")


def test_passkeys_and_email_links_use_the_tunnels_https_address(app, client, db, cfg):
    cfg["tunnel"] = {**cfg["tunnel"], "mode": "token", "hostname": "lens.example.com", "token": "tok-1"}
    # cloudflared reaches the web app over http; the browser is on https://lens.example.com
    o = client.post("/api/v1/auth/passkey/options", headers={"x-forwarded-host": "lens.example.com", "x-forwarded-proto": "http"})
    assert o.status_code == 200 and o.json()["options"]["rpId"] == "lens.example.com"
    assert email.app_url(cfg) == "https://lens.example.com"
    cfg["notifications"] = {**cfg["notifications"], "app_url": "https://archive.example.org"}
    assert email.app_url(cfg) == "https://archive.example.org"  # the address set for notifications wins
    # a quick tunnel's address counts while it's up
    cfg["tunnel"] = {**cfg["tunnel"], "mode": "quick", "hostname": ""}
    db.q(
        "UPSERT $r MERGE {owner: 'api-1', at: $t, url: 'https://tiny-blue-fox.trycloudflare.com'}",
        r=tunnel.ROW,
        t=tunnel._ts(),
    )
    tunnel._SEEN.clear()
    try:
        o = client.post(
            "/api/v1/auth/passkey/options", headers={"x-forwarded-host": "tiny-blue-fox.trycloudflare.com", "x-forwarded-proto": "http"}
        )
        assert o.json()["options"]["rpId"] == "tiny-blue-fox.trycloudflare.com"
        o = client.post(
            "/api/v1/auth/passkey/options", headers={"x-forwarded-host": "other.trycloudflare.com", "x-forwarded-proto": "https"}
        )
        assert o.json()["options"]["rpId"] == "localhost"
    finally:
        tunnel._SEEN.clear()


def test_cloudflared_is_fetched_when_its_not_installed(cfg, monkeypatch, tmp_path):
    import httpx

    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    assert tunnel.binary(cfg) is None
    monkeypatch.setattr(tunnel.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tunnel.platform, "machine", lambda: "aarch64")
    seen = []

    class Stream:
        def __init__(self, method, url, **kw):
            seen.append(url)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield b"#!/bin/sh\n"

    monkeypatch.setattr(httpx, "stream", Stream)
    path = tunnel.fetch_binary(cfg)
    assert seen == [tunnel.RELEASES + "cloudflared-linux-arm64"]
    assert pathlib.Path(path).read_bytes() == b"#!/bin/sh\n" and tunnel.binary(cfg) == path


def test_while_cloudflare_cant_be_reached_it_says_why(db, cfg, cloudflared, monkeypatch):
    monkeypatch.setenv("FAKE_EDGE", "blocked")
    cfg["tunnel"] = {**cfg["tunnel"], "mode": "quick"}
    r = runner(db, cfg)
    try:
        r.tick()
        s = wait(lambda: (st := tunnel.status(db, cfg))["error"] and "7844" in st["error"] and st)
        assert s["running"] and not s["connected"] and s["url"] == "https://tiny-blue-fox.trycloudflare.com"
        assert s["error"] == "Allow outbound QUIC traffic on port 7844 or use HTTP2."
    finally:
        r.stop_process()
