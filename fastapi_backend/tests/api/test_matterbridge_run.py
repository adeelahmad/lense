"""Matterbridge run by Lens: chat networks set in Settings → Chat rooms become the matterbridge service's config, a
gateway per room, and the bridge talks to that Matterbridge with a token of its own."""

from __future__ import annotations

import os
import pathlib

import pytest

from app.domain import bridge, matterbridge, settings
from tests import fake_matterbridge
from tests.helpers import login, make_user

QR = (pathlib.Path(__file__).parent.parent / "data" / "whatsapp-qr.txt").read_text(encoding="utf-8")


@pytest.fixture
def shared(tmp_path, monkeypatch):
    """The volume Lens shares with the matterbridge service, and its API at the fake's address."""
    srv, url = fake_matterbridge.start()
    monkeypatch.setenv("LENS_MATTERBRIDGE_DIR", str(tmp_path))
    monkeypatch.setenv("LENS_MATTERBRIDGE_URL", url)
    yield tmp_path
    srv.shutdown()
    fake_matterbridge.Handler.token = "mb-token"


def test_chat_networks_are_set_in_the_app_and_become_matterbridges_config(client, db, cfg, shared):
    make_user(db, "root@x.io", "root password 1", admin=True)
    a = login(client, "root@x.io", "root password 1")
    r = client.put(
        "/api/v1/settings/matterbridge",
        headers=a,
        json={
            "run": True,
            "slack_token": "xoxb-secret",
            "slack_channels": ["general", " #ops ", ""],
            "whatsapp_number": "+44 1234 567890",
            "whatsapp_groups": ["Family Chat"],
            "telegram_token": "123:abc",  # no chats: not in the config
        },
    )
    assert r.status_code == 200, r.text
    assert "xoxb-secret" not in client.get("/api/v1/settings", headers=a).text  # the tokens are secrets
    for bad in ({"whatsapp_number": "01234"}, {"matrix_server": "matrix.org"}, {"slack_channels": "general"}, {"slack_token": "a\nb"}):
        assert client.put("/api/v1/settings/matterbridge", headers=a, json=bad).status_code == 400, bad

    saved = settings.Settings(db, cfg).current()
    assert saved["matterbridge"]["whatsapp_number"] == "+441234567890" and saved["matterbridge"]["slack_channels"] == ["general", "#ops"]
    assert [g[0] for g in matterbridge.gateways(saved)] == ["slack-general", "slack-ops", "whatsapp-family-chat"]
    assert matterbridge.write(saved) is None
    toml = (shared / "matterbridge.toml").read_text()
    assert oct(os.stat(shared / "matterbridge.toml").st_mode & 0o777) == "0o600"  # tokens: for Matterbridge only
    assert '[slack.lens]\nToken="xoxb-secret"' in toml and "[telegram.lens]" not in toml
    assert f'Token="{matterbridge.api_token(saved)}"' in toml and 'name="slack-ops"' in toml and 'channel="#ops"' in toml
    assert f'SessionFile="{shared}/whatsapp"' in toml  # paired once, kept in the volume
    assert toml.count('account="api.lens"') == 3  # Lens reads and answers every room through the API
    mtime = os.stat(shared / "matterbridge.toml").st_mtime_ns
    assert matterbridge.write(saved) is None and os.stat(shared / "matterbridge.toml").st_mtime_ns == mtime  # unchanged: not rewritten

    # the page shows the rooms (for bridge.rooms) and, while WhatsApp waits to be paired, its QR code
    (shared / "matterbridge.log").write_text("level=info msg=Connecting\n" + QR + "\n")
    s = client.get("/api/v1/settings/bridge", headers=a).json()["matterbridge"]
    assert [g["gateway"] for g in s["gateways"]] == ["slack-general", "slack-ops", "whatsapp-family-chat"]
    assert s["whatsapp_qr"] == QR.rstrip("\n") and s["error"] is None
    (shared / "matterbridge.log").write_text(QR + '\nlevel=info msg="WhatsApp connection successful"\n')
    assert client.get("/api/v1/settings/bridge", headers=a).json()["matterbridge"]["whatsapp_qr"] is None


def test_the_bridge_talks_to_the_matterbridge_lens_runs(db, cfg, shared):
    make_user(db, "root@x.io", "root password 1", admin=True)
    cfg["bridge"].update(enabled=True, url=None, token=None, account="root@x.io")
    assert bridge.ready(cfg) == "no Matterbridge API address"
    cfg["matterbridge"].update(run=True, telegram_token="123:abc")
    assert bridge.check(db, cfg) == "add a chat network and at least one of its rooms"  # Matterbridge needs a room
    cfg["matterbridge"]["telegram_chats"] = ["-100200300"]
    fake_matterbridge.Handler.token = matterbridge.api_token(cfg)
    assert bridge.ready(cfg) is None and bridge.check(db, cfg) is None
    assert 'name="telegram-100200300"' in (shared / "matterbridge.toml").read_text()
    cfg["ai"]["tools"] = False
    fake_matterbridge.Handler.waiting = [fake_matterbridge.said("Lens, hi", gateway="telegram-100200300")]
    fake_matterbridge.Handler.posted = []
    assert bridge.tick(db, cfg, cfg, "api-1") == 1
    assert fake_matterbridge.Handler.posted[-1]["gateway"] == "telegram-100200300"
    # outside Compose there's nowhere to write it, and the bridge says so
    os.environ.pop("LENS_MATTERBRIDGE_DIR")
    assert "Docker Compose" in bridge.check(db, cfg)
