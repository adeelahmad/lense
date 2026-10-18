"""Lens's own private network between the machines it runs on: a Nebula mesh (docs/mesh.md).

A single machine needs none, so it is off until an admin adds a node (Settings › Nodes, or `POST /mesh/tokens`), which
turns it on. Lens is the mesh's certificate authority. It makes the CA the first time (its key sealed in the database
like a setting's secret), and every machine gets an address in `mesh.network` and a certificate naming its role and
the namespaces it serves as Nebula groups:

* the **primary**, this install: the lighthouse other machines find each other through, and the relay for those that
  can't reach each other directly. It is the first address of the network.
* **worker** nodes run jobs against this install's database, which they reach over the mesh only.
* **compute** nodes run code in sandboxes for the assistant, and get nothing of Lens's: no database, no keys.

A node joins with a join token, shown once to the admin who made it. The token never crosses the wire: the node sends
its token's id, its own public key (its private key never leaves it) and an HMAC of them keyed with the token's
secret, and the answer (its certificate, the mesh's settings and the credentials it signs in to this install with) is
sealed with a key derived from that secret, so a join over plain http:// on the LAN gives an eavesdropper nothing and
a machine in the middle can neither join nor read the answer. After that a node talks to this install over the mesh
only, and signs every call with its own credentials too: Lens trusts no address, the mesh's included.

Certificates last `mesh.cert_days`; a node renews its own at two thirds of that. Removing a node puts its certificates
on the mesh's blocklist, which every machine's config carries, so Nebula drops its tunnels and refuses new ones.

`nebula-cert` (from Nebula's release, MIT) makes the CA and signs certificates: on PATH, in LENS_NEBULA_DIR, or fetched
into data_dir/bin on first use, its checksum checked against the one kept here.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import os
import pathlib
import platform
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import zipfile

import httpx

from . import settings, store

R = store.R
NEBULA_VERSION = "1.11.2"
RELEASES = f"https://github.com/slackhq/nebula/releases/download/v{NEBULA_VERSION}/"
# Nebula's own SHASUM256.txt for these downloads, kept here so a changed file is refused
SHA256 = {
    "nebula-linux-amd64.tar.gz": "6140d33f2ec21ce7f6b655b5bc820e93a684d97e51d0ddcf907324b5b28aac1e",
    "nebula-linux-arm64.tar.gz": "85d10e7bc2d121193c1392a1a919172ded7c413f46e602138281cfa9fa1b0231",
    "nebula-linux-arm-7.tar.gz": "dc9c890d7ec014ab709e0444592b33f35db4f396f28245c95887844b1565ff41",
    "nebula-linux-arm-6.tar.gz": "d8f86452620eb6ab76bdf9ed9841a309ba8122ac2980c2b7c7e2d1aa8d160e84",
    "nebula-darwin.zip": "ae23ebd29e570d72f28d71e608261d454fb87b1a19b4ca21c2aa26bb4698da88",
}
CA_ROW = R("mesh_ca", "current")
CA_YEARS = 10
ROLES = ("worker", "compute")
GROUP_PRIMARY, GROUP_NODE = "lens-primary", "lens-node"
# ports on the mesh: the primary serves the database (to worker nodes) and the calls nodes make (to every node); a
# node's agent serves the primary only
DB_PORT, API_PORT, AGENT_PORT = 8000, 8080, 7443
INTERFACE = "lens-mesh"
TOKEN_PREFIX = "lensjoin1."
NODE_PREFIX = "lensnode1."
TOKEN_HOURS = (1, 24 * 30)
TOKEN_USES = (1, 100)
NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,62}$")
NS_RX = re.compile(r"^[a-z0-9_-]{1,64}$")
PUBLIC_KEY_RX = re.compile(
    r"^-----BEGIN NEBULA (X25519|P256) PUBLIC KEY-----\n[A-Za-z0-9+/=\n]{40,200}-----END NEBULA (X25519|P256) PUBLIC KEY-----\n?$"
)
HOST_RX = re.compile(r"^(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9][A-Za-z0-9.-]{0,252})(:\d{1,5})?$")
JOIN_INFO = b"lens-mesh-join"
NODE_FIELDS = (
    "record::id(id) AS id, name, role, ip, groups, namespaces, fingerprint, not_after, joined_at, joined_by, last_seen, "
    "status, revoked_at, revoked_by, hosts"
)


class MeshError(RuntimeError):
    """Something about the mesh that didn't work; the message says why, for the admin."""


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _ts(t=None):
    return (t or _now()).isoformat(timespec="seconds")


def conf(cfg):
    return {**store.DEFAULTS["mesh"], **(cfg.get("mesh") or {})}


def network(cfg):
    return ipaddress.ip_network(conf(cfg)["network"], strict=False)


def primary_ip(cfg):
    """The primary's address on the mesh: the network's first."""
    return str(next(network(cfg).hosts()))


def host_port(value, port):
    """`host` or `host:port` (an IPv6 address in brackets) as host:port, with the mesh's port when it has none."""
    v = str(value).strip()
    if not HOST_RX.fullmatch(v):
        raise ValueError(f"{v!r} isn't a host name or address, with or without :port")
    if v.startswith("["):
        return v if "]:" in v else f"{v}:{port}"
    return v if ":" in v else f"{v}:{port}"


