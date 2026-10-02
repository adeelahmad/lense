"""Flags on comments from a decision model (app/domain/decide.py, docs/api.md#comments).

A new or edited comment is judged for spam, abuse and personal details. One the model is sure about
(`decisions.flag_above`) is flagged for the owners of its resource to review: they keep it (the flag is cleared and
that text isn't flagged again) or delete it. A flag never hides or removes anything by itself, and only owners see it.
"""

from __future__ import annotations

from . import decide, store

R = store.R
TIMEOUT = 5  # seconds; a comment that can't be judged in time just isn't flagged
REASONS = {
    "spam": (
        "Is this comment spam: advertising, a scam, or links and promotion that have nothing to do with the resource?",
        "it promotes or sells something unrelated, or is a scam",
        "it is about the resource or the conversation, even if short or critical",
    ),
    "abuse": (
        "Does this comment attack, threaten, harass or demean a person or a group?",
        "it insults, threatens or demeans someone",
        "it disagrees or criticises without attacking anyone",
    ),
    "personal": (
        "Does this comment reveal private personal details of someone: a home address, a phone number, an identity or "
        "account number, a password, or health details?",
        "it contains such details",
        "it contains none; names, and work contact details people publish themselves, don't count",
    ),
}
LABELS = {"spam": "Spam", "abuse": "Abuse", "personal": "Personal details"}


def judge(cfg, text):
    """The reasons to flag this text, most likely first: [{reason, p}]. decide.DecideError when the model can't be asked."""
    got = decide.ask(cfg, {"comment": text}, {k: decide.noul(*v) for k, v in REASONS.items()}, timeout=TIMEOUT)
    at = decide.threshold(cfg, "flag_above")
    return sorted(({"reason": k, "p": round(a["p"], 3)} for k, a in got.items() if a["p"] >= at), key=lambda x: -x["p"])


def check_comment(db, cfg, cid):
    """Judge a comment as it stands and set or clear its flag; the flag, or None. Does nothing where moderation is
    off, the comment is gone, the model can't be asked, or an owner already kept this very text."""
    if not decide.uses(cfg, "moderate"):
        return None
    c = db.one("SELECT text, flag FROM $r", r=R("comment", int(cid)))
    if not c:
        return None
    if (c.get("flag") or {}).get("kept") == c["text"]:
        return None
    try:
        reasons = judge(cfg, c["text"])
    except decide.DecideError:
        return None
    flag = {"reasons": reasons, "at": store.now()} if reasons else None
    # only if the text is still the one judged (it may have been edited meanwhile),
    # and nobody kept it meanwhile
    db.q("UPDATE $r SET flag = $f WHERE text = $x AND flag.kept != $x", r=R("comment", int(cid)), f=flag, x=c["text"])
    return flag


def keep(db, cid):
    """An owner looked and the comment stays: the flag goes, and this text isn't flagged again. Returns the reasons it
    was flagged for. KeyError for a comment without a flag."""
    c = db.one("SELECT text, flag FROM $r", r=R("comment", int(cid)))
    if not c or not (c.get("flag") or {}).get("reasons"):
        raise KeyError(cid)
    db.q("UPDATE $r SET flag = $f", r=R("comment", int(cid)), f={"kept": c["text"]})
    return c["flag"]["reasons"]


def flagged(db, spaces, limit=200):
    """The flagged comments of these namespaces, the newest flag first."""
    if not spaces:
        return []
    rows = db.rows(
        "SELECT record::id(id) AS id, recording, space, account, text, flag, created_at FROM comment "
        "WHERE space IN $s AND flag.reasons != NONE",
        s=list(spaces),
    )
    return sorted(rows, key=lambda c: c["flag"].get("at") or "", reverse=True)[:limit]
