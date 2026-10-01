"""IP groups, after Aviary's "public user with view permission in an IP group" (docs/access.md): address ranges whose
visitors see all of some recordings without signing in, as if they had permission on them.

A namespace's owners define its IP groups: a name, address ranges (single addresses or CIDR ranges), and what the group
opens: every recording in the namespace (`everything`), or the recordings chosen in each one's Access dialog.

The visitor's address is the request's peer, or, when the peer is a trusted proxy (`server.trusted_proxies`), the
address that proxy reports in X-Forwarded-For: see client_address(). An address the server can't vouch for opens
nothing.
"""

from __future__ import annotations

import functools
import ipaddress
from typing import NamedTuple

from . import store

R = store.R
MAX_RANGES = 100
NAME_MAX = 80
# The widest range an IP group may hold, as a prefix length: wider is most of the internet, i.e. a public recording.
WIDEST = {4: 8, 6: 16}
FIELDS = "record::id(id) AS id, space, name, ranges, everything, recordings, by, at, updated_by, updated_at"


# ---------- addresses ----------
def network(value):
    """One range: an address (203.0.113.7) or a CIDR range (203.0.113.0/24, 2001:db8::/48). Raises ValueError for
    anything else, and for ranges wider than WIDEST."""
    s = value.strip() if isinstance(value, str) else ""
    try:
        net = ipaddress.ip_network(s, strict=False)
    except ValueError:
        shown = s or ("an empty line" if isinstance(value, str) else repr(value))
        raise ValueError(f"{shown} isn't an address or a range like 203.0.113.0/24") from None
    if net.prefixlen < WIDEST[net.version]:
        raise ValueError(f"{s} is too wide: a range can be /{WIDEST[net.version]} at the widest")
    return net


def ranges(values):
    """Canonical ranges without repeats (a single address without its /32 or /128); at least one, at most MAX_RANGES."""
    if not isinstance(values, list) or not values:
        raise ValueError("list at least one address or range")
    out = []
    for v in values:
        net = network(v)
        s = str(net.network_address) if net.num_addresses == 1 else str(net)
        if s not in out:
            out.append(s)
    if len(out) > MAX_RANGES:
        raise ValueError(f"an IP group holds at most {MAX_RANGES} ranges")
    return out


def proxies(values):
    """server.trusted_proxies: addresses or ranges of any width, as canonical strings."""
    if not isinstance(values, list):
        raise ValueError("server.trusted_proxies is a list of addresses or ranges")
    out = []
    for v in values:
        try:
            out.append(str(ipaddress.ip_network(v.strip(), strict=False)))
        except (AttributeError, ValueError):
            raise ValueError(f"server.trusted_proxies: {v} isn't an address or a range like 10.0.0.0/8") from None
    return out


def address(raw):
    """An address from a connection's peer or an X-Forwarded-For entry ("203.0.113.7", "203.0.113.7:5678",
    "[2001:db8::1]:443"), with an IPv4-mapped IPv6 address as IPv4; None for anything else ("unknown", names)."""
    s = raw.strip().strip('"') if isinstance(raw, str) else ""
    if s.startswith("["):
        s = s[1 : s.find("]")] if "]" in s else ""
    elif s.count(":") == 1:  # IPv4 with a port
        s = s.split(":")[0]
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return None
    return (ip.ipv4_mapped or ip) if isinstance(ip, ipaddress.IPv6Address) else ip


@functools.lru_cache(maxsize=256)
def _networks(values):
    out = []
    for v in values:
        try:
            out.append(ipaddress.ip_network(str(v).strip(), strict=False))
        except ValueError:
            continue
    return tuple(out)


def within(addr, values):
    """Whether an address is in any of these ranges."""
    return addr is not None and any(addr in n for n in _networks(tuple(values or ())))


def client_address(peer, port, forwarded, trusted):
    """The address a request comes from, or None when the server can't vouch for one.

    Starting at the connection's peer: while the address is a trusted proxy, step to the one it reports, the next
    X-Forwarded-For entry from the right. The first address that isn't a trusted proxy is the visitor's. None when:

    - a peer that isn't trusted forwarded someone else's request (it sent X-Forwarded-For): its own address isn't the
      visitor's (the web app in a container, until its address is in server.trusted_proxies);
    - every hop is a trusted proxy (the web app asking on its own behalf);
    - an entry isn't an address.

    uvicorn's own proxy handling (FORWARDED_ALLOW_IPS, 127.0.0.1 unless set) may already have put a forwarded address
    in place of the peer, which it marks with port 0; the walk then starts over from the right of X-Forwarded-For.
    """
    hops = [h for h in (x.strip() for x in (forwarded or "").split(",")) if h]
    if port != 0:
        addr = address(peer)
        if addr is None:
            return None
        if not within(addr, trusted):
            return None if hops else addr
    for raw in reversed(hops):
        addr = address(raw)
        if addr is None:
            return None
        if not within(addr, trusted):
            return addr
    return None


