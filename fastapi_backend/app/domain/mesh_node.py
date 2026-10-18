"""`lens node`: this machine's side of another install's mesh (docs/mesh.md, Nodes).

The node's install line (install.sh with LENS_JOIN and LENS_JOIN_TOKEN) starts two containers: `mesh` runs Nebula
(mesh/sidecar.py, the same as on the primary) and `node` runs this, in the mesh container's network, with the folder
the two share. It needs no database and no settings of its own:

1. **Joining, once.** It turns the shared folder's lens.json on, so the container makes this machine's key pair and
   publishes the public key; then it joins at the install's address with the token (mesh.join: the token never crosses
   the wire), opens the sealed answer, and keeps what it holds in node.json, which only it reads. A refused token (used,
   expired, withdrawn) is not tried again: the install line is run again with a new one.
2. **After that, over the mesh only.** It writes the config it was given into lens.json, which the container runs, and
   every minute asks the primary for its config as it is now (a removed node's certificate on the blocklist), renews
   its certificate when a third of its life is left, and reports how it runs (mesh.beat). A node the admin removed is
   refused; it keeps asking, so an undo brings it back.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import platform
import re
import secrets
import socket
import time

import httpx

from . import mesh, updates

BEAT_SECONDS = 60
TICK = 2.0
RETRY = (5, 15, 30, 60, 120)


def say(msg):
    print(f"lens node: {msg}", flush=True)


def _now():
    return dt.datetime.now(dt.timezone.utc)


def machine_name(raw=None):
    """A name the mesh takes (mesh.NAME_RX) from this machine's: what isn't a letter, digit, space, dot, dash or
    underscore becomes a dash."""
    name = re.sub(r"[^A-Za-z0-9 ._-]", "-", (raw or socket.gethostname() or "node").strip())[:63]
    name = re.sub(r"^[^A-Za-z0-9]+", "", name)
    return name or "node"


def machine():
    """What the Nodes page shows about this machine."""
    mem = None
    try:
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                mem = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    if mem is None:
        try:
            mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError, AttributeError):
            pass
    return {"os": platform.system(), "arch": platform.machine(), "cpus": os.cpu_count(), "memory": mem}


def _write(path, text, mode=0o600):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    tmp.chmod(mode)
    tmp.replace(path)


class Refused(Exception):
    """The primary said no for good: a join token that's used, expired or withdrawn."""


