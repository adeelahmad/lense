"""The automation action that calls a web address (docs/automations.md, Actions): http_request reaches public addresses
(or networks an admin allowed) only, asks first unless the node runs on its own, waits 10 seconds at most, passes on
up to 64 KB of the answer as outside text, and keeps credentials in headers sealed to the automation and the host."""

from __future__ import annotations

import http.server
import json
import threading
import time

import pytest

from app.domain import actions, actions_web, keyring, store
from tests import fake_llm
from tests.api._assist import start_llm
from tests.helpers import make_user, seed


class Site:
    """A local web server: answers every request with `answer` (status, content type, body) and keeps what it got."""

    def __init__(self):
        self.got, self.answer, self.wait, self.location = [], (200, "application/json", b'{"temp": 21}'), 0, None
        site = self

        class H(http.server.BaseHTTPRequestHandler):
            def _any(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.got.append(
                    {"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}
                )
                time.sleep(site.wait)
                status, ctype, data = site.answer
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                if site.location:
                    self.send_header("Location", site.location)
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = _any  # noqa: N815 - the stdlib's names

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"


@pytest.fixture
def site():
    s = Site()
    yield s
    s.srv.shutdown()


@pytest.fixture
def app(cfg, db):
    from app.main import create_app

    srv = start_llm(cfg)
    yield create_app(cfg, db, background=False)
    fake_llm.Handler.usage = None
    srv.shutdown()


@pytest.fixture
def people(app, db, cfg, folder):
    seed(db, cfg, folder)
    cfg["notifications"] = {**(cfg.get("notifications") or {}), "networks": ["127.0.0.0/8"]}
    return {"me": make_user(db, "me@x.io", "my password 1", roles={"pods": "editor"}), "pods": store.ns_id(db, "pods")}


def run(db, cfg, people, params, dry=False, **kw):
    intent = {
        "key": "51-n4",
        "action": "http_request",
        "params": params,
        "effect": "out",
        "risk": "ask",
        "untrusted": False,
        "spaces": [people["pods"]],
        "run_as": people["me"],
        "node": "n4",
        "automation": 4,
        "run": 51,
        **kw,
    }
    return actions.perform(db, cfg, intent, None, dry)


def test_it_reaches_outside_lens_so_it_asks_unless_the_node_runs_on_its_own():
    spec = actions.get("http_request")
    assert spec.group == "Web" and actions.effect_of(spec, {}) == "out"
    assert actions.resolve_risk(spec, {"url": "https://x.example"}, {"node": "n4"}) == "ask"
    assert actions.resolve_risk(spec, {"url": "https://x.example"}, {"node": "n4"}, {"n4": "auto"}) == "routine"
    assert actions.check_params(spec, {"url": "https://x.example"}) == {"method": "GET", "url": "https://x.example", "headers": {}}
    assert actions.describe("http_request", {"method": "post", "url": "https://x.example/hook"}) == "POST https://x.example/hook"


def test_it_calls_an_address_and_passes_on_the_answer_as_outside_text(db, cfg, people, site):
    got = run(db, cfg, people, {"url": site.url + "/weather?city=Oslo", "headers": {"Accept": "application/json"}})
    assert got["status"] == 200 and got["ok"] and got["json"] == {"temp": 21} and got["body"] == '{"temp": 21}'
    assert got["untrusted"] and not got["truncated"] and got["headers"] == {"Accept": "application/json"}
    assert site.got[-1]["method"] == "GET" and site.got[-1]["path"] == "/weather?city=Oslo" and site.got[-1]["headers"]["user-agent"]
    # an object is sent as JSON, text as it is
    run(db, cfg, people, {"method": "POST", "url": site.url + "/hook", "body": {"subject": "Trip on Friday"}})
    assert site.got[-1]["headers"]["content-type"] == "application/json" and json.loads(site.got[-1]["body"]) == {
        "subject": "Trip on Friday"
    }
    site.answer = (201, "text/plain", b"made")
    got = run(db, cfg, people, {"method": "PUT", "url": site.url + "/x", "body": "plain words"})
    assert (
        site.got[-1]["method"] == "PUT"
        and site.got[-1]["body"] == b"plain words"
        and site.got[-1]["headers"]["content-type"].startswith("text/plain")
    )
    assert got["status"] == 201 and got["body"] == "made" and "json" not in got
    row = db.one("SELECT * OMIT id FROM activity WHERE action = 'automation.http' ORDER BY at DESC LIMIT 1")
    assert row["kind"] == "out" and row["detail"] == {"method": "PUT", "host": "127.0.0.1", "status": 201} and row["ok"]
    # a dry run sends nothing
    sent = len(site.got)
    got = run(db, cfg, people, {"method": "POST", "url": site.url + "/hook", "body": "x"}, dry=True)
    assert got["would"] == f"POST {site.url}/hook" and got["status"] is None and len(site.got) == sent


def test_the_answer_is_capped_redirects_are_not_followed_and_it_waits_ten_seconds_at_most(db, cfg, people, site, monkeypatch):
    site.answer = (200, "application/json", b"[" + b"1," * 40000 + b"1]")
    got = run(db, cfg, people, {"url": site.url})
    assert len(got["body"]) == 64 * 1024 and got["truncated"] and "json" not in got
    site.answer, site.location = (302, "text/plain", b""), "http://169.254.169.254/latest/meta-data/"
    got = run(db, cfg, people, {"url": site.url})
    assert got["status"] == 302 and not got["ok"] and len(site.got) == 2  # the redirect isn't followed
    site.answer, site.location, site.wait = (200, "text/plain", b"late"), None, 1.5
    monkeypatch.setattr(actions_web, "TIMEOUT", 1)
    with pytest.raises(ValueError, match="127.0.0.1 didn't answer within 1 seconds"):
        run(db, cfg, people, {"url": site.url})
    assert (
        actions_web.TIMEOUT == 1
        and not db.one("SELECT ok, at FROM activity WHERE action = 'automation.http' ORDER BY at DESC LIMIT 1")["ok"]
    )


def test_only_public_addresses_unless_an_admin_allowed_the_network(db, cfg, people, site):
    cfg["notifications"]["networks"] = []
    with pytest.raises(ValueError, match="127.0.0.1 isn't a public address"):
        run(db, cfg, people, {"url": site.url})
    assert not site.got
    for params, said in (
        ({"url": "ftp://x.example"}, "an http or https URL"),
        ({"url": "https://me:pw@x.example"}, "leave the user name and password out"),
        ({"url": "https://x.example", "method": "DELETE"}, "method is one of GET, POST, PUT"),
        ({"url": "https://x.example", "body": "x"}, "a GET sends no body"),
        ({"url": "https://x.example", "headers": {"Host": "intranet"}}, "Host isn't a header it can set"),
        ({"url": "https://x.example", "headers": {"X-A": "a\r\nX-B: b"}}, "the X-A header is one line"),
        ({"url": "https://x.example", "method": "POST", "body": "x" * (65 * 1024)}, "the body is over 64 KB"),
    ):
        with pytest.raises(ValueError, match=said):
            run(db, cfg, people, params, dry=True)


def test_credentials_in_headers_are_sealed_to_the_automation_and_the_host_and_never_shown(db, cfg, people, site):
    kept = actions_web.seal_headers(
        db, cfg, people["pods"], 4, site.url + "/hook", {"Authorization": "Bearer s3cret", "X-Api-Key": "k-1", "Accept": "text/plain"}
    )
    assert keyring.is_sealed(kept["Authorization"]) and keyring.is_sealed(kept["X-Api-Key"]) and kept["Accept"] == "text/plain"
    assert "s3cret" not in json.dumps(kept)
    assert actions_web.seal_headers(db, cfg, people["pods"], 4, site.url, kept) == kept  # sealed once
    got = run(db, cfg, people, {"method": "POST", "url": site.url + "/other-path", "headers": kept, "body": {"a": 1}})
    assert site.got[-1]["headers"]["authorization"] == "Bearer s3cret" and site.got[-1]["headers"]["x-api-key"] == "k-1"
    assert got["headers"] == {"Authorization": "••••", "X-Api-Key": "••••", "Accept": "text/plain", "Content-Type": "application/json"}
    assert "s3cret" not in json.dumps(got) and "s3cret" not in actions.describe("http_request", {"url": site.url, "headers": kept})
    # typed in plain, a credential is sent but still not shown
    got = run(db, cfg, people, {"url": site.url, "headers": {"Authorization": "Bearer typed"}})
    assert got["headers"] == {"Authorization": "••••"} and site.got[-1]["headers"]["authorization"] == "Bearer typed"
    # another automation, another host or another namespace can't use them
    sent = len(site.got)
    with pytest.raises(ValueError, match="sealed for another automation or address"):
        run(db, cfg, people, {"url": site.url, "headers": kept}, automation=5)
    with pytest.raises(ValueError, match="sealed for another automation or address"):
        run(db, cfg, people, {"url": site.url.replace("127.0.0.1", "localhost"), "headers": kept})
    calls = store.ns_id(db, "calls")
    with pytest.raises(ValueError, match="sealed for another namespace"):
        run(db, cfg, people, {"url": site.url, "headers": kept}, spaces=[calls])
    assert len(site.got) == sent