# ---------- what an address opens ----------
class Network(NamedTuple):
    """What the visitor's address opens: whole namespaces and chosen recordings, each with the name of the IP group
    that opens it."""

    spaces: dict
    recordings: dict

    def opens(self, rid, space):
        return space in self.spaces or rid in self.recordings

    def name(self, rid, space):
        return self.spaces.get(space) or self.recordings.get(rid)


NOWHERE = Network({}, {})


def of(db, addr):
    """What an address opens. A chosen recording counts only while it is in the group's namespace."""
    if addr is None:
        return NOWHERE
    spaces, recs = {}, {}
    for g in db.rows("SELECT space, name, ranges, everything, recordings FROM ip_group ORDER BY name"):
        if not within(addr, g.get("ranges")):
            continue
        if g.get("everything"):
            spaces.setdefault(g["space"], g["name"])
        elif g.get("recordings"):
            ids = [R("recording", i) for i in g["recordings"]]
            for rid in db.values("SELECT VALUE record::id(id) FROM recording WHERE id IN $ids AND space = $s", ids=ids, s=g["space"]):
                recs.setdefault(rid, g["name"])
    return Network(spaces, recs)


# ---------- a namespace's IP groups ----------
def groups(db, space):
    """A namespace's IP groups, by name."""
    return db.rows(f"SELECT {FIELDS} FROM ip_group WHERE space = $s ORDER BY name", s=space)


def get(db, gid, space=None):
    """One IP group (of this namespace, when given); KeyError when there is none."""
    g = db.one(f"SELECT {FIELDS} FROM $g", g=R("ip_group", gid))
    if not g or (space is not None and g["space"] != space):
        raise KeyError(gid)
    return g


def _name(db, space, name, gid=None):
    n = " ".join((name or "").split())
    if not n:
        raise ValueError("give the IP group a name")
    if len(n) > NAME_MAX:
        raise ValueError(f"a name has at most {NAME_MAX} characters")
    for g in db.rows("SELECT record::id(id) AS id, name FROM ip_group WHERE space = $s", s=space):
        if g["id"] != gid and g["name"].casefold() == n.casefold():
            raise ValueError(f"there is already an IP group called {g['name']} here")
    return n


def create(db, space, name, values, everything=False, by=None):
    gid = db.next_id("ip_group")
    d = {
        "space": space,
        "name": _name(db, space, name),
        "ranges": ranges(values),
        "everything": bool(everything),
        "recordings": [],
        "by": by,
        "at": store.now(),
    }
    db.q("CREATE $g CONTENT $d", g=R("ip_group", gid), d=d)
    return get(db, gid)


def update(db, gid, space, changes, by=None):
    """Change a group's name, ranges or what it opens (keys left out stay as they are)."""
    get(db, gid, space)
    sets = {"updated_by": by, "updated_at": store.now()}
    if changes.get("name") is not None:
        sets["name"] = _name(db, space, changes["name"], gid)
    if changes.get("ranges") is not None:
        sets["ranges"] = ranges(changes["ranges"])
    if changes.get("everything") is not None:
        sets["everything"] = bool(changes["everything"])
    db.q("UPDATE $g MERGE $d", g=R("ip_group", gid), d=sets)
    return get(db, gid)


def delete(db, gid, space):
    """Delete a group; returns it as it was."""
    g = get(db, gid, space)
    db.q("DELETE $g", g=R("ip_group", gid))
    return g


def for_recording(db, rid, space):
    """The namespace's IP groups, each with whether it opens this recording."""
    return [{**g, "opens": bool(g.get("everything")) or rid in (g.get("recordings") or [])} for g in groups(db, space)]


def choose(db, gid, space, rid, on=True):
    """Open a recording to a group that opens chosen recordings (on), or close it again. False when nothing changed.
    A group that opens everything can't choose."""
    g = get(db, gid, space)
    if g.get("everything"):
        raise ValueError(f"{g['name']} opens every recording in the namespace")
    if (rid in (g.get("recordings") or [])) == on:
        return False
    fn = "array::union" if on else "array::complement"
    db.q(f"UPDATE $g SET recordings = {fn}(recordings ?? [], [$r])", g=R("ip_group", gid), r=rid)
    return True
