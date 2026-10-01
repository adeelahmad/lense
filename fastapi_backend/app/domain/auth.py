"""People, sessions, API tokens, namespace roles, share links and the audit log.

Admins can do everything. Otherwise access is per namespace: viewer (read, listen, search, chat), editor (import,
correct, rename and merge speakers, run pipelines) and owner (members and namespace settings).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import re
import secrets
import threading
import time

from . import store

R = store.R
ROLES = {"viewer": 1, "editor": 2, "owner": 3}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DUMMY = []
_FAILS, _FL = {}, threading.Lock()
REUSE_GRACE_SECONDS = 60


def _b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def hash_password(pw):
    if len(pw or "") < 10:
        raise ValueError("passwords need at least 10 characters")
    salt, n, r, p = secrets.token_bytes(16), 2**15, 8, 1
    h = hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(h)}"


def verify_password(pw, stored):
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt((pw or "").encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(calc, _unb64(h))
    except (ValueError, TypeError):
        return False


def _later(hours):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)).isoformat(timespec="seconds")


# ---------- accounts ----------
def create_account(db, email, password, name=None, admin=False):
    email = (email or "").strip().lower()
    if not EMAIL.match(email):
        raise ValueError("enter a valid email address")
    if db.values("SELECT VALUE id FROM account WHERE email = $e", e=email):
        raise ValueError("that email already has an account")
    uid = db.next_id("account")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("account", uid),
        d={
            "email": email,
            "name": (name or email.split("@")[0])[:80],
            "pw": hash_password(password),
            "admin": bool(admin),
            "disabled": False,
            "created_at": store.now(),
        },
    )
    return uid


def account_count(db):
    return len(db.values("SELECT VALUE id FROM account LIMIT 1"))


def public(u):
    return {k: u.get(k) for k in ("id", "email", "name", "admin", "disabled", "created_at", "last_login_at")}


def get_account(db, uid):
    return db.one("SELECT record::id(id) AS id, email, name, admin, disabled, created_at, last_login_at, pw FROM $r", r=R("account", uid))


def find_account(db, email):
    return db.one(
        "SELECT record::id(id) AS id, email, name, admin, disabled, pw FROM account WHERE email = $e LIMIT 1",
        e=(email or "").strip().lower(),
    )


def throttled(key):
    with _FL:
        _FAILS[key] = [t for t in _FAILS.get(key, []) if time.time() - t < 900]
        return len(_FAILS[key]) >= 8


def login(db, email, password, key=""):
    """Returns the account or None. Unknown emails cost the same time as wrong passwords."""
    u = find_account(db, email)
    if not _DUMMY:
        _DUMMY.append(hash_password("x" * 16))
    ok = verify_password(password, u["pw"] if u else _DUMMY[0])
    if not u or not ok or u.get("disabled"):
        with _FL:
            _FAILS.setdefault(key, []).append(time.time())
        return None
    db.q("UPDATE $r SET last_login_at = $t", r=R("account", u["id"]), t=store.now())
    return public(u)


def update_account(db, uid, name=None, admin=None, disabled=None, password=None):
    patch = store.clean({"name": name, "admin": admin, "disabled": disabled, "pw": hash_password(password) if password else None})
    if patch:
        db.q("UPDATE $r MERGE $p", r=R("account", uid), p=patch)
    if disabled or password:
        db.q("DELETE login_session WHERE account = $a", a=uid)


def change_password(db, uid, current, new, keep_sid=None, key=""):
    """Someone changes their own password: the current one first. Their other sessions end (this one, keep_sid, stays)
    and so do reset links they asked for. Wrong current passwords count towards the sign-in throttle (key)."""
    u = get_account(db, uid)
    if not u or not verify_password(current, u.get("pw") or ""):
        with _FL:
            _FAILS.setdefault(key, []).append(time.time())
        raise ValueError("Your current password is wrong.")
    if new == current:
        raise ValueError("The new password is the same as the current one.")
    db.q("UPDATE $r SET pw = $p", r=R("account", uid), p=hash_password(new))
    db.run(
        ["DELETE login_session WHERE account = $a AND sid != $keep", "DELETE password_reset WHERE account = $a"],
        a=uid,
        keep=keep_sid or "",
    )


def rename_account(db, uid, name):
    """Someone changes their own name (whitespace collapsed, at most 80 characters)."""
    n = " ".join((name or "").split())
    if not n:
        raise ValueError("Your name can't be empty.")
    if len(n) > 80:
        raise ValueError("A name has at most 80 characters.")
    db.q("UPDATE $r SET name = $n", r=R("account", uid), n=n)
    return n


# ---------- sessions (refresh tokens) and API tokens ----------
# The web app signs in through NextAuth: the API hands out a short-lived JWT access token (see app.core.security) and a
# long-lived refresh token. Only the refresh token's hash is stored, one row per signed-in device; every refresh
# rotates it, and presenting a rotated-out token ends that whole session (it was probably stolen).
def start_session(db, cfg, uid, ua="", ip=""):
    raw, sid = secrets.token_urlsafe(32), secrets.token_urlsafe(12)
    db.q(
        "CREATE $r CONTENT $d",
        r=R("login_session", sha(raw)),
        d={
            "account": uid,
            "sid": sid,
            "created_at": store.now(),
            "expires_at": _later(cfg["server"].get("session_hours", 168)),
            "ua": (ua or "")[:200],
            "ip": ip,
        },
    )
    return raw, sid


def refresh_session(db, cfg, raw):
    """Swap a refresh token for a new one. Returns (account, new token, sid) or None."""
    if not raw:
        return None
    s = db.one("SELECT account, sid, expires_at, rotated, rotated_at FROM $r", r=R("login_session", sha(raw)))
    if not s or (s.get("expires_at") or "") < store.now():
        return None
    if s.get("rotated") and (s.get("rotated_at") or "") < _later(-REUSE_GRACE_SECONDS / 3600):
        # a token swapped a while ago came back: it was probably copied, so end that session everywhere. (Within the
        # grace period it's more likely two tabs refreshing at once, which is allowed.)
        db.q("DELETE login_session WHERE sid = $s", s=s["sid"])
        return None
    u = get_account(db, s["account"])
    if not _active(u):
        return None
    new = secrets.token_urlsafe(32)
    db.run(
        ["UPDATE $old SET rotated = true, rotated_at = rotated_at OR $now", "CREATE $new CONTENT $d"],
        old=R("login_session", sha(raw)),
        now=store.now(),
        new=R("login_session", sha(new)),
        d={"account": u["id"], "sid": s["sid"], "created_at": store.now(), "expires_at": _later(cfg["server"].get("session_hours", 168))},
    )
    return public(u), new, s["sid"]


def end_session(db, raw):
    if raw:
        s = db.one("SELECT sid FROM $r", r=R("login_session", sha(raw)))
        if s:
            db.q("DELETE login_session WHERE sid = $s", s=s["sid"])


def session_active(db, sid):
    return bool(sid) and bool(
        db.values("SELECT VALUE id FROM login_session WHERE sid = $s AND expires_at > $n LIMIT 1", s=sid, n=store.now())
    )


def _active(u):
    return u and not u.get("disabled")


def active_account(db, uid):
    u = get_account(db, uid)
    return public(u) if _active(u) else None


# ---------- password reset ----------
def start_reset(db, email, minutes=60):
    """A one-time reset token for this email, or None when there's no such (active) account. Callers answer the same either way."""
    u = find_account(db, email)
    if not _active(u):
        return None, None
    raw = secrets.token_urlsafe(32)
    db.q("DELETE password_reset WHERE account = $a", a=u["id"])
    db.q(
        "CREATE $r CONTENT $d",
        r=R("password_reset", sha(raw)),
        d={"account": u["id"], "created_at": store.now(), "expires_at": _later(minutes / 60)},
    )
    return raw, public(u)


