"""Chat exports: a WhatsApp, Telegram, Slack or iMessage chat read as a transcript, one segment per message.

Who sent a message is its segment's speaker, so the people in a chat become speakers like the people in a recording,
and the assistant can say who said what. When a message was sent is kept on its segment (`at`); its times are a reading
pace, as for documents, so a chat that went on for a year doesn't read as a year-long recording. The resource is dated
by its first message and holds a chat (recording.form), which is how the Chat export content type knows it.

What each app exports:
- WhatsApp: "Export chat" gives a .txt, a line per message: `31/12/2023, 21:41 - Alice: Hi` (Android) or
  `[31/12/2023, 21:41:05] Alice: Hi` (iPhone); day and month come in the phone's order, which is worked out from the
  dates themselves.
- Telegram Desktop: "Export chat history" as JSON gives result.json, {name, messages: [{from, date, text}]}; a whole
  account's export holds every chat under chats.list.
- Slack: a workspace export holds a JSON file per channel per day, a list of {user_profile, user, ts, text}.
- iMessage: imessage-exporter's text export, a block per message: when, who, then what was said.
"""

from __future__ import annotations

import datetime as dt
import json
import re

WORD_MS = 385  # the reading pace segments are given (as for documents and transcripts without times)
MIN_MESSAGES = 2  # fewer than this and it isn't taken for a chat
MAX_SENDER = 80

_SPACES = str.maketrans({" ": " ", " ": " ", " ": " "})
_MARKS = re.compile("[‎‏‪-‮⁦-⁩]")
_WA_DATE = r"(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4}),?\s+(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?\s*([AaPp]\.?\s?[Mm]\.?)?"
WA_ANDROID = re.compile(rf"^{_WA_DATE}\s+[-–]\s+(.*)$")
WA_IPHONE = re.compile(rf"^\[{_WA_DATE}\]\s+(.*)$")
IM_WHEN = re.compile(r"^([A-Z][a-z]{2} \d{1,2}, \d{4})\s+(\d{1,2}:\d{2}:\d{2}\s*[AP]M)(?:\s*\(.*\))?\s*$")


def read(raw, name=None):
    """The chat in `raw` (a file's text): {segments, speakers, title, timed, form, app, recorded_at}, or None when it
    isn't one."""
    raw = (raw or "").lstrip("﻿")
    found = None
    if raw.lstrip()[:1] in "{[":  # JSON, or an iPhone's WhatsApp lines, which start [date]
        try:
            j = json.loads(raw)
        except ValueError:
            pass
        else:
            found = _telegram(j) or _slack(j)
            if not found:
                return None
    found = found or _whatsapp(raw) or _imessage(raw)
    if not found:
        return None
    app, title, msgs = found
    msgs = [m for m in msgs if m["text"]]
    if len(msgs) < MIN_MESSAGES:
        return None
    segs, t = [], 0
    for m in msgs:
        dur = WORD_MS * max(1, len(m["text"].split()))
        segs.append({"t0": t, "t1": t + dur, "text": m["text"], "speaker": m["sender"], "at": m.get("at")})
        t += dur
    when = next((m["at"] for m in msgs if m.get("at")), None)
    return {"segments": segs, "speakers": {}, "title": title, "timed": False, "form": "chat", "app": app, "recorded_at": when}


def _iso(d):
    return d.isoformat(timespec="seconds") if d else None


def _sender(s):
    s = _MARKS.sub("", str(s or "")).strip()
    return s[:MAX_SENDER] or None


# ---------- WhatsApp ----------
def _wa_lines(raw):
    """(date parts, the rest) for each line that starts a message, and continuation lines joined to it."""
    lines = _MARKS.sub("", raw.translate(_SPACES)).splitlines()
    for rx in (WA_IPHONE, WA_ANDROID):
        first = next((ln for ln in lines if ln.strip()), "")
        if not rx.match(first):
            continue
        out = []
        for ln in lines:
            m = rx.match(ln)
            if m:
                out.append([m.groups()[:7], m.group(8)])
            elif out:
                out[-1][1] += "\n" + ln
        return out
    return []


