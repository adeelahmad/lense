"""The providers auto scaling launches units on (scaling.py, docs/scaling.md): what each one asks for, and the calls
that launch, look at and terminate a unit there.

A driver is the code for one kind of provider. It names the settings an admin fills in for a provider of its kind
(`fields`), which of them are credentials (`secrets`, sealed by scaling.py and handed to the driver opened, only for
a call), and what a scaling group picks for each unit (`template`: a size, an image, the steps a worker runs). A
driver that charges money (`priced`) needs a budget on every group that uses it.

The calls get the provider as `p` ({id, name, region, settings, secrets}) and a unit as `node` (its scale_node row):

* check(p): a read-only call that proves the credentials work (listing regions, say); raises DriverError.
* price(p, template): the list price in USD per unit-hour, when the driver knows it; None otherwise.
* launch(p, template, name, boot): start one unit named `name`. `boot` is how a node joins Lens (scaling.bootstrap:
  a script and its environment). Returns {"id": the provider's id for it, "address"?, "endpoint"?, "price"?}.
* describe(p, node): where it stands: {"state": "pending" | "running" | "gone", "address"?, "endpoint"?}.
* terminate(p, node): stop it, and delete what was made for it. Terminating one that's already gone is no error.
* listing(p, tag): the units the provider has that carry Lens's tag, [{"id", "name"}], to find ones Lens lost.

Drivers that need a library name it as `extra` (an optional dependency in pyproject.toml): it's fetched the first
time a provider of that kind is saved (components.pip_install), so the default install carries none of them.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading

from . import jobs


class DriverError(RuntimeError):
    """What a provider said when a call failed, for the person to read."""


FIELD_TYPES = ("string", "integer", "number", "boolean", "list")


def _field(label, type_="string", required=False, default=None, help=None, options=None, secret=False):
    return {
        "label": label,
        "type": type_,
        "required": required,
        "default": default,
        "help": help,
        "options": options,
        "secret": secret,
    }


class Driver:
    id = ""
    kind = "node"  # node: a machine that runs `lens worker`; model: an OpenAI-compatible model endpoint
    label = ""
    about = ""
    priced = True  # it charges money: its groups need a budget
    extra = None  # the optional dependency (pyproject.toml) it needs, fetched when a provider of it is saved
    modules = ()  # what that extra installs, to see whether it's here
    license = None
    fields: dict = {}  # the provider's settings: {name: _field(...)}; secret ones are sealed
    template: dict = {}  # what a group picks for each unit

    @property
    def secrets(self):
        return tuple(k for k, f in self.fields.items() if f.get("secret"))

    def ready(self):
        from . import components

        return not self.modules or components.importable(*self.modules)

    def spec(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "about": self.about,
            "priced": self.priced,
            "extra": self.extra,
            "license": self.license,
            "ready": self.ready(),
            "fields": self.fields,
            "template": self.template,
        }

    def check(self, p):
        return {"ok": True}

    def price(self, p, template):
        return None

    def launch(self, p, template, name, boot):
        raise NotImplementedError

    def describe(self, p, node):
        raise NotImplementedError

    def terminate(self, p, node):
        raise NotImplementedError

    def listing(self, p, tag):
        return []


def _values(fields, given, what):
    """`given` checked against `fields` (types, required, options), with defaults filled in: the settings a driver
    keeps. ValueError naming the first problem."""
    given = dict(given or {})
    unknown = sorted(set(given) - set(fields))
    if unknown:
        raise ValueError(f"{what} has no {', '.join(unknown)}; it takes {', '.join(fields) or 'nothing'}")
    out = {}
    for k, f in fields.items():
        if f.get("secret"):
            continue
        v = given.get(k, f.get("default"))
        if v in (None, ""):
            if f.get("required"):
                raise ValueError(f"{what}: {f['label']} ({k}) is needed")
            continue
        t = f["type"]
        if t == "string":
            if not isinstance(v, str) or len(v) > 2000:
                raise ValueError(f"{what}: {k} is text")
            v = v.strip()
        elif t == "integer":
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError(f"{what}: {k} is a whole number")
        elif t == "number":
            if isinstance(v, bool) or not isinstance(v, int | float):
                raise ValueError(f"{what}: {k} is a number")
        elif t == "boolean":
            if not isinstance(v, bool):
                raise ValueError(f"{what}: {k} is true or false")
        elif t == "list":
            if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v) or len(v) > 100:
                raise ValueError(f"{what}: {k} is a list of names")
            v = [x.strip() for x in v]
        if f.get("options") and (v if isinstance(v, list) else [v]) and not set(v if isinstance(v, list) else [v]) <= set(f["options"]):
            raise ValueError(f"{what}: {k} is one of {', '.join(map(str, f['options']))}")
        out[k] = v
    return out


def check_settings(drv, given):
    return _values(drv.fields, given, f"a {drv.label} provider")


def check_template(drv, given):
    return _values(drv.template, given, f"a {drv.label} unit")


# ---------- this machine ----------
def _die_with_parent():
    """In the child, before it runs: be stopped when the process that started it ends (Linux), so a worker this
    machine's scaler started never outlives it."""
    try:
        import ctypes

        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


