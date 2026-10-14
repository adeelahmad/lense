"""LibreOffice and Chromium installed the first time they're needed, as in a microsandbox's full edition: a folder to ask
in (LENS_PROGRAMS) and a stand-in for the start script's loop that answers there (microsandbox/run.sh)."""

from __future__ import annotations

import contextlib
import shutil
import threading

from app.domain import convert


def sandbox(tmp_path, monkeypatch):
    """The full edition's folder for asking, with LibreOffice and Chromium looked for only in a `bin` folder of its own,
    where neither is yet. Returns (the folder, bin)."""
    root, bin_dir = tmp_path / "programs", tmp_path / "bin"
    for d in (root / "ask", root / "got", bin_dir):
        d.mkdir(parents=True)
    monkeypatch.setenv("LENS_PROGRAMS", str(root))
    monkeypatch.setattr(convert, "soffice", lambda cfg: shutil.which("soffice", path=str(bin_dir)))
    monkeypatch.setattr(convert, "chromium", lambda cfg: shutil.which("chromium", path=str(bin_dir)))
    return root, bin_dir


def program(path):
    """A program that does nothing, at `path`."""
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


@contextlib.contextmanager
def installer(root, install):
    """Answers each ask/NAME.want as the start script does: `install(name)` puts the program in place, then
    got/NAME.ok; if it raises, got/NAME.failed says why. One already installed is only taken off the list. Yields the
    names it installed (or tried to), in order."""
    asked, stop = [], threading.Event()

    def loop():
        while not stop.is_set():
            for want in sorted((root / "ask").glob("*.want")):
                name, got = want.name[: -len(".want")], root / "got"
                if not (got / f"{name}.ok").exists():
                    asked.append(name)
                    (got / f"{name}.failed").unlink(missing_ok=True)
                    try:
                        install(name)
                    except Exception as e:  # noqa: BLE001 - answered, as apt's failure is
                        (got / f"{name}.failed").write_text(f"Reading package lists...\n{e}\n")
                    else:
                        (got / f"{name}.ok").touch()
                want.unlink(missing_ok=True)
            stop.wait(0.05)

    th = threading.Thread(target=loop, daemon=True)
    th.start()
    try:
        yield asked
    finally:
        stop.set()
        th.join()