def _wa_order(parts):
    """Which of a date's first two numbers is the day: whichever is ever above 12 isn't the month. Phones that show
    AM/PM (the US) put the month first when the dates don't say."""
    firsts = [int(p[0]) for p in parts if len(p[0]) <= 2]
    seconds = [int(p[1]) for p in parts]
    if any(len(p[0]) == 4 for p in parts):
        return "ymd"
    if any(x > 12 for x in firsts):
        return "dmy"
    if any(x > 12 for x in seconds):
        return "mdy"
    return "mdy" if any(p[6] for p in parts) else "dmy"


def _wa_when(p, order):
    a, b, c, h, mi, s, ampm = p
    a, b, c = int(a), int(b), int(c)
    y, mo, d = (a, b, c) if order == "ymd" else (c, b, a) if order == "dmy" else (c, a, b)
    if y < 100:
        y += 2000
    h = int(h)
    if ampm:
        pm = ampm.lower().startswith("p")
        h = h % 12 + (12 if pm else 0)
    try:
        return dt.datetime(y, mo, d, h, int(mi), int(s or 0))
    except ValueError:
        return None


def _whatsapp(raw):
    lines = _wa_lines(raw)
    if not lines:
        return None
    order = _wa_order([p for p, _ in lines])
    msgs = []
    for p, rest in lines:
        sender, sep, text = rest.partition(": ")
        if not sep or "\n" in sender:  # "Messages and calls are end-to-end encrypted.", someone joined: not a message
            continue
        msgs.append({"sender": _sender(sender), "text": text.strip(), "at": _iso(_wa_when(p, order))})
    return ("whatsapp", None, msgs) if msgs else None


# ---------- iMessage (imessage-exporter) ----------
def _imessage(raw):
    lines = raw.translate(_SPACES).splitlines()
    starts = [i for i, ln in enumerate(lines) if IM_WHEN.match(ln) and i + 1 < len(lines) and lines[i + 1].strip()]
    if not starts or lines[starts[0]] != next((ln for ln in lines if ln.strip()), None):
        return None
    msgs = []
    for k, i in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        m = IM_WHEN.match(lines[i])
        try:
            when = dt.datetime.strptime(f"{m.group(1)} {re.sub(r'\s+', ' ', m.group(2))}", "%b %d, %Y %I:%M:%S %p")
        except ValueError:
            when = None
        msgs.append({"sender": _sender(lines[i + 1]), "text": "\n".join(lines[i + 2 : end]).strip(), "at": _iso(when)})
    return ("imessage", None, msgs)


# ---------- Telegram ----------
def _tg_text(t):
    if isinstance(t, list):
        return "".join(x if isinstance(x, str) else str((x or {}).get("text", "")) for x in t)
    return str(t or "")


def _tg_messages(chat):
    out = []
    for m in chat.get("messages") or []:
        if not isinstance(m, dict) or m.get("type", "message") != "message" or "from" not in m:
            continue
        try:
            when = dt.datetime.fromisoformat(str(m.get("date")))
        except ValueError:
            when = None
        out.append({"sender": _sender(m.get("from")), "text": _tg_text(m.get("text")).strip(), "at": _iso(when)})
    return out


def _telegram(j):
    if not isinstance(j, dict):
        return None
    if isinstance(j.get("messages"), list):
        return ("telegram", j.get("name") or None, _tg_messages(j))
    chats = (j.get("chats") or {}).get("list") if isinstance(j.get("chats"), dict) else None
    if isinstance(chats, list):
        msgs = [m for c in chats if isinstance(c, dict) for m in _tg_messages(c)]
        return ("telegram", "Telegram chats", sorted(msgs, key=lambda m: m["at"] or ""))
    return None


# ---------- Slack ----------
def _slack(j):
    if not isinstance(j, list) or not j or not all(isinstance(m, dict) and "ts" in m for m in j):
        return None
    msgs = []
    for m in j:
        if m.get("type", "message") != "message" or m.get("subtype") in ("channel_join", "channel_leave"):
            continue
        prof = m.get("user_profile") or {}
        who = prof.get("real_name") or prof.get("display_name") or m.get("user_name") or m.get("username") or m.get("user")
        try:
            when = dt.datetime.fromtimestamp(float(m["ts"]), dt.timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError, OverflowError):
            when = None
        msgs.append({"sender": _sender(who), "text": str(m.get("text") or "").strip(), "at": _iso(when)})
    return ("slack", None, msgs)
