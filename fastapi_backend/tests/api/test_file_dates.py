"""An asset dated by when it was taken, as its own file says (docs/processing.md, Steps): a photo's EXIF date and GPS
fix, a video's creation time; a date its name gives wins, and nothing is guessed."""

from __future__ import annotations

import datetime as dt
import io
import subprocess

import pytest
from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational

from app.core import security
from app.domain import ingest, store
from tests.api.test_documents import _upload
from tests.api.test_mcp import tool
from tests.api.test_oauth import _bearer, _grant
from tests.helpers import login, make_user, quiet
from tests.video_helpers import needs_ffmpeg

R = store.R
MODIFIED = 1_700_000_000_000  # the file's last change, as the browser sends it (2023-11-14)


@pytest.fixture
def app(cfg, db):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def env(client, db):
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    return {"he": login(client, "ed@x.io", "editor password 1")}


def _photo(taken="2019:08:17 09:41:05", changed="2020:01:01 00:00:00", gps=True):
    """A small JPEG with the EXIF a phone writes: when it was taken and changed, and where (the Eiffel Tower)."""
    exif = Image.Exif()
    if changed:
        exif[ExifTags.Base.DateTime] = changed
    if taken:
        exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = taken
    if gps:
        g = exif.get_ifd(ExifTags.IFD.GPSInfo)
        g[ExifTags.GPS.GPSLatitudeRef], g[ExifTags.GPS.GPSLatitude] = "N", (IFDRational(48), IFDRational(51), IFDRational(2964, 100))
        g[ExifTags.GPS.GPSLongitudeRef], g[ExifTags.GPS.GPSLongitude] = "E", (IFDRational(2), IFDRational(17), IFDRational(4044, 100))
    out = io.BytesIO()
    Image.new("RGB", (32, 24), "teal").save(out, format="JPEG", exif=exif)
    return out.getvalue()


def _video(path, *metadata):
    """A second of colour and tone, with the metadata given (key=value)."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1"]
    cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    for m in metadata:
        cmd += ["-metadata", m]
    subprocess.run([*cmd, *(["-movflags", "use_metadata_tags"] if path.suffix == ".mov" else []), str(path)], check=True)
    return path


def _row(db, rid):
    return db.one("SELECT recorded_at, place FROM $r", r=R("recording", rid))


def test_a_photo_is_dated_and_placed_by_its_exif(client, new_client, env, db):
    up = _upload(client, env["he"], _photo(), "IMG_4711.jpg", modified=MODIFIED)
    rid = up["recording"]
    # when it was taken (DateTimeOriginal, not DateTime nor the file's last change), and where
    assert _row(db, rid) == {"recorded_at": "2019-08-17T09:41:05", "place": {"lat": 48.858233, "lon": 2.294567}}
    rec = client.get(f"/api/v1/recordings/{rid}", headers=env["he"]).json()
    assert (rec["recorded_at"], rec["place"]) == ("2019-08-17T09:41:05", {"lat": 48.858233, "lon": 2.294567})
    _, tokens = _grant(client, env["he"], scope="read")  # an agent, through MCP
    got = tool(client, _bearer(tokens), "get_recording", recording_id=rid)
    assert (got["recorded_at"], got["place"]) == ("2019-08-17T09:41:05", {"lat": 48.858233, "lon": 2.294567})
    # where it was taken stays with its members: a link signed for a visitor doesn't say it
    seen = new_client().get(security.sign_path(f"/api/v1/recordings/{rid}"))
    assert seen.status_code == 200, seen.text
    assert seen.json().get("place") is None and seen.json()["recorded_at"] == "2019-08-17T09:41:05"
    # a photo with only DateTime is dated by that; one with no GPS has no place
    rid = _upload(client, env["he"], _photo(taken=None, gps=False), "IMG_4712.jpg", modified=MODIFIED)["recording"]
    assert _row(db, rid) == {"recorded_at": "2020-01-01T00:00:00", "place": None}


def test_a_date_already_set_is_kept(client, env, db):
    # a date in its name is someone's: it wins over the EXIF (the place is still kept)
    rid = _upload(client, env["he"], _photo(), "2024-03-05 beach.jpg", modified=MODIFIED)["recording"]
    assert _row(db, rid) == {"recorded_at": "2024-03-05T00:00:00", "place": {"lat": 48.858233, "lon": 2.294567}}
    # the same photo uploaded again is the same asset, its date untouched
    db.q("UPDATE $r SET recorded_at = '2001-02-03T04:05:06'", r=R("recording", rid))
    again = _upload(client, env["he"], _photo(), "2024-03-05 beach.jpg", modified=MODIFIED)
    assert (again["recording"], again["duplicate"]) == (rid, True)
    assert _row(db, rid)["recorded_at"] == "2001-02-03T04:05:06"
    # nothing is invented: a camera whose clock was never set says 1970, so the file's last change dates it
    unset = _photo(taken="1970:01:01 00:00:00", changed=None, gps=False)
    rid = _upload(client, env["he"], unset, "IMG_0001.jpg", modified=MODIFIED)["recording"]
    assert _row(db, rid)["recorded_at"] == dt.datetime.fromtimestamp(MODIFIED / 1000).isoformat(timespec="seconds")


@needs_ffmpeg
def test_a_video_is_dated_by_its_creation_time(client, env, db, folder):
    clip = _video(folder / "clip.mp4", "creation_time=2021-07-04T15:30:00.000000Z")
    rid = _upload(client, env["he"], clip.read_bytes(), "clip.mp4", modified=MODIFIED)["recording"]
    assert _row(db, rid) == {"recorded_at": "2021-07-04T15:30:00+00:00", "place": None}
    assert client.get(f"/api/v1/recordings/{rid}", headers=env["he"]).json()["recorded_at"] == "2021-07-04T15:30:00+00:00"
    # a phone's QuickTime creationdate keeps its local time, so it comes first
    mov = _video(folder / "phone.mov", "creation_time=2021-07-04T15:30:00Z", "com.apple.quicktime.creationdate=2021-07-04T17:30:00+0200")
    assert ingest.taken(mov) == ("2021-07-04T17:30:00+02:00", None)
    # one that says nothing is dated as before; a QuickTime zero date (1904) says nothing
    assert ingest.taken(_video(folder / "plain.mp4")) == (None, None)
    assert ingest.taken(_video(folder / "zero.mp4", "creation_time=1904-01-01T00:00:00Z")) == (None, None)


@needs_ffmpeg
def test_a_scanned_video_is_dated_by_its_creation_time(db, cfg, folder):
    shelf = folder / "shelf"
    shelf.mkdir()
    _video(shelf / "holiday.mp4", "creation_time=2022-12-24T18:00:00Z")
    cfg["namespaces"]["pods"]["paths"] = [str(shelf)]
    assert ingest.scan(db, cfg, only="pods", log=quiet)["new"] == 1
    rec = db.one("SELECT recorded_at, place FROM recording WHERE path = $p", p=str(shelf / "holiday.mp4"))
    assert rec == {"recorded_at": "2022-12-24T18:00:00+00:00", "place": None}
