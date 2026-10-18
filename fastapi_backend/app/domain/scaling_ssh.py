"""Linux machines you have, reached over SSH, as an auto scaling provider (scaling_drivers.py, docs/scaling.md): each
unit is a `lens worker` started on one of the provider's hosts, which joins Lens over the mesh; letting it go stops it.
Nothing is created or deleted, so it costs nothing unless you give the provider a price.

On each host a unit is a few files in `~/.lens-scaling/`: `<name>.pid` (its process group, started with setsid) and
`<name>.log`. The first-boot script (scaling_drivers.user_data) is written there with only the user able to read it,
and the unit deletes it as it starts, since it holds the unit's join token.

Host keys are trusted on first use, when the provider is saved or checked: the check records each host's key on the
provider (`check.host_keys`, kept when a later check fails), and every other call refuses a host whose key isn't the
one recorded. A host whose key changed for a good reason is taken off the provider and added back.

It uses Paramiko (LGPL-2.1, unmodified), the `ssh` extra, fetched the first time an SSH provider is saved.
"""

from __future__ import annotations

import base64
import io
import re
import shlex

from . import jobs
from .scaling_drivers import Driver, DriverError, _field, register, user_data

TIMEOUT = 20.0
DIR = ".lens-scaling"
HOST_RX = re.compile(r"^(?P<user>[a-z_][a-z0-9_.-]{0,31})@(?P<host>\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.-]{1,253})(?::(?P<port>\d{1,5}))?$")


def parse_host(spec):
    """`user@host`, `user@host:port` or `user@[v6]:port` as (user, host, port); ValueError otherwise."""
    m = HOST_RX.match(spec or "")
    if not m or not 0 < int(m["port"] or 22) < 65536:
        raise ValueError(f"an SSH host is user@host or user@host:port, not {spec!r}")
    return m["user"], m["host"].strip("[]"), int(m["port"] or 22)


def known_name(host, port):
    """How known_hosts names a host: `host`, or `[host]:port` off port 22."""
    return host if port == 22 else f"[{host}]:{port}"


