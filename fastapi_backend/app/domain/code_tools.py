"""Running a code tool's Python apart from the server.

The code runs in its own Python process, started from an empty folder, with no environment variables from the server
(so no keys, tokens or database address), limits on CPU time, memory, open files and the size of files it writes,
and a wall-clock time limit after which it's killed. It reads its arguments as JSON and gives back what `run(**args)`
returns, as JSON.

The web is shut off unless the tool says it needs it: in a network namespace of its own when this machine allows one
(`unshare -rn`, no network but loopback), and, either way, by refusing sockets to anywhere but loopback and the start
of other programs inside the process (Python's audit hooks). The audit hooks raise the bar; they aren't a wall, which is
why only admins write code tools.
"""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
import sys
import tempfile

MAX_SECONDS = 60
MAX_OUTPUT = 200_000
MEMORY = 512 * 2**20

RUNNER = r"""
import json, os, resource, sys
cpu, mem, net = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3] == "1"
HOME = os.path.realpath(os.getcwd())
LIBS = tuple({os.path.realpath(p) + os.sep for p in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix)})
resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
try:
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
except (ValueError, OSError):
    pass
resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 2**20, 50 * 2**20))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
LOOP = ("127.0.0.1", "::1", "localhost")

def guard(event, args):
    if event == "open" and not isinstance(args[0], int):
        path = os.path.realpath(os.fsdecode(args[0]))
        writing = any(c in str(args[1] or "") for c in "wax+") or (isinstance(args[2], int) and args[2] & (os.O_WRONLY | os.O_RDWR))
        if not (path == HOME or path.startswith(HOME + os.sep) or (not writing and path.startswith(LIBS))):
            raise PermissionError("a code tool reads and writes in its own folder only")
        return
    if event in ("ctypes.dlopen", "ctypes.cdata", "sys.setprofile", "sys.settrace", "sys.addaudithook"):
        raise PermissionError("a code tool can't do that")
    if event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty", "pty.spawn"):
        raise PermissionError("a code tool can't start other programs")
    if not net and event in ("socket.connect", "socket.sendto", "socket.getaddrinfo"):
        where = args[1] if event != "socket.getaddrinfo" else args[0]
        host = where[0] if isinstance(where, tuple) else where
        if host not in LOOP:
            raise PermissionError("this code tool has no network: say it needs it (network: true)")

sys.addaudithook(guard)
src = sys.stdin.readline()
args = json.loads(sys.stdin.readline())
scope = {"__name__": "lens_tool"}
exec(compile(json.loads(src), "tool.py", "exec"), scope)
if not callable(scope.get("run")):
    raise SystemExit("the code defines no run(**args) function")
out = scope["run"](**args)
sys.stdout.write("\n\x00LENS-RESULT\x00" + json.dumps(out, default=str))
"""


class CodeError(ValueError):
    pass


@functools.lru_cache(maxsize=1)
def netns():
    """Whether this machine lets a process have a network namespace of its own (`unshare -rn`)."""
    exe = shutil.which("unshare")
    if not exe:
        return None
    try:
        ok = subprocess.run([exe, "-rn", "true"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    return exe if ok else None


def check(source):
    """The code compiles and defines run (SyntaxError and ValueError name the problem)."""
    try:
        tree = compile(source, "tool.py", "exec", flags=0x400, dont_inherit=True)  # ast.PyCF_ONLY_AST
    except SyntaxError as e:
        raise CodeError(f"the code doesn't compile: {e.msg} (line {e.lineno})") from None
    if not any(getattr(n, "name", None) == "run" for n in getattr(tree, "body", [])):
        raise CodeError("the code defines a function run(**args) that the tool calls")


def run(source, args, seconds=20, network=False):
    """Run `run(**args)` from `source` in a process of its own; what it returned. CodeError says what went wrong
    (with the end of what it printed)."""
    seconds = max(1, min(int(seconds or 20), MAX_SECONDS))
    with tempfile.TemporaryDirectory(prefix="lens-tool-") as tmp:
        cmd = [sys.executable, "-I", "-S", "-B", "-c", RUNNER, str(seconds), str(MEMORY), "1" if network else "0"]
        if not network and netns():
            cmd = [netns(), "-rn", *cmd]
        env = {"PATH": "/usr/bin:/bin", "HOME": tmp, "TMPDIR": tmp, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        stdin = json.dumps(source) + "\n" + json.dumps(args) + "\n"
        try:
            p = subprocess.run(
                cmd, input=stdin, capture_output=True, text=True, cwd=tmp, env=env, timeout=seconds + 5, start_new_session=True
            )
        except subprocess.TimeoutExpired:
            raise CodeError(f"the code ran longer than {seconds} seconds and was stopped") from None
        out, err = p.stdout[-MAX_OUTPUT:], p.stderr[-2000:]
    head, sep, result = out.rpartition("\n\x00LENS-RESULT\x00")
    if p.returncode != 0 or not sep:
        why = (err.strip().splitlines() or [f"it exited with {p.returncode}"])[-1]
        if p.returncode in (-9, -24, 137):
            why = "it used more CPU time or memory than a code tool may"
        raise CodeError(f"the code failed: {why}")
    try:
        value = json.loads(result)
    except ValueError:
        raise CodeError("the code's result isn't JSON") from None
    printed = head.strip()
    return {"result": value, **({"printed": printed[-4000:]} if printed else {})}
