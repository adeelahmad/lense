"""`lens ext`: extensions' manifests as files, pulled from Lens and pushed back, against this install's database or a
Lens server over its API with a token; neither side's changes are overwritten without --force."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from app import cli, cli_ext
from app.domain import auth, extensions
from tests.helpers import make_user

SKILL = """---
name: meeting_recap
kind: skill
description: Recaps a meeting
when: someone asks for a recap of a meeting
---

Find the meeting, read it, and answer with three bullets: decisions, owners, dates.
"""

# written by hand: no effect and no parameter kinds, which the check fills in
TOOL = """name: shout
kind: tool
description: Says it louder.
params:
  - name: text
run:
  type: prompt
  prompt: "Say this in capitals: {{text}}"
"""


def versions(db, name):
    return [x["current"] for x in extensions.visible(db, extensions.who(0, "x", True, {})) if x["name"] == name]


def test_pull_edit_and_push_on_this_install(tmp_path, monkeypatch, capsys):
    import io

    monkeypatch.delenv("SURREAL_URL", raising=False)
    (tmp_path / "archive.yaml").write_text("data_dir: ./data\nnamespaces:\n  pods:\n    paths: []\n")

    def run(*args):
        return cli.main(["--config", str(tmp_path / "archive.yaml"), "ext", *args])

    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    mine.mkdir()
    (mine / "meeting_recap.md").write_text(SKILL)
    (mine / "shout.yaml").write_text(TOOL)
    (mine / "README.md").write_text("# My extensions\n")  # not a manifest: left alone
    with pytest.raises(SystemExit, match="no admin account yet"):
        run("push", str(mine))
    assert run("check", str(mine)) == 0  # checking needs no account
    assert "ok meeting_recap.md: meeting_recap (skill)" in capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO("cli password 123\n"))
    cli.main(["--config", str(tmp_path / "archive.yaml"), "users", "add", "root@x.io", "--admin", "--password-stdin"])
    assert run("push", str(mine)) == 0
    out = capsys.readouterr().out
    assert "created meeting_recap (skill, id" in out and "created shout (tool, id" in out
    assert run("push", str(mine)) == 0  # nothing changed, though the tool file leaves out what the check fills in
    assert capsys.readouterr().out.count("unchanged") == 2
    assert run("list") == 0
    assert "meeting_recap" in capsys.readouterr().out
    assert run("list", "--json") == 0
    assert '"name": "shout"' in capsys.readouterr().out

    # pulled at a version, changed here, pushed: a new version, and the file says which
    assert run("pull", "--dir", str(theirs)) == 0
    pulled = (theirs / "meeting_recap.md").read_text()
    assert pulled.startswith("---\nname: meeting_recap\n") and "version: 1\n---\n" in pulled
    assert "version: 1\n---\n\nSay this in capitals: {{text}}" in (theirs / "shout.md").read_text()  # a prompt tool
    assert run("pull", "--dir", str(theirs)) == 0
    assert "unchanged meeting_recap.md (v1)" in capsys.readouterr().out
    (theirs / "meeting_recap.md").write_text(pulled.replace("three bullets", "four bullets"))
    assert run("push", str(theirs), "--notes", "four now") == 0
    assert "saved meeting_recap v2 from meeting_recap.md" in capsys.readouterr().out
    assert "version: 2\n" in (theirs / "meeting_recap.md").read_text()

    # the first folder was never pulled: its files say no version, so one that differs from Lens is kept unless --force
    assert run("pull", "--dir", str(mine)) == 1
    assert "meeting_recap: meeting_recap.md has changes that aren't in Lens" in capsys.readouterr().err
    assert run("pull", "--dir", str(mine), "--force") == 0
    assert "pulled shout into shout.md, instead of shout.yaml (v1)" in capsys.readouterr().out
    assert "four bullets" in (mine / "meeting_recap.md").read_text() and not (mine / "shout.yaml").exists()

    # changed in Lens since this folder pulled it: push refuses, pull takes it; a stale edit stays
    (theirs / "meeting_recap.md").write_text((theirs / "meeting_recap.md").read_text().replace("four", "five"))
    assert run("push", str(theirs)) == 0  # v3
    capsys.readouterr()
    (mine / "meeting_recap.md").write_text((mine / "meeting_recap.md").read_text().replace("four", "six"))
    assert run("push", str(mine)) == 1
    assert "changed in Lens since v2 (now v3): pull it first, or push --force" in capsys.readouterr().err
    assert run("pull", "--dir", str(mine)) == 1  # it has changes of its own too
    (mine / "meeting_recap.md").write_text((mine / "meeting_recap.md").read_text().replace("six", "four"))
    assert run("pull", "--dir", str(mine)) == 0  # back as it was pulled: safe to take Lens's
    assert "five bullets" in (mine / "meeting_recap.md").read_text()

    # a manifest that doesn't check, and two files with one name, are named and change nothing
    (mine / "broken.yaml").write_text(TOOL.replace("shout", "broken").replace("{{text}}", "{{words}}"))
    (mine / "again.md").write_text(SKILL)
    assert run("check", str(mine)) == 1
    err = capsys.readouterr().err
    assert "broken.yaml: the tool uses {{words}}, which aren't its parameters" in err
    assert "meeting_recap is in again.md too" in err or "meeting_recap is in meeting_recap.md too" in err


@pytest.fixture
def server(app):
    """The app on a real port, as a Lens server the command line reaches over HTTP."""
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    srv = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    t = threading.Thread(target=srv.run, kwargs={"sockets": [sock]}, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    srv.should_exit = True
    t.join(5)


def test_over_the_api_with_a_token(server, db, tmp_path, capsys):
    ed = make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    vi = make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    tok_ed, tok_vi = (auth.create_token(db, uid, "cli", "write")[1] for uid in (ed, vi))
    (tmp_path / "meeting_recap.md").write_text(SKILL.replace("when:", "visibility: namespace\nnamespaces: [pods]\nwhen:"))

    def run(token, *args):
        return cli_ext.main(["--url", server + "/", "--token", token, *args])

    assert run(tok_ed, "check", str(tmp_path)) == 0
    assert run(tok_ed, "push", str(tmp_path)) == 0
    assert "created meeting_recap (skill, id" in capsys.readouterr().out
    assert versions(db, "meeting_recap") == [1]

    # the viewer sees it, shared with pods, but can't change it; pulling it takes --all or its name
    folder = tmp_path / "vi"
    assert run(tok_vi, "pull", "--dir", str(folder)) == 0
    assert "none of the extensions are yours" in capsys.readouterr().out
    assert run(tok_vi, "pull", "meeting_recap", "--dir", str(folder)) == 0
    (folder / "meeting_recap.md").write_text((folder / "meeting_recap.md").read_text().replace("three", "two"))
    assert run(tok_vi, "push", str(folder)) == 1
    assert "meeting_recap is ed@x.io's: only its owner or an admin changes it" in capsys.readouterr().err

    # sharing written in the file follows it
    (tmp_path / "meeting_recap.md").write_text(SKILL.replace("when:", "visibility: private\nwhen:"))
    assert run(tok_ed, "push", str(tmp_path)) == 0
    assert "unchanged meeting_recap.md (v1); shared meeting_recap: private" in capsys.readouterr().out
    assert run(tok_vi, "list") == 0
    assert "meeting_recap" not in capsys.readouterr().out

    with pytest.raises(SystemExit, match="the token was refused"):
        run("la_nonsense", "list")
