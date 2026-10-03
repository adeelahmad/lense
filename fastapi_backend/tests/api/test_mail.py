"""Email set up in the app: an SMTP server with its password kept secret, a test message to check it, and .env's
MAIL_* winning when set."""

from __future__ import annotations

import pytest

from app import email
from app.domain import settings
from tests.helpers import login, make_user


class Mailer:
    sent = []
    fail = None

    def __init__(self, cfg):
        self.cfg = cfg

    async def send_message(self, message, template_name=None):
        if Mailer.fail:
            raise RuntimeError(Mailer.fail)
        Mailer.sent.append((self.cfg["mail"]["server"], [r.email for r in message.recipients], message.subject, template_name))


@pytest.fixture
def admin(client, db, monkeypatch):
    Mailer.sent, Mailer.fail = [], None
    monkeypatch.setattr(email, "_mailer", Mailer)
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def test_email_is_set_up_and_checked_in_the_app(client, admin, app):
    h = admin
    assert client.post("/api/v1/settings/mail/test", headers=h).json() == {
        "ok": False,
        "to": None,
        "error": "set the SMTP server and the From address first",
    }
    r = client.put(
        "/api/v1/settings/mail",
        headers=h,
        json={
            "server": "smtp.example.org",
            "port": 465,
            "username": "lens",
            "password": "s3cret",
            "from_address": "lens@example.org",
            "security": "ssl",
        },
    )
    assert r.status_code == 200, r.text
    v = client.get("/api/v1/settings", headers=h)
    assert "s3cret" not in v.text and v.json()["mail"]["values"]["password"] == {"secret": True, "set": True}
    assert client.post("/api/v1/settings/mail/test", headers=h).json() == {"ok": True, "to": "root@x.io", "error": None}
    assert Mailer.sent[-1] == ("smtp.example.org", ["root@x.io"], "Lens can send email", None)
    Mailer.fail = "535 authentication failed"
    out = client.post("/api/v1/settings/mail/test", headers=h).json()
    assert not out["ok"] and "535 authentication failed" in out["error"]
    for bad in ({"port": 0}, {"from_address": "nobody"}, {"server": "smtp example"}, {"security": "tls13"}):
        assert client.put("/api/v1/settings/mail", headers=h, json=bad).status_code == 400, bad


def test_access_requests_are_emailed_with_the_apps_settings(client, admin, db, cfg, app):
    settings.save(db, cfg, "mail", {"server": "smtp.example.org", "from_address": "lens@example.org"}, "t")
    settings.save(db, cfg, "notifications", {"app_url": "https://lens.example.org"}, "t")
    import asyncio

    asyncio.run(email.send_access_request_email(app.state.settings.current(), ["owner@x.io"], "Vi", 7, "Budget", "pods", None))
    assert Mailer.sent[-1][:3] == ("smtp.example.org", ["owner@x.io"], "Vi asked for access to Budget")
    assert email.app_url(app.state.settings.current()) == "https://lens.example.org"


def test_mail_in_env_wins(monkeypatch, db, cfg):
    monkeypatch.setenv("MAIL_SERVER", "smtp.env.example")
    monkeypatch.setenv("MAIL_PORT", "2525")
    assert set(settings.locked("mail")) == {"server", "port"}
    eff = settings.effective(db, cfg)["mail"]
    assert (eff["server"], eff["port"]) == ("smtp.env.example", 2525)
