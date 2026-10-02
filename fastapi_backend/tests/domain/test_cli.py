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
    monkeypatch.setattr("sys.stdin", io.StringIO("cli password 123\n"))
    run("users", "add", "cli@x.io", "--admin", "--password-stdin")
    run("users", "list")
    run("worker", "--once")

    text = capsys.readouterr().out
    assert "cli@x.io" in text
    assert "job(s)" in text
    assert "match(es)" in text
    assert "Zed" in text
    assert "surrealkv://" in text
    assert (tmp_path / "data" / "reports" / "pods" / "index.html").exists()


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
