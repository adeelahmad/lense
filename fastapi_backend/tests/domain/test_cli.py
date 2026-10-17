"""The `lens` command line: imports (file and stdin), the batch run, search, speakers, status, users and a worker."""

from __future__ import annotations

import io

from app import cli
from tests.helpers import PODS1


def test_import_file_and_stdin_then_run(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SURREAL_URL", raising=False)
    (tmp_path / "archive.yaml").write_text("data_dir: ./data\nnamespaces:\n  pods:\n    paths: []\n")
    (tmp_path / "ep.txt").write_text(PODS1)

    def run(*args):
        cli.main(["--config", str(tmp_path / "archive.yaml"), *args])

    run("import", "pods", str(tmp_path / "ep.txt"), "--title", "Episode one")
    monkeypatch.setattr("sys.stdin", io.StringIO("Zed: pasted from a terminal about capsid work.\nYan: ok.\nZed: bye."))
    run("import", "pods", "-", "--title", "Piped")
    run("run")
    run("search", "capsid")
    run("speakers", "list", "pods")
    run("status")
    run("history", "verify")
    run("history", "checkpoint")
    run("history", "list")
    monkeypatch.setattr("sys.stdin", io.StringIO("cli password 123\n"))
    run("users", "add", "cli@x.io", "--admin", "--password-stdin")
    run("users", "list")
    run("users", "add", "ed@x.io")  # no password: a sign-in link for adding a passkey
    run("users", "link", "cli@x.io")
    run("worker", "--once")

    text = capsys.readouterr().out
    assert "cli@x.io" in text
    assert "sign-in link (works once, for 3 days): http://localhost:3000/signin-link#" in text
    assert "sign-in link for cli@x.io (works once): http://localhost:3000/signin-link#" in text
    assert "job(s)" in text
    assert "match(es)" in text
    assert "Zed" in text
    assert "surrealkv://" in text
    assert "the history replays to today's graph" in text and "analysis by analysis via analysis" in text
    assert (tmp_path / "data" / "reports" / "pods" / "index.html").exists()


def test_a_worker_logs_the_upgrade_it_runs_as_it_starts(tmp_path, monkeypatch, capsys):
    """The API and the worker start together and either may upgrade the database (docs/database.md, Upgrades), so
    the worker's log says what it ran too."""
    import logging

    from app.domain import logs

    monkeypatch.delenv("SURREAL_URL", raising=False)
    (tmp_path / "archive.yaml").write_text("data_dir: ./data\n")
    root, console, level = logging.getLogger(), logs._state["console"], logging.getLogger().level
    logs.shutdown()  # as in a new process: no settings read, nothing logs to the console yet
    if console is not None:
        root.removeHandler(console)
    logs._state["console"] = None
    root.setLevel(logging.WARNING)
    try:
        cli.main(["--config", str(tmp_path / "archive.yaml"), "worker", "--once"])  # a new database runs every step
    finally:
        if logs._state["console"] is not None:
            root.removeHandler(logs._state["console"])
        logs._state["console"] = console
        if console is not None:
            root.addHandler(console)
        root.setLevel(level)
        logs.shutdown()
    assert "upgrading the database: access-levels" in capsys.readouterr().err


def test_term_stops_a_worker_like_ctrl_c():
    import os
    import signal

    import pytest

    from app import cli

    before = signal.getsignal(signal.SIGTERM)
    try:
        cli._stop_on_term()
        with pytest.raises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGTERM)
            signal.raise_signal(signal.SIGTERM)  # in case delivery of the first is deferred
    finally:
        signal.signal(signal.SIGTERM, before)
