"""Opening the archive: configuration, database, settings saved in the app, and background threads."""

from __future__ import annotations

import logging
import os
import secrets
import threading
from typing import Any

from app.config import settings as env
from app.domain import auth, jobs, notify, settings, sources, store, templates

log = logging.getLogger("lens")


class Archive:
    """Everything a process needs to serve or work on the archive."""

    def __init__(self, cfg: dict[str, Any] | None = None, db: store.DB | None = None):
        self.base = cfg or store.load_config(env.ARCHIVE_CONFIG)
        self.db = db or store.connect(self.base)
        self.owns_db = db is None
        self.settings = settings.Settings(self.db, self.base)
        self.stop = threading.Event()
        self.setup_code: str | None = None

    def current(self) -> dict[str, Any]:
        return self.settings.current()

    def prepare(self) -> None:
        templates.seed(self.db)
        # Pay for the embedded engine's full-text repair at startup rather than in someone's first search.
        self.db.ready_fulltext()
        if auth.account_count(self.db) == 0:
            self.setup_code = os.environ.get("LENS_SETUP_CODE") or secrets.token_urlsafe(9)
            log.warning("No accounts yet. Create the first admin in the web app with setup code: %s", self.setup_code)

    def start_background(self) -> None:
        """Inline workers, the watched-folder poller and the notifier. Production runs these as separate processes (`lens worker`)."""
        for i in range(max(0, int(self.current()["workers"]["inline"]))):
            w = jobs.Worker(self.db, self.current, name=f"api-{os.getpid()}-{i}", log=log.info)
            threading.Thread(target=w.loop, args=(self.stop,), daemon=True, name=f"worker-{i}").start()

        def watcher() -> None:
            while not self.stop.wait(max(5, self.current()["sources"]["check_seconds"])):
                try:
                    sources.poll_due(self.db, self.current(), log=log.info)
                except Exception:  # noqa: BLE001 - keep polling
                    log.exception("watcher failed")

        threading.Thread(target=watcher, daemon=True, name="watcher").start()
        notify.start(self.db, self.current, self.stop, name=f"api-{os.getpid()}", log=log.warning)

    def close(self) -> None:
        self.stop.set()
        if self.owns_db:
            self.db.close()
