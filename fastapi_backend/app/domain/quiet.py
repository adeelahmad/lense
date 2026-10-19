"""Quiet processing: hours for the heavy steps, and how many of them run at once (Settings › Workers).

On a small machine at home, transcribing and reading video keep it busy and its fan loud. Two settings, both off by
default, keep that to when it suits:
- `workers.heavy_hours`: a window like "01:00-06:00" (in `workers.timezone`, UTC by default; it may pass midnight).
  Outside it, no job starts a heavy step; one that reaches one goes back on the queue and waits for the window.
- `workers.heavy_at_once`: at most this many jobs in a heavy step at a time, across every worker (a step a worker is
  already running finishes; two workers claiming in the same instant can briefly make it one more).

The heavy steps are `workers.heavy_steps`: by default the ones that work through media on this machine (speech,
voices, shots, text on screen, faces, objects, NSFW, reading documents, blurring faces). Everything else runs as before.
"""

from __future__ import annotations

import datetime as dt
import re

from . import schedule, store

HEAVY = ["transcribe", "read", "diarize", "shots", "ocr", "faces", "objects", "nsfw", "blur_faces"]
WINDOW = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")


def window(text):
    """'01:00-06:00' as (start, end) minutes of the day; ValueError when it isn't one."""
    m = WINDOW.match(str(text or "").strip())
    if not m:
        raise ValueError("workers.heavy_hours is a window like 01:00-06:00 (24-hour times), or empty for any time")
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    if a == b:
        raise ValueError("workers.heavy_hours starts and ends at different times")
    return a, b


def in_window(text, tz="UTC", now=None):
    """Whether `now` (default: this moment) is inside the window, read in time zone `tz`."""
    a, b = window(text)
    t = (now or dt.datetime.now(dt.timezone.utc)).astimezone(schedule.zone(tz))
    m = t.hour * 60 + t.minute
    return a <= m < b if a < b else (m >= a or m < b)


def held(db, cfg, now=None, job=None):
    """The heavy steps no job may start now: all of them outside the hours, or with as many running as allowed (not
    counting `job`, the one asking); an empty set when neither setting is on."""
    w = cfg.get("workers") or {}
    heavy = set(w.get("heavy_steps") or HEAVY)
    if w.get("heavy_hours") and not in_window(w["heavy_hours"], w.get("timezone") or "UTC", now):
        return heavy
    cap = w.get("heavy_at_once")
    if cap:
        n = db.one(
            "SELECT count() AS n FROM job WHERE status = 'running' AND next_step IN $h AND id != $j GROUP ALL",
            h=sorted(heavy),
            j=store.R("job", job if job is not None else -1),
        )
        if (n or {}).get("n", 0) >= int(cap):
            return heavy
    return set()


def why(cfg):
    """Why a heavy step is waiting, for the job's log."""
    w = cfg.get("workers") or {}
    if w.get("heavy_hours") and not in_window(w["heavy_hours"], w.get("timezone") or "UTC"):
        return f"heavy steps run {w['heavy_hours']} ({w.get('timezone') or 'UTC'})"
    return f"{w.get('heavy_at_once')} heavy step(s) at a time are running"
