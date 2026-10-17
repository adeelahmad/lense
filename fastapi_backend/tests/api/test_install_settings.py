"""Changing how install.sh set the machine up, from the web app: whether other machines on the network reach the web
app, and the edition (full or lean). The machine's upgrade agent runs install.sh again for the version running with
LENS_PUBLIC or LENS_TARGET, as Settings › HTTPS does with LENS_HTTPS (docs/updates.md)."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import time

import pytest

from app.domain import updates
from tests.helpers import login, make_user

AGENT = pathlib.Path(__file__).resolve().parents[3] / "packaging" / "host" / "lens-upgrade.sh"


@pytest.fixture
def control(tmp_path, monkeypatch):
    """The control folder install.sh shares with the containers, with an upgrade agent watching it, and Lens started
    the way docker-compose.prod.yml starts it: this machine only, the full edition."""
    d = tmp_path / "control"
    (d / "inbox").mkdir(parents=True)
    (d / "agent").write_text("kind=systemd\n")
    monkeypatch.setenv("LENS_CONTROL_DIR", str(d))
    monkeypatch.setenv("LENS_BIND", "127.0.0.1")
    monkeypatch.setenv("LENS_TARGET", "full")
    return d


def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def handed_over(control):
    """The request in the agent's inbox, once the web app has handed it over (after the response)."""
    f = control / "inbox" / "upgrade"
    for _ in range(100):
        if f.exists():
            break
        time.sleep(0.02)
    return dict(line.split("=", 1) for line in f.read_text().splitlines())


def audit(db):
    return [r["detail"] for r in db.rows("SELECT detail FROM audit_log WHERE action = 'lens.install'")]


@pytest.mark.parametrize(
    ("bind", "public"),
    [
        ("0.0.0.0", True),
        ("::", True),
        ("192.168.1.20", True),  # this machine's address on the network
        ("127.0.0.1", False),
        ("::1", False),
        ("[::1]", False),
        ("localhost", False),
        ("", None),  # not started by install.sh or docker-compose.prod.yml: not known
        ("lens.lan", None),
    ],
)
def test_who_reaches_the_web_app_is_read_from_where_it_listens(monkeypatch, bind, public):
    monkeypatch.setenv("LENS_BIND", bind)
    monkeypatch.delenv("LENS_TARGET", raising=False)
    assert updates.installed() == {"public": public, "target": None}


def test_how_it_runs_is_shown_with_the_line_for_each_choice(client, db, monkeypatch):
    monkeypatch.delenv("LENS_CONTROL_DIR", raising=False)
    monkeypatch.delenv("LENS_BIND", raising=False)
    monkeypatch.delenv("LENS_TARGET", raising=False)
    h = admin(client, db)
    s = client.get("/api/v1/admin/install", headers=h).json()
    assert (s["public"], s["target"], s["agent"], s["reconfigure"]) == (None, None, None, None)  # unknown, not guessed
    m = s["manual"]
    assert m["public"].endswith("| LENS_REF=main LENS_PUBLIC=1 sh") and m["private"].endswith("LENS_PUBLIC=0 sh")
    assert m["full"].endswith("LENS_TARGET=full sh") and m["lean"].endswith("LENS_TARGET=lean sh")
    monkeypatch.setenv("LENS_BIND", "0.0.0.0")
    monkeypatch.setenv("LENS_TARGET", "lean")
    s = client.get("/api/v1/admin/install", headers=h).json()
    assert (s["public"], s["target"]) == (True, "lean")
    monkeypatch.setenv("LENS_TARGET", "medium")
    assert client.get("/api/v1/admin/install", headers=h).json()["target"] is None

    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    he = login(client, "ed@x.io", "editor password 1")
    assert client.get("/api/v1/admin/install", headers=he).status_code == 403
    assert client.put("/api/v1/admin/install", headers=he, json={"public": True}).status_code == 403