class Agent:
    """Joins once, then keeps lens.json as the primary says and reports every minute (the module's docstring)."""

    def __init__(self, state, shared, join_url=None, token=None, name=None, http=None):
        self.state, self.shared = pathlib.Path(state), pathlib.Path(shared)
        self.join_url = (join_url or "").strip().rstrip("/") or None
        self.token = (token or "").strip() or None
        self.name = machine_name(name)
        self.http = http or httpx.Client(timeout=20, follow_redirects=False)
        self.node = None
        self.error = None
        self.removed = False
        self.refused = None
        self.fails = 0
        self.next_try = 0.0
        self.last_beat = 0.0
        self._reported = None

    # ---------- what's kept ----------
    @property
    def path(self):
        return self.state / "node.json"

    def load(self):
        if self.node is None:
            try:
                self.node = json.loads(self.path.read_text())
            except (OSError, ValueError):
                self.node = None
        return self.node

    def save(self):
        self.state.mkdir(parents=True, exist_ok=True)
        _write(self.path, json.dumps(self.node, indent=1, sort_keys=True))

    def api(self, path):
        n = self.node
        return f"http://{n['primary']['ip']}:{n['primary']['api_port']}/api/v1/mesh{path}"

    def _headers(self):
        return {"Authorization": f"Bearer {self.node['node_token']}"}

    # ---------- the shared folder ----------
    def public_key(self):
        try:
            text = (self.shared / "host.pub").read_text()
        except OSError:
            return None
        return text if mesh.PUBLIC_KEY_RX.fullmatch(text.replace("\r\n", "\n")) else None

    def lens_file(self, pub=None):
        """What lens.json says: on and waiting for a key before joining, then on with this node's config."""
        n = self.node
        if not n:
            return {"on": True, "ip": None, "public_key": None, "config": None, "version": None}
        return {"on": True, "ip": n["node"]["ip"], "public_key": pub, "config": n["config"], "version": n["version"]}

    def publish(self, pub=None):
        text = json.dumps(self.lens_file(pub), indent=1, sort_keys=True)
        path = self.shared / "lens.json"
        try:
            if path.read_text() == text:
                return False
        except OSError:
            pass
        _write(path, text, 0o644)  # the mesh container runs as another user
        return True

    def service(self):
        """How the mesh container says Nebula runs (its status.json), {} when it hasn't said."""
        try:
            s = json.loads((self.shared / "status.json").read_text())
        except (OSError, ValueError):
            return {}
        return s if isinstance(s, dict) else {}

    # ---------- the calls ----------
    def join(self, pub):
        if not self.join_url:
            raise Refused("there's no install to join: run the install line with LENS_JOIN and LENS_JOIN_TOKEN")
        try:
            tid, secret = mesh.parse_token(self.token)
        except ValueError as e:
            raise Refused(f"{e}: copy the install line from Settings › Nodes again") from None
        nonce = secrets.token_urlsafe(24)
        body = {
            "token_id": tid,
            "nonce": nonce,
            "name": self.name,
            "public_key": pub,
            "proof": mesh.proof(secret, tid, nonce, self.name, pub),
            "info": machine(),
        }
        r = self.http.post(f"{self.join_url}/api/v1/mesh/join", json=body)
        if r.status_code in (400, 403):
            raise Refused(_detail(r))
        r.raise_for_status()
        try:
            answer = mesh.open_answer(secret, nonce, tid, r.json()["sealed"])
        except ValueError as e:
            raise Refused(str(e)) from None
        self.node = {**answer, "public_key": pub, "joined_at": _now().isoformat(), "join_url": self.join_url}
        self.save()
        n = answer["node"]
        say(f"joined the mesh as {n['name']} ({n['role']}, {n['ip']}), node {n['id']}")

    def follow(self, pub):
        """The config as the primary has it now, a new certificate when it's due, and this machine's report."""
        r = self.http.get(self.api("/node/config"), headers=self._headers())
        if r.status_code == 401:
            if not self.removed:
                say("this machine was removed from the mesh in Settings › Nodes: waiting in case that's undone")
            self.removed = True
            return
        r.raise_for_status()
        if self.removed:
            say("this machine is back on the mesh")
        self.removed = False
        got = r.json()
        if pub and (_due(got.get("not_after"), got.get("renew_days")) or pub != self.node.get("public_key")):
            r = self.http.post(self.api("/node/renew"), json={"public_key": pub}, headers=self._headers())
            r.raise_for_status()
            got = r.json()
            self.node["public_key"] = pub
            say(f"renewed its certificate (good until {got.get('not_after')})")
        if got["version"] != self.node.get("version") or got.get("not_after") != self.node.get("not_after"):
            self.node.update(config=got["config"], version=got["version"], not_after=got.get("not_after"), renew_days=got.get("renew_days"))
            self.save()
        s = self.service()
        report = {
            "nebula": s.get("state") or ("running" if s.get("nebula") else "stopped"),
            "version": updates.current(),
            "machine": machine(),
            "error": str(s["error"])[:500] if s.get("error") else None,  # this call reached the primary: its own are past
            "log": [str(x) for x in (s.get("log") or [])][-40:],
        }
        self.http.post(self.api("/node/beat"), json={k: v for k, v in report.items() if v is not None}, headers=self._headers())

    # ---------- the loop ----------
    def tick(self):
        if self.refused or time.monotonic() < self.next_try:
            return
        try:
            self.shared.mkdir(parents=True, exist_ok=True)
            pub = self.public_key()
            if not self.load():
                self.publish()  # the container makes the key pair once lens.json is on
                if not pub:
                    self.error = "waiting for the mesh container's key"
                    return
                self.join(pub)
            self.publish(self.node.get("public_key"))
            if self.service().get("state") != "running":  # the primary is reached over the mesh only
                self.error = "waiting for Nebula to start"
                return
            if time.monotonic() - self.last_beat >= BEAT_SECONDS:
                self.last_beat = time.monotonic()
                self.follow(pub)
                self.publish(self.node.get("public_key"))
            self.error, self.fails = None, 0
        except Refused as e:
            self.refused = str(e)
            say(f"couldn't join: {e}")
        except (httpx.HTTPError, OSError, ValueError, KeyError) as e:
            self.error = f"{type(e).__name__}: {e}"
            wait = RETRY[min(self.fails, len(RETRY) - 1)]
            self.fails += 1
            self.next_try = time.monotonic() + wait
            self.last_beat = 0.0
            say(f"{self.error} (trying again in {wait} s)")

    def report(self):
        """agent.json: how this agent is doing, for `lens node status` (another process) and the install line."""
        now = {"refused": self.refused, "error": self.error, "removed": self.removed, "joined": bool(self.node)}
        if now != self._reported:
            self.state.mkdir(parents=True, exist_ok=True)
            _write(self.state / "agent.json", json.dumps(now))
            self._reported = now

    def loop(self, stop):
        while not stop.is_set():
            self.tick()
            try:
                self.report()
            except OSError as e:
                say(f"can't write agent.json: {e}")
            stop.wait(TICK)

    def view(self):
        """`lens node status`: what this machine is on the mesh, without its credentials."""
        try:
            seen = json.loads((self.state / "agent.json").read_text())
        except (OSError, ValueError):
            seen = {}
        n = self.load()
        if not n:
            return {"joined": False, "refused": seen.get("refused"), "error": seen.get("error")}
        return {
            "joined": True,
            "node": n["node"],
            "primary": n["primary"],
            "not_after": n.get("not_after"),
            "joined_at": n.get("joined_at"),
            "join_url": n.get("join_url"),
            "removed": bool(seen.get("removed")),
            "error": seen.get("error"),
            "nebula": self.service().get("state"),
        }