def finish_reset(db, raw, password):
    row = db.one("SELECT account, expires_at FROM $r", r=R("password_reset", sha(raw or ""))) if raw else None
    if not row or row["expires_at"] < store.now():
        raise ValueError("this reset link is invalid or has expired")
    update_account(db, row["account"], password=password)
    db.q("DELETE password_reset WHERE account = $a", a=row["account"])
    return row["account"]


def create_token(db, uid, name, scope="read", days=90):
    if scope not in ("read", "write"):
        raise ValueError("scope is read or write")
    raw = "la_" + secrets.token_urlsafe(32)
    tid = db.next_id("api_token")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("api_token", tid),
        d=store.clean(
            {
                "account": uid,
                "name": (name or "token")[:80],
                "scope": scope,
                "hash": sha(raw),
                "prefix": raw[:9],
                "created_at": store.now(),
                "expires_at": _later(24 * days) if days else None,
            }
        ),
    )
    return tid, raw


def token_account(db, raw):
    if not raw or not raw.startswith("la_"):
        return None
    t = db.one("SELECT record::id(id) AS id, account, scope, expires_at FROM api_token WHERE hash = $h LIMIT 1", h=sha(raw))
    if not t or (t.get("expires_at") and t["expires_at"] < store.now()):
        return None
    u = get_account(db, t["account"])
    if not _active(u):
        return None
    db.q("UPDATE $r SET last_used_at = $n", r=R("api_token", t["id"]), n=store.now())
    return {**public(u), "via": "token", "scope": t["scope"]}


