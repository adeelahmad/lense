#!/usr/bin/env python3
"""A stand-in for rclone, for tests: remotes are folders under $FAKE_RCLONE_ROOT, and $FAKE_RCLONE_STATE says what
Google is throttling right now: nothing (empty), "uploads" (new files: Drive's daily upload limit), "downloads", or
"all". Each throttled call is logged to $FAKE_RCLONE_STATE.log."""

import json
import os
import pathlib
import shutil
import sys

LIMIT = "2026/10/10 12:00:00 ERROR : Failed to copy: googleapi: Error 403: User rate limit exceeded., userRateLimitExceeded"
root = pathlib.Path(os.environ["FAKE_RCLONE_ROOT"])
state = pathlib.Path(os.environ["FAKE_RCLONE_STATE"]).read_text().strip()
args, i = [], 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a in ("--config", "--retries", "--low-level-retries", "--max-depth"):
        i += 2
        continue
    if not a.startswith("-"):
        args.append(a)
    i += 1
cmd, rest = args[0], args[1:]


def remote(p):
    return root / p.split(":", 1)[1].lstrip("/") if ":" in p else None


def throttled(what):
    if state == "all" or state == what:
        with open(os.environ["FAKE_RCLONE_STATE"] + ".log", "a") as log:
            log.write(f"{cmd}\n")
        print(LIMIT, file=sys.stderr)
        sys.exit(1)


if cmd == "obscure":
    print(sys.stdin.read().strip())
elif cmd == "lsjson":
    throttled("lists")
    base = remote(rest[0])
    base.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in base.rglob("*") if p.is_file()) if "-R" in sys.argv else sorted(base.iterdir())
    out = [
        {"Path": str(p.relative_to(base)), "Name": p.name, "Size": p.stat().st_size, "ModTime": "2020-01-01T00:00:00Z", "IsDir": p.is_dir()}
        for p in files
    ]
    print(json.dumps(out))
elif cmd == "copyto":
    src, dst = rest
    throttled("uploads" if remote(dst) is not None else "downloads")
    if remote(src) is not None and not remote(src).exists():
        print("ERROR : file not found", file=sys.stderr)
        sys.exit(1)
    out = remote(dst) or pathlib.Path(dst)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(remote(src) or src, out)
elif cmd == "deletefile":
    remote(rest[0]).unlink(missing_ok=True)
else:
    sys.exit(f"fake rclone: {cmd} isn't faked")