class Local(Driver):
    """More `lens worker` processes on the machine the scaler runs on: when a big machine's one worker can't keep up,
    more of them take jobs at once, and they stop again when the queue is empty. Nothing to sign in to and no cost.
    Each one stops with the process that started it; the scaler that takes over starts them again where it runs."""

    id = "local"
    label = "This machine"
    about = "Runs more Lens workers on the machine Lens runs on, so more jobs run at once. Costs nothing."
    priced = False
    fields = {}
    template = {
        "steps": _field(
            "Steps",
            "list",
            help="the steps these workers run (transcribe, analyze, embed...); none: every step",
            options=sorted(jobs.STEPS),
        )
    }

    def __init__(self):
        self.procs: dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()

    def host(self):
        return socket.gethostname()

    def command(self, name, template):
        cmd = [sys.executable, "-c", "import sys; from app.cli import main; sys.exit(main())", "worker", "--name", name, "--no-schedule"]
        if template.get("steps"):
            cmd += ["--steps", ",".join(template["steps"])]
        return cmd

    def launch(self, p, template, name, boot):
        env = {**os.environ, **(boot.get("env") or {})}
        try:
            proc = subprocess.Popen(
                self.command(name, template),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=_die_with_parent if sys.platform.startswith("linux") else None,
            )
        except OSError as e:
            raise DriverError(f"couldn't start a worker here: {e}") from None
        with self.lock:
            self.procs[name] = proc
        return {"id": f"{self.host()}:{proc.pid}", "price": 0.0}

    def _proc(self, node):
        with self.lock:
            return self.procs.get(node.get("name"))

    def describe(self, p, node):
        host, _, pid = (node.get("external_id") or "").rpartition(":")
        if host != self.host():
            return {"state": "gone"}  # started by a scaler on another machine, which stopped it when it ended
        proc = self._proc(node)
        if proc is not None:
            return {"state": "running" if proc.poll() is None else "gone"}
        try:
            os.kill(int(pid), 0)
        except (OSError, ValueError):
            return {"state": "gone"}
        return {"state": "running"}

    def terminate(self, p, node):
        host, _, pid = (node.get("external_id") or "").rpartition(":")
        proc = self._proc(node)
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(5)
            with self.lock:
                self.procs.pop(node.get("name"), None)
            return
        if host == self.host() and pid.isdigit():
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass


DRIVERS: dict[str, Driver] = {}


def register(drv):
    DRIVERS[drv.id] = drv
    return drv


def get(driver_id):
    try:
        return DRIVERS[driver_id]
    except KeyError:
        raise ValueError(f"there's no provider kind {driver_id}; there are {', '.join(sorted(DRIVERS))}") from None


register(Local())

from . import scaling_aws  # noqa: E402,F401 - AWS EC2 registers itself