def check_setting(key, value):
    """settings.save: mesh.<key> checked and normalised; ValueError for what can't work."""
    if key == "network":
        try:
            net = ipaddress.ip_network(str(value).strip(), strict=False)
        except ValueError:
            raise ValueError("mesh.network is an address range, like 10.73.0.0/16") from None
        if net.version != 4 or not net.is_private or net.prefixlen > 28 or net.prefixlen < 8:
            raise ValueError("mesh.network is a private IPv4 range from /8 to /28, like 10.73.0.0/16")
        return str(net)
    if key == "hosts":
        if not isinstance(value, list) or len(value) > 8:
            raise ValueError("mesh.hosts is a list of up to 8 addresses, like lens.example.com:4242")
        out = []
        for v in value:
            v = str(v).strip()
            if not HOST_RX.fullmatch(v):
                raise ValueError(f"mesh.hosts: {v!r} isn't a host name or address, with or without :port")
            out.append(v)
        return out
    if key in ("port", "mtu", "cert_days"):
        lo, hi = {"port": (1, 65535), "mtu": (1200, 9000), "cert_days": (2, 365)}[key]
        if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
            raise ValueError(f"mesh.{key} is a whole number from {lo} to {hi}")
        return value
    if key in ("enabled", "relay"):
        if not isinstance(value, bool):
            raise ValueError(f"mesh.{key} is true or false")
        return value
    return value


def check(db, cfg, changes):
    """Before mesh settings are saved: the network can't move while machines have addresses in it."""
    if "network" in changes and changes["network"] != conf(cfg)["network"] and ca_row(db):
        raise ValueError("the mesh's network can't change once it has a certificate authority: remove the nodes first")


# ---------- nebula-cert ----------
def _platform():
    system = platform.system().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64", "armv7l": "arm-7", "armv6l": "arm-6"}.get(
        platform.machine().lower()
    )
    if system == "darwin":
        return "nebula-darwin.zip"
    if system != "linux" or not arch:
        raise MeshError(f"install Nebula {NEBULA_VERSION} yourself on this system ({system} {platform.machine()}); Lens finds it on PATH")
    return f"nebula-linux-{arch}.tar.gz"


def _fetched(cfg):
    return pathlib.Path(cfg["data_dir"]) / "bin" / f"nebula-{NEBULA_VERSION}"


def binaries(cfg):
    """{"nebula": path, "nebula-cert": path} from LENS_NEBULA_DIR, PATH or the copy Lens fetched; None without both."""
    for d in (os.environ.get("LENS_NEBULA_DIR"), None, str(_fetched(cfg))):
        found = {}
        for name in ("nebula", "nebula-cert"):
            exe = os.path.join(d, name) if d else shutil.which(name)
            if exe and os.path.isfile(exe) and os.access(exe, os.X_OK):
                found[name] = exe
        if len(found) == 2:
            return found
    return None


def fetch(cfg, say=None, dest=None):
    """Download Nebula's release for this machine into data_dir/bin (or `dest`), checksum checked. Returns binaries()."""
    name = _platform()
    out = pathlib.Path(dest) if dest else _fetched(cfg)
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / (name + ".part")
    if say:
        say(f"downloading Nebula {NEBULA_VERSION} ({name})")
    h = hashlib.sha256()
    try:
        with httpx.stream("GET", RELEASES + name, follow_redirects=True, timeout=300) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes():
                    h.update(chunk)
                    f.write(chunk)
    except httpx.HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise MeshError(f"couldn't download Nebula from GitHub: {e}") from None
    if h.hexdigest() != SHA256[name]:
        tmp.unlink(missing_ok=True)
        raise MeshError("the Nebula download doesn't match its checksum; try again")
    try:
        for prog in ("nebula", "nebula-cert"):
            if name.endswith(".zip"):
                with zipfile.ZipFile(tmp) as z, z.open(prog) as src, open(out / f"{prog}.new", "wb") as f:
                    shutil.copyfileobj(src, f)
            else:
                with tarfile.open(tmp) as t:
                    member = t.getmember(prog)
                    src = t.extractfile(member)
                    if src is None:
                        raise KeyError(prog)
                    with src, open(out / f"{prog}.new", "wb") as f:
                        shutil.copyfileobj(src, f)
            (out / f"{prog}.new").chmod(0o755)
            (out / f"{prog}.new").replace(out / prog)
    except (KeyError, OSError, tarfile.TarError, zipfile.BadZipFile) as e:
        raise MeshError(f"the Nebula download has no {e} in it") from None
    finally:
        tmp.unlink(missing_ok=True)
    return {"nebula": str(out / "nebula"), "nebula-cert": str(out / "nebula-cert")}


def tools(cfg, say=None):
    return binaries(cfg) or fetch(cfg, say)