def _detail(r):
    try:
        return str(r.json().get("detail") or r.text)
    except ValueError:
        return r.text or f"HTTP {r.status_code}"


def _due(not_after, days):
    if not not_after:
        return False
    try:
        ends = dt.datetime.fromisoformat(str(not_after).replace("Z", "+00:00"))
    except ValueError:
        return False
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=dt.timezone.utc)
    return ends - _now() < dt.timedelta(days=int(days or 1))


def agent_from_env(http=None):
    return Agent(
        os.environ.get("LENS_NODE_DIR") or "/data/node",
        os.environ.get("LENS_MESH_DIR") or "/mesh",
        os.environ.get("LENS_JOIN"),
        os.environ.get("LENS_JOIN_TOKEN"),
        os.environ.get("LENS_NODE_NAME") or os.environ.get("LENS_SCALING_UNIT"),
        http,
    )


def main(argv=None):
    import argparse
    import signal
    import threading

    ap = argparse.ArgumentParser(prog="lens node", description="this machine's side of another Lens install's mesh (docs/mesh.md)")
    ap.add_argument("action", nargs="?", default="run", choices=["run", "status"])
    a = ap.parse_args(argv)
    agent = agent_from_env()
    if a.action == "status":
        print(json.dumps(agent.view(), indent=1, ensure_ascii=False))
        return
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    say(f"{'on the mesh as ' + agent.node['node']['name'] if agent.load() else 'joining ' + str(agent.join_url)}")
    try:
        agent.loop(stop)
    except KeyboardInterrupt:
        pass
