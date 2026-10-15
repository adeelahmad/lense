"""The machine's upgrade agent (packaging/host/lens-upgrade.sh), run for real: it takes the request from the inbox,
gets the release, runs install.sh and reports each step; when the new version doesn't start it goes back to the code
that ran before, starts it, and leaves the restore request for it (docs/updates.md).

install.sh is a stand-in that gets the code as the real one does and prints its lines, failing for a broken release;
the repository is a local one with three releases."""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]

FAKE_INSTALL = """#!/bin/sh
set -eu
cd "$LENS_DIR"
if [ "${LENS_KEEP_CODE:-0}" != 1 ]; then
  echo "Updating Lens in $LENS_DIR..."
  git fetch -q --depth 1 origin "$LENS_REF" && git checkout -q -f -B "$LENS_REF" FETCH_HEAD
fi
echo "Building and starting Lens (the first build takes a few minutes)..."
echo "Waiting for the web app..."
echo "$(cat VERSION) $LENS_KEEP_CODE_SEEN" >>"$LENS_DIR/../started"
if grep -q broken VERSION; then echo "Lens didn't answer within 10 minutes." && exit 1; fi
echo "Lens is running: http://localhost:3000/setup?code=x"
""".replace("$LENS_KEEP_CODE_SEEN", "${LENS_KEEP_CODE:-0}")

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="needs git")


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def lens(tmp_path):
    """An install on v0.6.0 whose origin also has v0.7.0 (works) and v0.8.0 (doesn't start)."""
    origin = tmp_path / "origin"
    (origin / "packaging" / "host").mkdir(parents=True)
    git(origin, "init", "-q")
    shutil.copy(ROOT / "packaging" / "host" / "lens-upgrade.sh", origin / "packaging" / "host")
    (origin / "install.sh").write_text(FAKE_INSTALL)
    (origin / "fastapi_backend").mkdir()
    (origin / ".gitignore").write_text(".lens-control/\n.env\n")
    for v in ("0.6.0", "0.7.0", "0.8.0-broken"):
        (origin / "VERSION").write_text(v)
        (origin / "fastapi_backend" / "pyproject.toml").write_text(f'[project]\nversion = "{v.split("-")[0]}"\n')
        git(origin, "add", "-A")
        git(origin, "commit", "-q", "-m", v)
        git(origin, "tag", "v" + v.split("-")[0])
    lens = tmp_path / "lens"
    git(tmp_path, "clone", "-q", "--branch", "v0.6.0", str(origin), str(lens))
    git(lens, "checkout", "-q", "-B", "v0.6.0")  # as install.sh leaves a release
    (lens / ".lens-control" / "inbox").mkdir(parents=True)
    return lens


def upgrade(lens, ref, backup="before-upgrade-20261010-120000.surql.gz"):
    ctl = lens / ".lens-control"
    (ctl / "inbox" / "upgrade").write_text(f"id=r1\nref={ref}\nbackup={backup}\nby=t\n")
    subprocess.run(["sh", str(lens / "packaging" / "host" / "lens-upgrade.sh"), str(lens)], check=True, timeout=60)
    status = dict(line.split("=", 1) for line in (ctl / "upgrade-status").read_text().splitlines())
    return status, (ctl / "upgrade.log").read_text()


def test_a_release_is_installed(lens):
    status, log = upgrade(lens, "v0.7.0")
    assert status["state"] == "done" and status["version"] == "0.7.0"
    assert (lens / "VERSION").read_text() == "0.7.0"
    assert "Lens is running" in log
    assert not (lens / ".lens-control" / "inbox" / "restore").exists()
    assert not (lens / ".lens-control" / "inbox" / "upgrade").exists()


def test_a_release_that_doesnt_start_is_rolled_back(lens):
    status, log = upgrade(lens, "v0.8.0")
    assert status["state"] == "rolled-back", log
    assert status["version"] == "0.6.0" and "didn't start" in status["error"]
    assert status["backup"] == "before-upgrade-20261010-120000.surql.gz"
    assert (lens / "VERSION").read_text() == "0.6.0"
    # the broken one was started, then the previous one again from the code already there
    assert (lens.parent / "started").read_text().splitlines() == ["0.8.0-broken 0", "0.6.0 1"]
    restore = (lens / ".lens-control" / "inbox" / "restore").read_text()
    assert restore == "id=r1\nbackup=before-upgrade-20261010-120000.surql.gz\n"
    # and a later upgrade from there works
    assert upgrade(lens, "v0.7.0")[0]["state"] == "done"


def test_only_releases_and_this_installs_branch(lens):
    status, log = upgrade(lens, "evil-branch")
    assert status["state"] == "failed" and "only a release tag" in status["error"]
    assert (lens / "VERSION").read_text() == "0.6.0"
