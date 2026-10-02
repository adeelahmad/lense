"""Settings saved in the app, the audit log, the health page and rebuilding the search index (admins only)."""

from __future__ import annotations

import logging
import pathlib
import shutil
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request

from app.api.deps import AdminReader, AdminWriter, Cfg, Db, domain_errors
from app.core.middleware import host_name
from app.domain import auth, jobs, llm, settings, sources, store, telemetry
from app.schemas.admin import AuditEntry, Health, LlmTestResult, Started, TelemetryStatus, TelemetryTestResult
from app.schemas.auth import AccountToken
from app.schemas.common import Ok

log = logging.getLogger("lens")
router = APIRouter(tags=["admin"])


@router.get("/settings")
def get_settings(user: AdminReader, request: Request, db: Db) -> dict[str, Any]:
    """Every section people can change in the app, with who changed it last. Secrets show only whether they are set."""
    return settings.view(db, request.app.state.archive.base)


@router.put("/settings/{section}")
def update_settings(section: str, body: dict[str, Any], user: AdminWriter, request: Request, db: Db) -> Ok:
    """Change some settings in one section; the keys and their types are checked against the section's defaults."""
    if section == "server" and "allowed_hosts" in body:
        here = host_name(request.headers.get("host", ""))
        hosts = [str(h).lower() for h in body["allowed_hosts"] or []]
        if "*" not in hosts and here not in hosts:
            raise HTTPException(400, f"that list leaves out {here}, the address you're using, and would lock you out")
    with domain_errors():
        settings.save(db, request.app.state.archive.base, section, body, user.email)
    auth.audit(db, user.as_audit(), "settings.save", section, sorted(body))
    return Ok()


@router.post("/settings/llm/test")
def test_llm(user: AdminWriter, cfg: Cfg) -> LlmTestResult:
    """Ask the configured model for one word, to check the address, key and model name."""
    if not llm.configured(cfg):
        return LlmTestResult(ok=False, error="set a base URL and a model first")
    t0 = time.time()
    try:
        reply = llm.chat(cfg, [{"role": "user", "content": "Reply with the single word OK."}], max_tokens=5)
    except llm.LLMError as e:
        return LlmTestResult(ok=False, error=str(e))
    return LlmTestResult(ok=True, reply=reply.strip()[:40], ms=int((time.time() - t0) * 1000), model=cfg["llm"]["model"])


@router.get("/settings/telemetry/status")
def telemetry_status(user: AdminReader, cfg: Cfg) -> TelemetryStatus:
    """Whether telemetry is on, where it goes, and how the API process's last exports went. Off by default."""
    return TelemetryStatus.model_validate(telemetry.status(cfg))


@router.post("/settings/telemetry/test")
def test_telemetry(user: AdminWriter, cfg: Cfg) -> TelemetryTestResult:
    """Send one test span to the saved endpoint now (on or off), to check the address and headers."""
    ok, error, ms = telemetry.test_export(cfg)
    return TelemetryTestResult(ok=ok, error=error, ms=ms)


@router.get("/audit")
def list_audit(user: AdminReader, db: Db, limit: int = Query(200, ge=1, le=1000)) -> list[AuditEntry]:
    """Who changed what, newest first."""
    return db.rows(f"SELECT at, email, action, target, detail FROM audit_log ORDER BY at DESC LIMIT {limit}")


@router.get("/admin/tokens")
def list_all_tokens(user: AdminReader, db: Db) -> list[AccountToken]:
    """Everyone's API keys (admins), the latest made first: whose, what scope, when it expires and was last used."""
    return [AccountToken(**t) for t in auth.all_tokens(db)]


@router.delete("/admin/tokens/{token_id}")
def revoke_any_token(token_id: int, user: AdminWriter, db: Db) -> Ok:
    """Revoke anyone's API key (admins): whatever uses it stops working now. Audited as `token.revoke`."""
    owner = auth.drop_token(db, token_id)
    if owner is None:
        raise HTTPException(404, "not found")
    auth.audit(db, user.as_audit(), "token.revoke", f"api_token:{token_id}", {"account": owner})
    return Ok()


@router.get("/admin/health")
def get_health(user: AdminReader, request: Request, db: Db) -> Health:
    """The database, the job queue, workers, storage sources and disk space at a glance."""
    try:
        ok = bool(db.ping())
    except Exception:  # noqa: BLE001 - a failed ping is the answer
        ok = False
    data_dir = request.app.state.archive.base["data_dir"]
    where = pathlib.Path(data_dir)
    while not where.exists() and where != where.parent:  # not created yet: measure the disk it will live on
        where = where.parent
    disk = shutil.disk_usage(where)

    def n(table: str) -> int:
        rows = db.rows(f"SELECT count() AS n FROM {table} GROUP ALL")
        return rows[0]["n"] if rows else 0

    return Health.model_validate(
        {
            "database": {"url": db.url, "embedded": db.embedded, "fulltext": getattr(db, "fulltext", None), "ok": ok},
            "queue": jobs.counts(db),
            "workers": db.rows("SELECT record::id(id) AS name, steps, host, heartbeat_at, current FROM worker"),
            "sources": [{"id": x["id"], "name": x["name"], "type": x["type"], "health": x.get("health")} for x in sources.list_sources(db)],
            "disk": {"path": data_dir, "free_gb": round(disk.free / 1e9, 1), "total_gb": round(disk.total / 1e9, 1)},
            "counts": {"recordings": n("recording"), "speakers": n("speaker"), "accounts": n("account")},
        }
    )


def _reindex(request: Request) -> None:
    try:
        store.reindex(request.app.state.db, request.app.state.settings.current())
    except Exception:  # noqa: BLE001 - runs after the response; log it
        log.exception("reindex failed")


@router.post("/admin/reindex", status_code=202)
def reindex_search(user: AdminWriter, request: Request, db: Db, tasks: BackgroundTasks) -> Started:
    """Rebuild the search index in the background."""
    tasks.add_task(_reindex, request)
    auth.audit(db, user.as_audit(), "search.reindex")
    return Started(status="reindexing")
