"""Log patterns: a log stream's lines grouped by what's left once the parts that change are taken out (sensors.pattern),
so a chatty router is a few dozen patterns rather than thousands of lines. Each pattern keeps its count, an example,
a label and an action:

- label: routine, notable or alert. Set by a person, or (when the sensor's handling says `triage`) by the decision
  model: one question per pattern, never per line, a few dozen at a time. A label the model wasn't sure of is kept
  with `sure: false`, for a person to settle.
- action: keep (lines are stored as the sensor's handling says) or drop (counted, not stored).
"""

from __future__ import annotations

from . import decide, sensors, store

R = store.R
LABELS = {
    "routine": "normal operation: nothing to do",
    "notable": "worth a look later: a change, a warning, or something unusual that isn't urgent",
    "alert": "needs attention soon: a failure, an error, a security problem or an outage",
}
ACTIONS = ("keep", "drop")
QUESTION = "How much attention does this kind of log line from a home or office network device need?"
TRIAGE_PER_RUN = 50
FIELDS = "record::id(id) AS id, sensor, stream, template, example, count, first_at, last_at, label, label_by, confidence, sure, action"


def list_patterns(db, sid, stream=None, label=None, limit=200):
    q = f"SELECT {FIELDS} FROM sensor_pattern WHERE sensor = $s"
    if stream:
        q += " AND stream = $k"
    if label == "none":
        q += " AND label = NONE"
    elif label:
        q += " AND label = $l"
    rows = db.rows(q + " ORDER BY count DESC LIMIT $n", s=int(sid), k=stream, l=label, n=int(limit))
    names = {x["id"]: x["name"] for x in sensors.streams(db, sid)}
    for r in rows:
        r["stream_name"] = names.get(r.get("stream"))
        r["action"] = r.get("action") or "keep"
    return rows


def get(db, pid):
    row = db.one(f"SELECT {FIELDS} FROM $r", r=R("sensor_pattern", pid))
    if not row:
        raise KeyError(pid)
    return row


def update(db, pid, label=None, action=None, user=None, clear_label=False):
    get(db, pid)
    sets = {}
    if label is not None:
        if label not in LABELS:
            raise ValueError(f"label is one of: {', '.join(LABELS)}")
        sets.update(label=label, label_by=user or "you", sure=True, confidence=None)
    if action is not None:
        if action not in ACTIONS:
            raise ValueError(f"action is one of: {', '.join(ACTIONS)}")
        sets["action"] = action
    if sets:
        db.q("UPDATE $r MERGE $d", r=R("sensor_pattern", pid), d=store.clean(sets))
    if clear_label:
        db.q("UPDATE $r SET label = NONE, label_by = NONE, sure = NONE, confidence = NONE", r=R("sensor_pattern", pid))
    if action is not None:
        sensors._bump(db)  # the intake drops (or keeps) its lines from now on


def triage(db, cfg, ids=None, limit=TRIAGE_PER_RUN, say=None):
    """Label the busiest unlabelled patterns of sensors whose handling says triage: how many were labelled."""
    say = say or (lambda *a: None)
    rows = db.rows(f"SELECT {sensors.FIELDS} FROM storage_source WHERE type IN $t", t=list(sensors.STREAM_TYPES))
    picked = {s["id"]: s for s in rows if (ids is None or s["id"] in ids) and sensors.handling(cfg, s).get("triage")}
    if not picked:
        return 0
    if decide.engine(cfg) is None:
        say("triage is on, but there's no decision model or language model to ask")
        return 0
    todo = db.rows(
        f"SELECT {FIELDS} FROM sensor_pattern WHERE sensor IN $s AND label = NONE ORDER BY count DESC LIMIT $n",
        s=sorted(picked),
        n=int(limit),
    )
    done = 0
    for p in todo:
        s = picked[p["sensor"]]
        state = {
            "device": s.get("name"),
            "kind": sensors.STREAM_TYPES[s["type"]]["label"],
            "program": next((x["name"] for x in sensors.streams(db, s["id"]) if x["id"] == p["stream"]), None),
            "pattern": p["template"],
            "example": p.get("example"),
            "times_seen": p.get("count"),
        }
        try:
            got = decide.choose(cfg, QUESTION, LABELS, state)
        except decide.Undecided as e:
            say(f"triage stopped: {e}")
            break
        db.q(
            "UPDATE $r MERGE $d",
            r=R("sensor_pattern", p["id"]),
            d={"label": got["choice"], "label_by": got["by"], "confidence": got["confidence"], "sure": decide.sure(cfg, got)},
        )
        done += 1
    if done:
        say(f"labelled {done} log patterns")
    return done
