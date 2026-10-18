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
3. **A worker node runs jobs.** On a worker node it also runs `lens worker` (Worker), signed in to the primary's
   database through the mesh as the node's own user of it (mesh.worker_access), started again when it stops or that
   user's password changes, and stopped while the node is removed. The worker's jobs read their files from the primary
   over the mesh (media()).
4. **A compute node runs sandboxes.** Its install line starts an OpenShell gateway beside it (docker-compose.node.yml,
   profile `sandbox`). It hands the primary that gateway's client certificate once (Gateway, mesh.sandbox_gateway) and
   reports the gateway in each beat while it answers, so the assistant can run code here; the primary reaches the
   gateway through the mesh container's relay, never anything else on this machine.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import platform
import re
import secrets
import shutil
import socket
import subprocess
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
        self.worker = Worker(self)
        self.gateway = Gateway(self)

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
            self.worker.stop()
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
        role = self.node["node"]["role"]
        if role == "worker":
            self.worker.sync()
        sandbox = self.gateway.sync() if role == "compute" else None
        s = self.service()
        mine = self.worker.error or self.gateway.error
        report = {
            "nebula": s.get("state") or ("running" if s.get("nebula") else "stopped"),
            "version": updates.current(),
            "machine": machine(),
            "sandbox": sandbox,
            # this call reached the primary: its own errors are past; the mesh container's, the worker's and the
            # gateway's aren't
            "error": (str(s["error"]) if s.get("error") else mine or "")[:500] or None,
            "log": ([str(x) for x in (s.get("log") or [])] + self.worker.log + self.gateway.log)[-40:],
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
        try:
            while not stop.is_set():
                self.tick()
                try:
                    self.report()
                except OSError as e:
                    say(f"can't write agent.json: {e}")
                stop.wait(TICK)
        finally:
            self.worker.stop()

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


class Worker:
    """`lens worker` on a worker node (the module's docstring, 3): Agent.follow syncs it every minute."""

    RESTART = (5, 30, 120)

    def __init__(self, agent, popen=subprocess.Popen):
        self.agent, self.popen = agent, popen
        self.proc = None
        self.key = None
        self.error = None
        self.log = []
        self.starts = 0
        self.not_before = 0.0

    def _say(self, msg):
        say(msg)
        self.log = (self.log + [f"{_now().strftime('%H:%M:%S')} {msg}"])[-10:]

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def sync(self):
        a = self.agent
        r = a.http.get(a.api("/node/worker"), headers=a._headers())
        if r.status_code in (403, 409):
            self.error = _detail(r)
            self.stop()
            return
        r.raise_for_status()
        access = r.json()
        key = (access["url"], access["user"], access["password"])
        if self.running() and key == self.key:
            self.error = None
            return
        if self.proc is not None and not self.running():
            code = self.proc.returncode
            self.proc = None
            self.error = f"lens worker stopped (exit {code})"
            self._say(self.error)
            self.not_before = time.monotonic() + self.RESTART[min(self.starts - 1, len(self.RESTART) - 1)]
        self.stop()
        if time.monotonic() < self.not_before:
            return
        self.start(access, key)

    def start(self, access, key):
        n = self.agent.node["node"]
        allowed = list(access["steps"])
        asked = [s.strip() for s in (os.environ.get("LENS_WORKER_STEPS") or "").split(",") if s.strip()]
        # only the steps a node can run (it has no archive key): the rest would fail jobs the primary's worker takes
        picked = [s for s in asked if s in allowed] if asked else allowed
        if not picked:
            msg = f"none of LENS_WORKER_STEPS run on a node; it runs {', '.join(allowed)}"
            if msg != self.error:  # said once, not every minute
                self._say(msg)
            self.error = msg
            return
        if len(picked) < len(asked):
            self._say(f"skipping {', '.join(s for s in asked if s not in allowed)}: a node runs {', '.join(allowed)}")
        steps = ",".join(picked)
        name = machine_name(os.environ.get("LENS_SCALING_UNIT") or n["name"])
        env = {k: v for k, v in os.environ.items() if k not in ("LENS_JOIN_TOKEN", "ARCHIVE_SECRET_KEY")}
        env.update(
            SURREAL_URL=access["url"],
            SURREAL_USER=access["user"],
            SURREAL_PASS=access["password"],
            SURREAL_NS=access["namespace"],
            SURREAL_DB=access["database"],
            SURREAL_AUTH="database",
            LENS_MESH_NODE=str(n["id"]),
        )
        cmd = [shutil.which("lens") or "lens", "worker", "--name", name, "--steps", steps, "--no-schedule"]
        self.proc = self.popen(cmd, env=env)
        self.key = key
        self.starts += 1
        self.error = None
        self._say(f"started lens worker {name} ({steps})")

    def stop(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self._say("stopped lens worker")
        self.proc = None
        self.key = None


class Gateway:
    """A compute node's sandbox gateway (the module's docstring, 4): Agent.follow syncs it every minute. Hands the
    primary the gateway's client certificate when it's new, and says whether the gateway answers."""

    FILES = {"ca": "ca.crt", "cert": "tls.crt", "key": "tls.key"}

    def __init__(self, agent, connect=socket.create_connection):
        self.agent, self.connect = agent, connect
        self.error = None
        self.log = []

    def _say(self, msg):
        say(msg)
        self.log = (self.log + [f"{_now().strftime('%H:%M:%S')} {msg}"])[-10:]

    def _fail(self, msg):
        if msg != self.error:  # said once, not every minute
            self._say(msg)
        self.error = msg

    def bundle(self):
        """The gateway's client certificate as openshell-key left it, {ca, cert, key}; None before it has."""
        d = pathlib.Path(os.environ.get("LENS_SANDBOX_TLS_DIR") or "/sandbox-key")
        try:
            return {k: (d / f).read_text() for k, f in self.FILES.items()}
        except OSError:
            return None

    def sync(self):
        """The report's `sandbox`: {gateway: "running"} while the gateway answers, None otherwise (error says why)."""
        a = self.agent
        found = self.bundle()
        if not found:
            self._fail("this compute node runs no sandbox gateway yet: its install line starts it (LENS_NODE_SANDBOX=1)")
            return None
        digest = hashlib.sha256(json.dumps(found, sort_keys=True).encode()).hexdigest()
        if a.node.get("sandbox_sent") != digest:
            r = a.http.put(a.api("/node/sandbox"), json=found, headers=a._headers())
            if r.status_code in (400, 403):
                self._fail(f"the install didn't take the gateway's certificate: {_detail(r)}")
                return None
            r.raise_for_status()
            a.node["sandbox_sent"] = digest
            a.save()
            self._say("gave the install the sandbox gateway's certificate")
        host, _, port = (os.environ.get("LENS_NODE_GATEWAY") or "openshell-gateway:17670").rpartition(":")
        try:
            self.connect((host, int(port)), timeout=3).close()
        except (OSError, ValueError) as e:
            self._fail(f"the sandbox gateway isn't answering at {host}:{port}: {getattr(e, 'strerror', None) or e}")
            return None
        if self.error:
            self._say("the sandbox gateway answers")
        self.error = None
        return {"gateway": "running"}


# ---------- in a worker node's `lens worker`: the files its jobs read ----------
MEDIA_HOURS = 6


def on_worker_node():
    """Whether this process is a worker node's `lens worker` (Worker started it)."""
    return os.environ.get("LENS_MESH_NODE", "").isdigit() and bool(os.environ.get("LENS_NODE_DIR"))


def media(cfg, rec, http=None):
    """A local copy of recording `rec`'s file, fetched from the primary over the mesh (mesh.media) and kept in
    data_dir/cache/mesh, fetched again only when it changed there, and removed MEDIA_HOURS after it was last used."""
    node = json.loads((pathlib.Path(os.environ["LENS_NODE_DIR"]) / "node.json").read_text())
    folder = pathlib.Path(cfg["data_dir"]) / "cache" / "mesh"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    _sweep(folder)
    rid = int(rec["id"])
    dest = folder / f"{rid}{pathlib.PurePosixPath(rec.get('path') or (rec.get('remote') or {}).get('path') or '').suffix.lower()}"
    tag_file = dest.with_name(dest.name + ".etag")
    headers = {"Authorization": f"Bearer {node['node_token']}"}
    if dest.exists() and tag_file.exists():
        headers["If-None-Match"] = tag_file.read_text().strip()
    url = f"http://{node['primary']['ip']}:{node['primary']['api_port']}/api/v1/mesh/node/media/{rid}"
    client = http or httpx.Client(timeout=httpx.Timeout(60, read=600))
    try:
        with client.stream("GET", url, headers=headers) as r:
            if r.status_code == 304:
                os.utime(dest)
                return dest
            if r.status_code in (403, 404, 423):
                r.read()
                raise FileNotFoundError(f"the primary won't give recording {rid}'s file: {_detail(r)}")
            r.raise_for_status()
            tmp = dest.with_name(dest.name + ".part")
            with open(tmp, "wb") as f:
                os.chmod(tmp, 0o600)
                for block in r.iter_bytes(1 << 20):
                    f.write(block)
            tmp.replace(dest)
            tag_file.write_text(r.headers.get("etag") or "")
    finally:
        if http is None:
            client.close()
    return dest


def _sweep(folder):
    old = time.time() - MEDIA_HOURS * 3600
    for p in folder.iterdir():
        try:
            if p.suffix != ".etag" and p.stat().st_mtime < old:
                p.unlink()
                p.with_name(p.name + ".etag").unlink(missing_ok=True)
        except OSError:
            pass


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