def test_the_agent_sets_up_the_version_running_with_the_change(client, db, control):
    h = admin(client, db)
    s = client.get("/api/v1/admin/install", headers=h).json()
    assert (s["public"], s["target"], s["agent"]) == (False, "full", "systemd")

    r = client.put("/api/v1/admin/install", headers=h, json={"public": True})
    assert r.status_code == 202, r.text
    req = handed_over(control)
    # the version running (empty ref), set up again with LENS_PUBLIC=1 and nothing else
    assert (req["ref"], req["public"], req["by"]) == ("", "1", "root@x.io")
    assert "target" not in req and "https" not in req
    run = client.get("/api/v1/admin/install", headers=h).json()["reconfigure"]
    assert (run["public"], run["target"], run["state"], run["id"]) == (True, None, "queued", req["id"])
    assert audit(db) == [{"public": True}]
    # it isn't HTTPS's run, nor HTTPS's an install change
    assert client.get("/api/v1/settings/https/status", headers=h).json()["reconfigure"] is None

    # one run at a time
    r = client.put("/api/v1/admin/install", headers=h, json={"target": "lean"})
    assert r.status_code == 409 and "under way" in r.json()["detail"]

    # the agent says how it went; then the edition, and both at once
    (control / "inbox" / "upgrade").unlink()
    (control / "upgrade-status").write_text(f"id={req['id']}\nref=main\nstate=done\ncode=0\n")
    assert client.get("/api/v1/admin/install", headers=h).json()["reconfigure"]["state"] == "done"
    r = client.put("/api/v1/admin/install", headers=h, json={"target": "lean", "public": True})
    assert r.status_code == 202
    req = handed_over(control)
    assert (req["ref"], req["public"], req["target"]) == ("", "1", "lean")  # Lens still runs as it was started
    assert sorted(audit(db), key=len) == [{"public": True}, {"public": True, "target": "lean"}]


def test_what_is_so_already_restarts_nothing(client, db, control):
    h = admin(client, db)
    r = client.put("/api/v1/admin/install", headers=h, json={"public": False, "target": "full"})
    assert r.status_code == 200 and r.json()["reconfigure"] is None
    time.sleep(0.1)
    assert not (control / "inbox" / "upgrade").exists() and audit(db) == []
    r = client.put("/api/v1/admin/install", headers=h, json={"public": False, "target": "lean"})  # only what differs
    assert r.status_code == 202
    req = handed_over(control)
    assert req["target"] == "lean" and "public" not in req


def test_what_it_takes(client, db, control):
    h = admin(client, db)
    for body in ({}, {"public": None}, {"target": "medium"}, {"https": True}, {"public": "maybe"}):
        assert client.put("/api/v1/admin/install", headers=h, json=body).status_code == 422, body
    assert not (control / "inbox" / "upgrade").exists()
    with pytest.raises(updates.UpdateError):
        updates.reconfigure(db, {}, by="x")  # nothing to change
    with pytest.raises(updates.UpdateError):
        updates.manual_reconfigure({"updates": {}}, target="lean; rm -rf /")


def test_no_agent_gives_the_line_to_run(client, db, control, monkeypatch):
    monkeypatch.delenv("LENS_CONTROL_DIR")
    h = admin(client, db)
    r = client.put("/api/v1/admin/install", headers=h, json={"target": "lean"})
    assert r.status_code == 409
    assert "no upgrade agent" in r.json()["detail"] and r.json()["detail"].endswith("LENS_REF=main LENS_TARGET=lean sh")
    assert audit(db) == []


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_the_agent_passes_the_change_to_install_sh(tmp_path):
    """packaging/host/lens-upgrade.sh, with an install.sh that writes down what it was given."""
    d = tmp_path / "lens"
    d.mkdir()
    (d / "install.sh").write_text(
        'echo "ref=$LENS_REF https=$LENS_HTTPS public=${LENS_PUBLIC-unset} target=$LENS_TARGET" > "$LENS_DIR/ran"\n'
    )
    (d / ".env").write_text("LENS_HTTPS=0\nLENS_TARGET=full\nLENS_BIND=127.0.0.1\n")
    git = ["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@x.io"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    (d / ".lens-control" / "inbox").mkdir(parents=True)

    def run(request):
        (d / ".lens-control" / "inbox" / "upgrade").write_text(request)
        subprocess.run(["sh", str(AGENT), str(d)], check=True, capture_output=True)
        return (d / "ran").read_text().strip(), (d / ".lens-control" / "upgrade-status").read_text()

    ran, st = run("id=b1\nref=\npublic=1\ntarget=lean\n")
    assert ran == "ref=main https=0 public=1 target=lean" and "state=done" in st
    # what the request doesn't change is as .env has it: an empty LENS_PUBLIC is install.sh's own fallback to LENS_BIND
    ran, _ = run("id=b2\nref=\nhttps=1\n")
    assert ran == "ref=main https=1 public= target=full"
    ran, _ = run("id=b3\nref=\npublic=0\n")
    assert ran == "ref=main https=0 public=0 target=full"
    ran, _ = run("id=b4\nref=\npublic=yes; rm -rf /\ntarget=../../x\n")  # nothing but 0 or 1, full or lean
    assert ran == "ref=main https=0 public= target=full"
