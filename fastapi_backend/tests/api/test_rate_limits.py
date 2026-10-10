"""A connection that is rate limited (Google Drive's throttling and daily upload limit) still works: it reads on, keeps
new files on this machine, and files a scan couldn't fetch wait for the next one instead of failing for good."""

from __future__ import annotations

import pathlib

import pytest

from app.domain import blobs, sources, store

FAKE = pathlib.Path(__file__).parent.parent / "fake_rclone.py"


@pytest.fixture
def drive(db, cfg, folder, monkeypatch):
    state = folder / "throttled"
    state.write_text("")
    monkeypatch.setenv("RCLONE_BINARY", str(FAKE))
    monkeypatch.setenv("FAKE_RCLONE_ROOT", str(folder / "drive"))
    monkeypatch.setenv("FAKE_RCLONE_STATE", str(state))
    sid = sources.create(db, cfg, "Drive", "drive", secret_values={"token": '{"access_token":"a"}'})
    return sid, state


def test_a_throttled_connection_is_still_healthy(db, cfg, drive):
    sid, state = drive
    assert sources.test(db, cfg, sid)["ok"] and "limited" not in sources.test(db, cfg, sid)
    state.write_text("all")
    h = sources.test(db, cfg, sid)
    assert h["ok"] and "rate limiting" in h["limited"] and "error" not in h
    with pytest.raises(sources.RateLimited):
        sources.browse(db, cfg, sid, "")


def test_files_stay_here_while_uploads_are_refused(db, cfg, folder, drive):
    sid, state = drive
    pods = store.ns_id(db, "pods")
    where = {"store": "connection", "connection": sid, "folder": "lens", "crypt": False}
    (folder / "a.txt").write_bytes(b"kept on Drive")
    on_drive = blobs.put(db, cfg, pods, "notes/1/a.txt", folder / "a.txt", where)
    assert on_drive == where

    state.write_text("uploads")  # Google's daily upload limit: reading goes on
    (folder / "b.txt").write_bytes(b"kept here for now")
    assert blobs.put(db, cfg, pods, "notes/1/b.txt", folder / "b.txt", where) == {"store": "local"}
    with blobs.open_file(db, cfg, {"store": "local"}, "notes/1/b.txt") as f:
        assert f.read() == b"kept here for now"
    with blobs.open_file(db, cfg, on_drive, "notes/1/a.txt") as f:
        assert f.read() == b"kept on Drive"
    checked = blobs.test(db, cfg, where)
    assert checked["ok"] and "refusing new files" in checked["limited"]


def test_a_throttled_scan_waits_instead_of_failing(db, cfg, folder, drive):
    sid, state = drive
    calls = folder / "drive" / "calls"
    calls.mkdir(parents=True)
    for n in (1, 2):
        (calls / f"call{n}.txt").write_text(f"Ann: call {n}.\nBen: great.\nAnn: bye.")
    wid = sources.create_watch(db, cfg, sid, "calls", store.ns_id(db, "pods"), stable_seconds=0, backfill=True, kinds="transcripts")

    state.write_text("downloads")  # listing works, but every download is throttled from here
    stats = sources.poll_watch(db, cfg, wid, log=lambda *a: None)
    assert (stats["waiting"], stats["errors"]) == (2, 0)
    assert pathlib.Path(f"{state}.log").read_text().split() == ["copyto"]  # the rest don't hammer a throttled account
    rows = db.rows("SELECT path, status, error FROM remote_file WHERE watch = $w", w=wid)
    assert {r["status"] for r in rows} == {"waiting"} and all("rate limited" in r["error"] for r in rows)

    state.write_text("")  # the limit lifted
    stats = sources.poll_watch(db, cfg, wid, log=lambda *a: None)
    assert (stats["new"], stats["errors"]) == (2, 0)