def list_tokens(db, uid):
    return db.rows(
        "SELECT record::id(id) AS id, name, scope, prefix, created_at, expires_at, last_used_at FROM api_token WHERE account = $a", a=uid
    )


def revoke_token(db, uid, tid):
    db.q("DELETE api_token WHERE account = $a AND id = $r", a=uid, r=R("api_token", tid))


# ---------- roles ----------
def roles(db, user):
    """{space id: role} for this person; admins own everything."""
    if not user:
        return {}
    if user.get("admin"):
        return {sid: "owner" for sid in db.values("SELECT VALUE record::id(id) FROM space")}
    return {r["space"]: r["role"] for r in db.rows("SELECT space, role FROM membership WHERE account = $a", a=user["id"])}


def allows(role_map, sid, need="viewer"):
    return ROLES.get(role_map.get(sid), 0) >= ROLES[need]


def set_role(db, uid, sid, role):
    rid = R("membership", f"{uid}-{sid}")
    if role is None:
        db.q("DELETE $r", r=rid)
    elif role in ROLES:
        db.q("UPSERT $r CONTENT $d", r=rid, d={"account": uid, "space": sid, "role": role})
    else:
        raise ValueError("role is viewer, editor or owner")


def members(db, sid):
    rows = db.rows("SELECT account, role FROM membership WHERE space = $s", s=sid)
    people = (
        {
            u["id"]: u
            for u in db.rows(
                "SELECT record::id(id) AS id, email, name FROM account WHERE id IN $ids", ids=[R("account", r["account"]) for r in rows]
            )
        }
        if rows
        else {}
    )
    return [
        {
            "account": r["account"],
            "role": r["role"],
            "email": people.get(r["account"], {}).get("email"),
            "name": people.get(r["account"], {}).get("name"),
        }
        for r in rows
    ]


# ---------- share links (read-only access to one recording, revocable) ----------
def create_share(db, rid, uid, days=30):
    raw = secrets.token_urlsafe(24)
    db.q(
        "CREATE $r CONTENT $d",
        r=R("share_link", sha(raw)),
        d={"recording": rid, "created_by": uid, "created_at": store.now(), "expires_at": _later(24 * days), "revoked": False},
    )
    return raw


def share_ok(db, raw, rid):
    if not raw:
        return False
    s = db.one("SELECT recording, expires_at, revoked FROM $r", r=R("share_link", sha(raw)))
    return bool(s and s["recording"] == rid and not s.get("revoked") and s["expires_at"] >= store.now())


def revoke_shares(db, rid):
    db.q("UPDATE share_link SET revoked = true WHERE recording = $r", r=rid)


def audit(db, user, action, target=None, detail=None):
    db.q(
        "CREATE audit_log CONTENT $d",
        d=store.clean(
            {
                "at": store.now(),
                "account": (user or {}).get("id"),
                "email": (user or {}).get("email"),
                "action": action,
                "target": target,
                "detail": detail,
            }
        ),
    )