def _cert_tool(cfg, args, stdin=None):
    """Run nebula-cert; its stdout. MeshError with what it said when it fails."""
    exe = tools(cfg)["nebula-cert"]
    try:
        p = subprocess.run([exe, *args], input=stdin, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise MeshError(f"nebula-cert didn't run: {e}") from None
    if p.returncode != 0:
        raise MeshError(f"nebula-cert: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def describe(cfg, pem):
    """A certificate's details from nebula-cert print: name, networks, groups, fingerprint, not_after, ..."""
    out = json.loads(_cert_tool(cfg, ["print", "-json", "-path", "-"], pem))
    c = out[0] if isinstance(out, list) else out
    d = c.get("details") or {}
    return {
        "name": d.get("name"),
        "networks": d.get("networks") or d.get("ips") or [],
        "groups": d.get("groups") or [],
        "is_ca": bool(d.get("isCa")),
        "issuer": d.get("issuer"),
        "not_before": d.get("notBefore"),
        "not_after": d.get("notAfter"),
        "fingerprint": c.get("fingerprint"),
        "public_key": c.get("publicKey"),
    }


def keygen(cfg):
    """A new X25519 key pair, (private PEM, public PEM): for this machine's own certificate (and tests')."""
    with tempfile.TemporaryDirectory(prefix="lens-mesh-") as d:
        _cert_tool(cfg, ["keygen", "-out-key", f"{d}/k", "-out-pub", f"{d}/p"])
        return pathlib.Path(d, "k").read_text(), pathlib.Path(d, "p").read_text()


# ---------- the certificate authority ----------
def ca_row(db):
    return db.one("SELECT * FROM $r", r=CA_ROW)


def ca(db, cfg):
    """The mesh's CA, made the first time: {crt, fingerprint, not_after, network}. Its key stays sealed in the row."""
    row = ca_row(db)
    if row:
        return {k: row.get(k) for k in ("crt", "fingerprint", "not_after", "network", "created_at")}
    net = conf(cfg)["network"]
    with tempfile.TemporaryDirectory(prefix="lens-mesh-") as d:
        _cert_tool(
            cfg,
            [
                "ca",
                "-name",
                f"Lens mesh {secrets.token_hex(3)}",
                "-networks",
                net,
                "-duration",
                f"{CA_YEARS * 8760}h",
                "-out-crt",
                f"{d}/ca.crt",
                "-out-key",
                f"{d}/ca.key",
            ],
        )
        crt, key = pathlib.Path(d, "ca.crt").read_text(), pathlib.Path(d, "ca.key").read_text()
    info = describe(cfg, crt)
    row = {
        "crt": crt,
        "key": settings.seal(cfg, key, "mesh:ca.key"),
        "fingerprint": info["fingerprint"],
        "not_after": info["not_after"],
        "network": net,
        "created_at": store.now(),
        "blocklist": [],
    }
    try:
        db.q("CREATE $r CONTENT $d", r=CA_ROW, d=row)
    except Exception:  # noqa: BLE001 - another process made it first: use theirs
        if not ca_row(db):
            raise
    return ca(db, cfg)


def _sign(db, cfg, name, ip, groups, public_key):
    """A certificate for `public_key` at `ip`, signed by the CA (whose key reaches nebula-cert on stdin only)."""
    if not PUBLIC_KEY_RX.fullmatch(public_key.replace("\r\n", "\n")):
        raise ValueError("that isn't a Nebula public key (nebula-cert keygen makes one)")
    authority = ca(db, cfg)
    key = settings.unseal(cfg, ca_row(db)["key"], "mesh:ca.key")
    hours = conf(cfg)["cert_days"] * 24
    prefix = network(cfg).prefixlen
    with tempfile.TemporaryDirectory(prefix="lens-mesh-") as d:
        pathlib.Path(d, "ca.crt").write_text(authority["crt"])
        pathlib.Path(d, "host.pub").write_text(public_key)
        crt = _cert_tool(
            cfg,
            [
                "sign",
                "-ca-crt",
                f"{d}/ca.crt",
                "-ca-key",
                "-",
                "-name",
                name,
                "-networks",
                f"{ip}/{prefix}",
                "-groups",
                ",".join(groups),
                "-in-pub",
                f"{d}/host.pub",
                "-out-crt",
                "-",
                "-duration",
                f"{hours}h",
            ],
            stdin=key,
        )
    return crt, describe(cfg, crt)


# ---------- machines on the mesh ----------
def _groups(role, namespaces):
    base = [GROUP_PRIMARY] if role == "primary" else [GROUP_NODE, f"lens-{role}"]
    return base + [f"ns-{n}" for n in namespaces or ()]


def _view(row):
    if not row:
        return None
    out = {
        k: row.get(k) for k in ("id", "name", "role", "ip", "groups", "namespaces", "fingerprint", "not_after", "joined_at", "joined_by")
    }
    out |= {
        "last_seen": row.get("last_seen"),
        "status": row.get("status") or {},
        "revoked_at": row.get("revoked_at"),
        "revoked_by": row.get("revoked_by"),
        "hosts": row.get("hosts") or [],
    }
    return out


def nodes(db, revoked=True):
    """Every machine on the mesh, the primary first."""
    rows = db.rows(f"SELECT {NODE_FIELDS} FROM mesh_node ORDER BY id")
    rows.sort(key=lambda r: (r.get("role") != "primary", r["id"]))
    return [_view(r) for r in rows if revoked or not r.get("revoked_at")]


def node(db, nid):
    row = db.one(f"SELECT {NODE_FIELDS} FROM $r", r=R("mesh_node", int(nid)))
    if not row:
        raise KeyError(nid)
    return _view(row)


def _taken(db):
    return {r for r in db.values("SELECT VALUE ip FROM mesh_node") if r}


def _next_ip(db, cfg):
    taken = _taken(db) | {primary_ip(cfg)}
    for ip in network(cfg).hosts():  # addresses are never given out again, so a removed node's stays its own
        if str(ip) not in taken:
            return str(ip)
    raise MeshError("the mesh's network is full: give it a bigger range in Settings › Network before adding the CA")


def primary(db, cfg, public_key=None):
    """The primary's row, made the first time; with `public_key`, a new certificate for it (it renews its own)."""
    row = db.one(f"SELECT {NODE_FIELDS} FROM mesh_node WHERE role = 'primary' LIMIT 1")
    if not row:
        nid = db.next_id("mesh_node")
        db.q(
            "CREATE $r CONTENT $d",
            r=R("mesh_node", nid),
            d={
                "name": "This machine",
                "role": "primary",
                "ip": primary_ip(cfg),
                "groups": _groups("primary", []),
                "namespaces": [],
                "joined_at": store.now(),
            },
        )
        row = db.one(f"SELECT {NODE_FIELDS} FROM $r", r=R("mesh_node", nid))
    if public_key:
        crt, info = _sign(db, cfg, "primary", row["ip"], row["groups"], public_key)
        _record_cert(db, row["id"], info, crt)
        db.q("UPDATE $r SET public_key = $p", r=R("mesh_node", row["id"]), p=public_key)
        return {**_view(row), "crt": crt, "fingerprint": info["fingerprint"], "not_after": info["not_after"], "public_key": public_key}
    return _view(row)


def _record_cert(db, nid, info, crt):
    """A machine's newest certificate; every one it was given stays listed, for the blocklist if it's removed."""
    db.q(
        "UPDATE $r SET crt = $c, fingerprint = $f, not_after = $n, fingerprints = array::distinct(array::union(fingerprints ?? [], [$f]))",
        r=R("mesh_node", int(nid)),
        c=crt,
        f=info["fingerprint"],
        n=info["not_after"],
    )


def hosts(db, cfg, row=None):
    """Where nodes reach the primary's mesh port from outside the mesh: mesh.hosts, or the address the node joined
    at."""
    c = conf(cfg)
    if c["hosts"]:
        return [host_port(h, c["port"]) for h in c["hosts"]]
    return [host_port(h, c["port"]) for h in (row or {}).get("hosts") or []]


def blocklist(db):
    """The fingerprints of removed nodes' certificates, which every machine refuses."""
    out = []
    for r in db.rows("SELECT fingerprints FROM mesh_node WHERE revoked_at != NONE"):
        out.extend(r.get("fingerprints") or [])
    return sorted(set(out))


def render(db, cfg, row, crt):
    """A machine's Nebula config, as a dict to write out as YAML; the private key is the machine's own to add."""
    c = conf(cfg)
    authority = ca(db, cfg)
    me_primary = row["role"] == "primary"
    lighthouse = primary_ip(cfg)
    inbound = [{"proto": "icmp", "host": "any"}]
    if me_primary:
        inbound += [
            {"port": DB_PORT, "proto": "tcp", "group": "lens-worker"},
            {"port": API_PORT, "proto": "tcp", "group": GROUP_NODE},
        ]
    else:
        inbound += [{"port": AGENT_PORT, "proto": "tcp", "group": GROUP_PRIMARY}]
    out = {
        "pki": {"ca": authority["crt"], "cert": crt, "blocklist": blocklist(db), "disconnect_invalid": True},
        "static_host_map": {} if me_primary else {lighthouse: hosts(db, cfg, row)},
        "lighthouse": {"am_lighthouse": me_primary, "interval": 60, "hosts": [] if me_primary else [lighthouse]},
        "listen": {"host": "0.0.0.0", "port": c["port"] if me_primary else 0},
        "punchy": {"punch": True, "respond": True},
        "relay": {"am_relay": me_primary and c["relay"], "use_relays": c["relay"], "relays": [] if me_primary else [lighthouse]},
        "tun": {"dev": INTERFACE, "mtu": c["mtu"], "drop_local_broadcast": True, "drop_multicast": True},
        "cipher": c["cipher"],
        "logging": {"level": c["log_level"], "format": "json"},
        "firewall": {
            "outbound_action": "drop",
            "inbound_action": "drop",
            "conntrack": {"tcp_timeout": "12m", "udp_timeout": "3m", "default_timeout": "10m"},
            "outbound": [{"port": "any", "proto": "any", "host": "any"}],
            "inbound": inbound,
        },
    }
    return out


def version(config):
    """A short hash of a config, so a machine knows when to reload."""
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]


# ---------- join tokens ----------
def _token_view(row):
    out = {
        k: row.get(k)
        for k in ("id", "role", "name", "namespaces", "expires_at", "uses", "used", "created_by", "created_at", "last_used_at")
    }
    out["namespaces"] = out["namespaces"] or []
    out["used"] = out["used"] or 0
    out["expired"] = bool(row.get("expires_at") and row["expires_at"] < _ts())
    return out


def tokens(db):
    """Join tokens that can still be used, newest first (their secrets are never shown again)."""
    rows = db.rows("SELECT record::id(id) AS id, * FROM mesh_token ORDER BY id DESC")
    return [_token_view(r) for r in rows if not r.get("revoked_at") and (r.get("used") or 0) < (r.get("uses") or 1)]


def make_token(db, cfg, by=None, role="worker", name=None, namespaces=None, hours=24, uses=1):
    """A token another machine joins with: (token row, the token itself, shown this once). Makes the CA if there isn't
    one yet. The caller turns the mesh on (settings), which is what adding a node means."""
    if role not in ROLES:
        raise ValueError(f"a node is a {' or a '.join(ROLES)}")
    name = (name or "").strip() or None
    if name and not NAME_RX.fullmatch(name):
        raise ValueError("a node's name is letters, digits, spaces, dots, dashes and underscores, up to 63")
    namespaces = sorted(set(namespaces or ()))
    known = set(store.space_names(db).values())
    for n in namespaces:
        if not NS_RX.fullmatch(n) or n not in known:
            raise ValueError(f"there's no namespace {n!r}")
    for v, (lo, hi), what in ((hours, TOKEN_HOURS, "hours"), (uses, TOKEN_USES, "uses")):
        if not (isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi):
            raise ValueError(f"a token's {what} is a whole number from {lo} to {hi}")
    if role == "worker" and not str(db.url).startswith(("ws://", "wss://", "http://", "https://")):
        raise ValueError(
            "a worker on another machine needs this install's database to be a SurrealDB server (Docker installs have one); "
            "add a compute node instead"
        )
    ca(db, cfg)
    primary(db, cfg)
    tid, secret = db.next_id("mesh_token"), secrets.token_urlsafe(32)
    db.q(
        "CREATE $r CONTENT $d",
        r=R("mesh_token", tid),
        d=store.clean(
            {
                "role": role,
                "name": name,
                "namespaces": namespaces,
                "secret": settings.seal(cfg, secret, f"mesh:token.{tid}"),
                "expires_at": _ts(_now() + dt.timedelta(hours=hours)),
                "uses": uses,
                "used": 0,
                "created_by": by,
                "created_at": store.now(),
            }
        ),
    )
    row = db.one("SELECT record::id(id) AS id, * FROM $r", r=R("mesh_token", tid))
    return _token_view(row), f"{TOKEN_PREFIX}{tid}.{secret}"


def revoke_token(db, tid):
    if not db.one("SELECT id FROM $r", r=R("mesh_token", int(tid))):
        raise KeyError(tid)
    db.q("UPDATE $r SET revoked_at = $t", r=R("mesh_token", int(tid)), t=store.now())


def parse_token(token):
    """(id, secret) of a join token; ValueError when it isn't one."""
    m = re.fullmatch(re.escape(TOKEN_PREFIX) + r"(\d{1,12})\.([A-Za-z0-9_-]{20,100})", (token or "").strip())
    if not m:
        raise ValueError("that isn't a Lens join token")
    return int(m.group(1)), m.group(2)


def _material(token_id, nonce, name, public_key):
    return "\n".join(["lens-mesh-join", str(token_id), nonce, name, public_key]).encode()


def proof(secret, token_id, nonce, name, public_key):
    """What a joining machine sends to show it holds the token: an HMAC of the request, keyed with the token's
    secret."""
    return hmac.new(secret.encode(), _material(token_id, nonce, name, public_key), hashlib.sha256).hexdigest()


def _box_key(secret, nonce):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(algorithm=hashes.SHA256(), length=32, salt=nonce.encode(), info=JOIN_INFO).derive(secret.encode())


def seal_answer(secret, nonce, token_id, payload):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    iv = secrets.token_bytes(12)
    box = AESGCM(_box_key(secret, nonce)).encrypt(iv, json.dumps(payload).encode(), str(token_id).encode())
    return base64.urlsafe_b64encode(iv + box).decode()


def open_answer(secret, nonce, token_id, sealed):
    """The joining machine's side of seal_answer: the payload, or ValueError when it wasn't sealed with the token."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = base64.urlsafe_b64decode(sealed)
    try:
        return json.loads(AESGCM(_box_key(secret, nonce)).decrypt(raw[:12], raw[12:], str(token_id).encode()))
    except InvalidTag:
        raise ValueError("the answer wasn't sealed with this token: is that the right Lens?") from None


def _hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def join(db, cfg, token_id, nonce, name, public_key, sent_proof, joined_at=None, info=None):
    """A machine joins with a token (parse_token's id, the HMAC from proof()): it gets an address, a certificate and
    its credentials, sealed for it (seal_answer). PermissionError for a token that's wrong, used up or expired."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", nonce or ""):
        raise ValueError("the join request's nonce is 16 to 64 letters, digits, - or _")
    if not NAME_RX.fullmatch(name or ""):
        raise ValueError("a node's name is letters, digits, spaces, dots, dashes and underscores, up to 63")
    tok = db.one("SELECT record::id(id) AS id, * FROM $r", r=R("mesh_token", int(token_id)))
    if not tok or tok.get("revoked_at"):
        raise PermissionError("that join token isn't this install's, or it was withdrawn")
    try:
        secret = settings.unseal(cfg, tok["secret"], f"mesh:token.{tok['id']}")
    except Exception:  # noqa: BLE001 - the key changed since it was made
        raise PermissionError("that join token can't be read any more: make a new one") from None
    if not hmac.compare_digest(proof(secret, token_id, nonce, name, public_key), str(sent_proof or "")):
        raise PermissionError("the join request wasn't made with that token")
    if (tok.get("used") or 0) >= (tok.get("uses") or 1):
        raise PermissionError("that join token has been used: make a new one in Settings › Nodes")
    if tok.get("expires_at") and tok["expires_at"] < _ts():
        raise PermissionError("that join token has expired: make a new one in Settings › Nodes")
    used = db.values(
        "UPDATE $r SET used += 1, last_used_at = $t WHERE (used ?? 0) < (uses ?? 1) RETURN VALUE used",
        r=R("mesh_token", tok["id"]),
        t=store.now(),
    )
    if not used:  # another machine took the last use at the same moment
        raise PermissionError("that join token has been used: make a new one in Settings › Nodes")
    role, namespaces = tok["role"], tok.get("namespaces") or []
    nid, ip = db.next_id("mesh_node"), _next_ip(db, cfg)
    groups = _groups(role, namespaces)
    node_secret, agent_key = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.q(
        "CREATE $r CONTENT $d",
        r=R("mesh_node", nid),
        d=store.clean(
            {
                "name": tok.get("name") or name,
                "role": role,
                "ip": ip,
                "groups": groups,
                "namespaces": namespaces,
                "secret_hash": _hash(node_secret),
                "agent_key": settings.seal(cfg, agent_key, f"mesh:agent.{nid}"),
                "token": tok["id"],
                "joined_at": store.now(),
                "joined_by": tok.get("created_by"),
                "hosts": [joined_at] if joined_at else [],
                "status": store.clean({"info": info}) if info else None,
            }
        ),
    )
    try:
        crt, cert_info = _sign(db, cfg, f"node-{nid}", ip, groups, public_key)
    except Exception:
        db.q("DELETE $r", r=R("mesh_node", nid))
        raise
    _record_cert(db, nid, cert_info, crt)
    row = db.one(f"SELECT {NODE_FIELDS} FROM $r", r=R("mesh_node", nid))
    config = render(db, cfg, row, crt)
    payload = {
        "node": {"id": nid, "name": row["name"], "role": role, "ip": ip, "groups": groups, "namespaces": namespaces},
        "primary": {"ip": primary_ip(cfg), "db_port": DB_PORT, "api_port": API_PORT},
        "agent_port": AGENT_PORT,
        "config": config,
        "version": version(config),
        "not_after": cert_info["not_after"],
        "node_token": f"{NODE_PREFIX}{nid}.{node_secret}",
        "agent_key": agent_key,
        "renew_days": renew_days(cfg),
    }
    return nid, seal_answer(secret, nonce, token_id, payload)


def renew_days(cfg):
    """How many days before its certificate runs out a machine renews it: a third of its life."""
    return max(1, conf(cfg)["cert_days"] // 3)


# ---------- a node's own calls ----------
def by_token(db, token):
    """The node a node token belongs to, or None (a removed node's included: it gets nothing)."""
    m = re.fullmatch(re.escape(NODE_PREFIX) + r"(\d{1,12})\.([A-Za-z0-9_-]{20,100})", (token or "").strip())
    if not m:
        return None
    row = db.one(f"SELECT {NODE_FIELDS}, secret_hash FROM $r", r=R("mesh_node", int(m.group(1))))
    if not row or row.get("revoked_at") or not hmac.compare_digest(row.get("secret_hash") or "", _hash(m.group(2))):
        return None
    return row


def config_for(db, cfg, row):
    """A node's config as it is now (with the certificate it has), and its version."""
    crt = (db.one("SELECT crt FROM $r", r=R("mesh_node", row["id"])) or {}).get("crt")
    config = render(db, cfg, row, crt or "")
    return {"config": config, "version": version(config), "not_after": row.get("not_after"), "renew_days": renew_days(cfg)}


def renew(db, cfg, row, public_key):
    """A new certificate for a node's (new or same) public key, with the groups its row says now."""
    groups = _groups(row["role"], row.get("namespaces"))
    crt, info = _sign(db, cfg, f"node-{row['id']}", row["ip"], groups, public_key)
    _record_cert(db, row["id"], info, crt)
    db.q("UPDATE $r SET groups = $g", r=R("mesh_node", row["id"]), g=groups)
    row = db.one(f"SELECT {NODE_FIELDS} FROM $r", r=R("mesh_node", row["id"]))
    return {"crt": crt, **config_for(db, cfg, row)}


def beat(db, row, status):
    """What a node reports every minute: whether Nebula runs, its peers, its machine. Kept as the node's status."""
    keep = {k: status.get(k) for k in ("nebula", "version", "peers", "machine", "sandbox", "error", "log") if k in status}
    if isinstance(keep.get("log"), list):
        keep["log"] = [str(x)[:300] for x in keep["log"][-40:]]
    db.q(
        "UPDATE $r SET last_seen = $t, status = $s", r=R("mesh_node", row["id"]), t=store.now(), s=json.loads(json.dumps(keep, default=str))
    )


# ---------- the admin's changes ----------
def update(db, nid, name=None, namespaces=None):
    """Rename a node, or change the namespaces it serves (its certificate's groups follow when it next renews, which
    its next config fetch asks for)."""
    n = node(db, nid)
    patch = {}
    if name is not None:
        name = name.strip()
        if not NAME_RX.fullmatch(name):
            raise ValueError("a node's name is letters, digits, spaces, dots, dashes and underscores, up to 63")
        patch["name"] = name
    if namespaces is not None:
        if n["role"] == "primary":
            raise ValueError("the primary serves every namespace")
        known = set(store.space_names(db).values())
        bad = [x for x in namespaces if x not in known]
        if bad:
            raise ValueError(f"there's no namespace {bad[0]!r}")
        patch["namespaces"] = sorted(set(namespaces))
        patch["groups"] = _groups(n["role"], patch["namespaces"])
    if patch:
        db.q("UPDATE $r MERGE $p", r=R("mesh_node", int(nid)), p=patch)
    return node(db, nid)


def remove(db, nid, by=None):
    """Take a node off the mesh: its certificates go on the blocklist and its credentials stop working. undo()
    brings it back while its certificate is still good."""
    n = node(db, nid)
    if n["role"] == "primary":
        raise ValueError("the primary is this machine; turn the mesh off in Settings › Network instead")
    db.q("UPDATE $r SET revoked_at = $t, revoked_by = $b", r=R("mesh_node", int(nid)), t=store.now(), b=by)
    return node(db, nid)


def undo_remove(db, nid):
    n = node(db, nid)
    if not n["revoked_at"]:
        return n
    if n["not_after"] and n["not_after"] < _ts():
        raise ValueError("its certificate has run out since: add it again with a new token")
    db.q("UPDATE $r SET revoked_at = NONE, revoked_by = NONE", r=R("mesh_node", int(nid)))
    return node(db, nid)


# ---------- Nebula on this machine: the `mesh` container ----------
# Lens's server can't hold NET_ADMIN, so the primary's Nebula runs in the optional `mesh` container (mesh/sidecar.py),
# which shares a folder with it (LENS_MESH_DIR): Lens writes lens.json there (on or off, the config, the public key it
# signed), the container writes its public key (host.pub) and how it runs (status.json). The keeper below does
# Lens's half, in one process at a time (a lease on app_service:mesh_keeper), and asks the machine's upgrade agent to
# start the container when the mesh is turned on and stop it when it's turned off (services.py, profile `mesh`).
SERVICE = "mesh"
KEEPER_ROW = R("app_service", "mesh_keeper")
KEEPER_SECONDS, LEASE_SECONDS, BEAT_SECONDS = 10, 45, 60
STATUS_STALE = 30


def shared_dir():
    """The folder shared with the `mesh` container, or None where there is none (not Docker)."""
    d = os.environ.get("LENS_MESH_DIR")
    return pathlib.Path(d) if d else None


def _write(path, text):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    tmp.chmod(0o644)
    tmp.replace(path)


def service(cfg=None):
    """How the `mesh` container says it runs (status.json): {} when there's no container or it hasn't said;
    running False when what it said is old (it stopped)."""
    d = shared_dir()
    if not d:
        return {}
    try:
        s = json.loads((d / "status.json").read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(s, dict):
        return {}
    try:
        age = (_now() - dt.datetime.fromisoformat(str(s.get("at")).replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        age = STATUS_STALE + 1
    s["fresh"] = age <= STATUS_STALE
    if not s["fresh"]:
        s["nebula"] = False
    return s


def _parse_time(value):
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def lens_file(db, cfg, public_key=None):
    """What goes in lens.json now: off, on and waiting for the container's key, or on with the primary's config
    (its certificate signed again when the key is new or a third of its life is left)."""
    c = conf(cfg)
    if not c["enabled"]:
        return {"on": False}
    row = primary(db, cfg)
    out = {"on": True, "ip": row["ip"], "public_key": None, "config": None, "version": None}
    if not public_key:
        return out
    have = db.one("SELECT crt, public_key, not_after FROM $r", r=R("mesh_node", row["id"])) or {}
    ends = _parse_time(have.get("not_after"))
    if not have.get("crt") or have.get("public_key") != public_key or not ends or ends - _now() < dt.timedelta(days=renew_days(cfg)):
        have = primary(db, cfg, public_key=public_key)
    config = render(db, cfg, row, have["crt"])
    return {**out, "public_key": public_key, "config": config, "version": version(config)}


class Keeper:
    """Keeps lens.json as the mesh is, the primary's certificate signed, and the container started or stopped."""

    def __init__(self, db, cfg_fn, name, log=None):
        self.db, self.cfg_fn, self.name, self.log = db, cfg_fn, name, log or (lambda *a: None)
        self.last_beat = 0.0
        self.error = None

    def _lease(self):
        now = _now()
        return bool(
            self.db.rows(
                "UPSERT $r MERGE {owner: $me, at: $t} WHERE owner = NONE OR owner = $me OR at < $stale RETURN AFTER",
                r=KEEPER_ROW,
                me=self.name,
                t=_ts(now),
                stale=_ts(now - dt.timedelta(seconds=LEASE_SECONDS)),
            )
        )

    def _profile(self, on):
        """Ask the upgrade agent to start (or stop) the container, once for each turn of mesh.enabled, so an admin
        who stops it in Settings › Services isn't overruled."""
        from . import services

        row = self.db.one("SELECT asked_for FROM $r", r=KEEPER_ROW) or {}
        if row.get("asked_for") is on or (row.get("asked_for") is None and not on):
            return
        try:
            services.ask(self.db, SERVICE, on, by="lens")
        except services.ServiceError as e:
            if "upgrade agent" not in str(e):
                return  # busy: next tick
            self.log("mesh: %s", e)
        self.db.q("UPDATE $r SET asked_for = $o", r=KEEPER_ROW, o=on)

    def tick(self):
        d = shared_dir()
        if not d or not self._lease():
            return
        cfg = self.cfg_fn()
        on = conf(cfg)["enabled"]
        self._profile(on)
        pub = None
        try:
            text = (d / "host.pub").read_text()
            pub = text if PUBLIC_KEY_RX.fullmatch(text.replace("\r\n", "\n")) else None
        except OSError:
            pass
        want = lens_file(self.db, cfg, pub if on else None)
        text = json.dumps(want, indent=1, sort_keys=True)
        try:
            same = (d / "lens.json").read_text() == text
        except OSError:
            same = False
        if not same:
            _write(d / "lens.json", text)
            self.log("mesh: wrote the primary's config (%s)", "on" if on else "off")
        if on and time.monotonic() - self.last_beat > BEAT_SECONDS:
            s = service(cfg)
            row = primary(self.db, cfg)
            beat(
                self.db,
                row,
                {"nebula": bool(s.get("nebula")), "version": s.get("version"), "error": s.get("error"), "log": s.get("log") or []},
            )
            self.last_beat = time.monotonic()

    def loop(self, stop):
        while not stop.is_set():
            try:
                self.tick()
                self.error = None
            except Exception as e:  # noqa: BLE001 - keep the keeper alive; the error shows in the status
                self.error = f"{type(e).__name__}: {e}"
                try:
                    self.db.q("UPDATE $r SET error = $e WHERE owner = $me", r=KEEPER_ROW, e=self.error, me=self.name)
                except Exception:  # noqa: BLE001
                    pass
            stop.wait(KEEPER_SECONDS)
        try:
            self.db.q("UPDATE $r SET owner = NONE WHERE owner = $me", r=KEEPER_ROW, me=self.name)
        except Exception:  # noqa: BLE001
            pass


def start(db, cfg_fn, stop, name, log=None):
    """The keeper, where there is a `mesh` container to keep (LENS_MESH_DIR); None elsewhere."""
    if not shared_dir():
        return None
    k = Keeper(db, cfg_fn, name, log)
    threading.Thread(target=k.loop, args=(stop,), daemon=True, name="mesh").start()
    return k


def service_view(db, cfg):
    """The `mesh` container as Settings › Network shows it."""
    from . import services

    s = service(cfg)
    keeper = db.one("SELECT error FROM $r", r=KEEPER_ROW) or {}
    return {
        "available": bool(shared_dir()),
        "state": s.get("state") or ("off" if s else None),
        "nebula": bool(s.get("nebula")),
        "since": s.get("since"),
        "restarts": s.get("restarts") or 0,
        "relays": s.get("relays") or [],
        "error": s.get("error") or keeper.get("error"),
        "log": (s.get("log") or [])[-40:],
        "seen_at": s.get("at"),
        "manual": services.manual(SERVICE),
    }


def status(db, cfg):
    """What Settings › Network shows: on or off, the network, the CA, the primary's address, the counts, and how
    Nebula runs on this machine."""
    c, row = conf(cfg), ca_row(db)
    every = nodes(db)
    return {
        "enabled": c["enabled"],
        "network": c["network"],
        "port": c["port"],
        "hosts": c["hosts"],
        "primary_ip": primary_ip(cfg),
        "ca": {"fingerprint": row["fingerprint"], "not_after": row["not_after"], "created_at": row.get("created_at")} if row else None,
        "nodes": sum(1 for n in every if n["role"] != "primary" and not n["revoked_at"]),
        "removed": sum(1 for n in every if n["revoked_at"]),
        "tokens": len(tokens(db)),
        "nebula": NEBULA_VERSION,
        "tools": bool(binaries(cfg)),
        "service": service_view(db, cfg),
    }
