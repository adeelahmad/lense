"""Every module of app.domain imports on its own, first in a fresh interpreter: a script, a worker or one test file
may start with any of them, and a circular import that only works in the app's usual order fails there (#466, #475,
#505)."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[2]

# each module after forgetting every app module, so it is the first one loaded; third-party packages stay loaded
SCRIPT = """
import importlib, pkgutil, sys, traceback
import app.domain
names = sorted(m.name for m in pkgutil.iter_modules(app.domain.__path__))
for name in names:
    for k in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
        del sys.modules[k]
    try:
        importlib.import_module("app.domain." + name)
    except Exception:
        print(f"app.domain.{name}: " + traceback.format_exc(limit=-3).strip().replace(chr(10), chr(10) + "  "))
print(f"imported {len(names)}")
"""


def test_every_domain_module_imports_first():
    env = {**os.environ, "ACCESS_SECRET_KEY": os.environ.get("ACCESS_SECRET_KEY", "test-access-secret-key-0123456789abcdef")}
    run = subprocess.run([sys.executable, "-c", SCRIPT], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, run.stderr[-3000:]
    out = run.stdout.strip().splitlines()
    assert out[-1].startswith("imported ") and int(out[-1].split()[1]) > 100, run.stdout[-3000:]
    failed = "\n".join(out[:-1])
    assert not failed, "these fail when imported first:\n" + failed