class Ssh(Driver):
    id = "ssh"
    label = "Linux hosts over SSH"
    about = "Starts Lens workers on Linux machines you have, over SSH; they join Lens over the mesh. Costs nothing."
    priced = False
    needs_join = True
    delete_gone = False  # a worker that ended leaves only its log, and its process group may be another's now
    extra = "ssh"
    modules = ("paramiko",)
    license = "LGPL-2.1 (Paramiko)"
    kept = ("host_keys",)
    fields = {
        "hosts": _field("Hosts", "list", required=True, help="user@host or user@host:port, one per machine"),
        "private_key": _field("Private key", required=True, secret=True, help="an OpenSSH or PEM private key those users accept"),
        "passphrase": _field("Key passphrase", secret=True, help="when the key has one"),
    }
    template = {
        "per_host": _field("Workers on each host", "integer", default=1, help="how many of the group's units a host runs at once"),
        "steps": _field(
            "Steps",
            "list",
            help="the steps these workers run (transcribe, analyze, embed...); none: every step",
            options=sorted(jobs.STEPS),
        ),
    }

    # ---------- SSH ----------
    def _key(self, p):
        import paramiko

        text, phrase = p["secrets"].get("private_key") or "", p["secrets"].get("passphrase") or None
        for kind in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
            try:
                return kind.from_private_key(io.StringIO(text), password=phrase)
            except paramiko.PasswordRequiredException:
                raise DriverError("the private key needs its passphrase") from None
            except (paramiko.SSHException, ValueError):
                continue
        raise DriverError("the private key can't be read: give an OpenSSH or PEM private key")

    def _known(self, p):
        return dict(((p.get("check") or {}).get("host_keys")) or {})

    def run(self, p, spec, command, stdin=None, learn=None):
        """Run `command` on the host `spec` and return (exit code, stdout). With `learn` (a dict), a host with no
        recorded key is trusted and its key recorded there; without it, only the recorded key is accepted."""
        import paramiko

        user, host, port = parse_host(spec)
        name = known_name(host, port)
        known = self._known(p)
        client = paramiko.SSHClient()
        if known.get(spec):
            kind, _, b64 = known[spec].partition(" ")
            client.get_host_keys().add(name, kind, paramiko.PKey.from_type_string(kind, base64.b64decode(b64)))

        class Learn(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, c, hostname, key):
                if learn is None:
                    raise DriverError(f"{spec}'s host key isn't one Lens has seen: check the provider to trust it")
                learn[spec] = f"{key.get_name()} {key.get_base64()}"

        client.set_missing_host_key_policy(Learn())
        try:
            client.connect(
                host,
                port=port,
                username=user,
                pkey=self._key(p),
                timeout=TIMEOUT,
                auth_timeout=TIMEOUT,
                banner_timeout=TIMEOUT,
                allow_agent=False,
                look_for_keys=False,
            )
            if learn is not None and known.get(spec):
                learn[spec] = known[spec]
            stdin_, stdout, _ = client.exec_command(command, timeout=TIMEOUT)
            if stdin is not None:
                stdin_.write(stdin)
                stdin_.channel.shutdown_write()
            out = stdout.read().decode(errors="replace")
            return stdout.channel.recv_exit_status(), out
        except paramiko.BadHostKeyException:
            raise DriverError(f"{spec}'s host key changed: take the host off the provider and add it back if that's expected") from None
        except paramiko.AuthenticationException:
            raise DriverError(f"{spec} refused the key") from None
        except (OSError, paramiko.SSHException) as e:
            raise DriverError(f"couldn't reach {spec}: {type(e).__name__}: {e}"[:300]) from None
        finally:
            client.close()

    # ---------- the driver ----------
    def check(self, p):
        seen: dict[str, str] = {}
        for spec in p["settings"]["hosts"]:
            code, out = self.run(p, spec, "uname -sm", learn=seen)
            if code != 0 or not out.startswith("Linux"):
                raise DriverError(f"{spec} isn't a Linux machine Lens can run on ({out.strip() or code})")
        return {"hosts": len(seen), "host_keys": seen}

    def _units(self, p, spec):
        code, out = self.run(
            p,
            spec,
            f'cd ~/{DIR} 2>/dev/null && for f in *.pid; do [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null && echo "${{f%.pid}}"; done; true',
        )
        return [x for x in out.split() if x]

    def launch(self, p, template, name, boot):
        per = template.get("per_host") or 1
        for spec in p["settings"]["hosts"]:
            if len(self._units(p, spec)) >= per:
                continue
            q = shlex.quote(name)
            self.run(p, spec, f"umask 077 && mkdir -p ~/{DIR} && cat > ~/{DIR}/{q}.sh", stdin=user_data(boot, template))
            start = (
                f'cd ~/{DIR} && nohup setsid sh -c \'f="$1"; s=$(cat "$f"); rm -f "$f"; exec sh -c "$s"\' sh {q}.sh'
                f" > {q}.log 2>&1 < /dev/null & echo $! > ~/{DIR}/{q}.pid"
            )
            code, _ = self.run(p, spec, start)
            if code != 0:
                raise DriverError(f"couldn't start a worker on {spec} (exit {code})")
            return {"id": f"{spec}#{name}"}
        raise DriverError(f"every host runs its {per} worker(s): add a host or lower the group's max")

    def _where(self, node):
        spec, _, name = (node.get("external_id") or "").partition("#")
        return spec, name

    def describe(self, p, node):
        spec, name = self._where(node)
        if not name:
            return {"state": "gone"}
        return {"state": "running" if name in self._units(p, spec) else "gone"}

    def terminate(self, p, node):
        spec, name = self._where(node)
        if not name or spec not in p["settings"]["hosts"]:
            return
        q = shlex.quote(name)
        self.run(
            p,
            spec,
            f'cd ~/{DIR} 2>/dev/null || exit 0; if [ -f {q}.pid ]; then g=$(cat {q}.pid); kill -TERM -- -"$g" 2>/dev/null;'
            f' for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$g" 2>/dev/null || break; sleep 1; done;'
            f' kill -KILL -- -"$g" 2>/dev/null; fi; rm -f {q}.pid {q}.sh {q}.log; true',
        )

    def listing(self, p, tag):
        return [
            {"id": f"{spec}#{n}", "name": n} for spec in p["settings"]["hosts"] for n in self._units(p, spec) if n.startswith(f"{tag}-")
        ]


register(Ssh())
