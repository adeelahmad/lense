"""Routing rules for a watched email account: which namespace each new message goes to.

A shared inbox (support@, info@, a family address) often carries mail for several namespaces. A watch on an IMAP
source can carry rules, tried in order; the first whose conditions all match a message decides where it goes: into
another namespace, or nowhere (skipped). A message no rule matches goes to the watch's own namespace, as it does with
no rules at all, and so does one whose rule names a namespace that has since been deleted.

A condition looks at one part of the message (FIELDS) and holds one or more patterns, any of which may match. Patterns
ignore case; one without * or ? matches anywhere in the text (`acme.com` matches `ana@acme.com`), one with them must
match all of it (`*@acme.com`). `to` covers To, Cc and the address the inbox received the message at (Delivered-To,
X-Original-To), which is how mail sent to an alias of a shared inbox is told apart.

An email account (the source) can carry shared rules too, which every watch on it uses: an admin writes the
account's policy once (support@ to support, newsletters skipped) instead of copying it onto each mailbox's watch. A
watch's own rules are tried first, then the account's, then the message goes to the watch's namespace (`decide`).

Rules are kept with namespace ids (a renamed namespace keeps its mail), and shown with names.
"""

from __future__ import annotations

import fnmatch

from . import store

FIELDS = {
    "from": "who sent it",
    "to": "who it was for: To, Cc, or the address the inbox received it at",
    "subject": "its subject",
    "list": "its mailing list (List-Id)",
    "mailbox": "the mailbox it is in",
}
MAX_RULES = 100
MAX_PATTERNS = 50


def _patterns(field, value):
    vals = [value] if isinstance(value, str) else value
    if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals):
        raise ValueError(f"{field} is a pattern or a list of patterns")
    vals = [v.strip() for v in vals if v.strip()]
    if not vals or len(vals) > MAX_PATTERNS:
        raise ValueError(f"{field} needs between 1 and {MAX_PATTERNS} patterns")
    return vals


def check(db, rules):
    """Rules as the API takes them ({match: {field: patterns}, namespace} or {match, skip: true}), checked, with
    namespace ids in place of names. A namespace must exist already: a rule never creates one."""
    if rules is None:
        return []
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        raise ValueError(f"routes is a list of at most {MAX_RULES} rules")
    out = []
    for i, r in enumerate(rules, 1):
        if not isinstance(r, dict):
            raise ValueError(f"rule {i} isn't a rule")
        match = r.get("match") or {}
        if not isinstance(match, dict) or not match:
            raise ValueError(f"rule {i} needs at least one condition: {', '.join(FIELDS)}")
        if set(match) - set(FIELDS):
            raise ValueError(f"rule {i}: conditions are on {', '.join(FIELDS)}")
        rule = {"match": {f: _patterns(f, v) for f, v in match.items()}}
        if r.get("skip"):
            if r.get("namespace"):
                raise ValueError(f"rule {i} either skips messages or sends them to a namespace, not both")
            rule["skip"] = True
        elif r.get("namespace"):
            try:
                rule["space"] = store.ns_id(db, r["namespace"], create=False)
            except KeyError:
                raise ValueError(f"rule {i}: there's no namespace {r['namespace']}") from None
        else:
            raise ValueError(f"rule {i} needs a namespace, or skip")
        if r.get("name"):
            rule["name"] = str(r["name"]).strip()[:100]
        out.append(rule)
    return out


def view(rules, names):
    """Stored rules as the API shows them: namespaces by name (None for one deleted since)."""
    out = []
    for r in rules or []:
        shown = {"match": r["match"], "skip": bool(r.get("skip")), "namespace": None if r.get("skip") else names.get(r.get("space"))}
        out.append({**shown, "name": r["name"]} if r.get("name") else shown)
    return out


def _hit(pattern, text):
    p, t = pattern.lower(), (text or "").lower()
    return fnmatch.fnmatchcase(t, p) if any(c in p for c in "*?[") else p in t


def matches(rule, mail):
    for field, patterns in rule["match"].items():
        got = mail.get(field)
        texts = got if isinstance(got, list) else [got or ""]
        if not any(_hit(p, t) for p in patterns for t in texts):
            return False
    return True


def route(rules, mail, home, spaces):
    """Where a message goes: (namespace id, or None to skip it; the number of the rule that decided, or None).
    `home` is the watch's namespace; `spaces` the namespace ids that exist."""
    if not mail:
        return home, None
    for i, r in enumerate(rules or [], 1):
        if matches(r, mail):
            if r.get("skip"):
                return None, i
            return (r["space"], i) if r.get("space") in spaces else (home, i)
    return home, None


def decide(own, shared, mail, home, spaces):
    """Where a message goes under a watch's own rules, then its account's shared ones: (namespace id, or None to skip
    it; the number of the rule that decided, within its list, or None; whether that rule is a shared one)."""
    space, n = route(own, mail, home, spaces)
    if n is not None or not shared:
        return space, n, False
    space, n = route(shared, mail, home, spaces)
    return space, n, n is not None


# ---------- rules written as lines (the assistant's mailbox: mailbox.py) ----------
def parse_line(text):
    """A rule written as a line, "from: *@acme.com, *@acme.org; subject: invoice = finance" (conditions joined by ;,
    patterns by a comma, the namespace by name after =) or "list: * = skip": {match, namespace} or {match, skip}.
    ValueError saying what's wrong."""
    where, eq, target = (text or "").rpartition("=")
    target = target.strip().lower()
    if not eq or not where.strip() or not target:
        raise ValueError(f"“{text.strip()}” isn't a rule, like from: *@acme.com = acme")
    match = {}
    for cond in where.split(";"):
        field, colon, patterns = cond.partition(":")
        field = field.strip().lower()
        if not colon or field not in FIELDS:
            raise ValueError(f"“{cond.strip()}”: a condition is on {', '.join(FIELDS)}, like from: *@acme.com")
        match.setdefault(field, []).extend(_patterns(field, [p for p in patterns.split(",")]))
    return {"match": match, "skip": True} if target == "skip" else {"match": match, "namespace": target}


def line(rule):
    """A rule as parse_line reads it."""
    conds = "; ".join(f"{f}: {', '.join(ps)}" for f, ps in rule["match"].items())
    return f"{conds} = {'skip' if rule.get('skip') else rule['namespace']}"
