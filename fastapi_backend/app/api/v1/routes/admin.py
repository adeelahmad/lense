"""Settings saved in the app, the audit log, the health page and rebuilding the search index (admins only)."""

from __future__ import annotations

import logging
import pathlib
import shutil
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request

from app import email
from app.api.deps import AdminReader, AdminWriter, Cfg, Db, domain_errors
from app.core.middleware import host_name
from app.domain import auth, bridge, decide, jobs, llm, semantic, settings, sources, store, telemetry, tunnel
from app.schemas.admin import (
    AuditEntry,
    BridgeStatus,
    BridgeTestResult,
    DecisionStatus,
    DecisionTestResult,
    EmbedTestResult,
    Health,
    IndexQueued,
    LlmTestResult,
    MailTestResult,
    SemanticStatus,
    Started,
    TelemetryStatus,
    TelemetryTestResult,
    TunnelStatus,
)
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
    if (
        section == "bridge"
        and body.get("enabled")
        and "account" not in body
        and not request.app.state.archive.current()["bridge"].get("account")
    ):
        body = {**body, "account": user.email}  # turned on without saying who it answers as: the admin who turned it on
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


@router.post("/settings/mail/test")
async def test_mail(user: AdminWriter, cfg: Cfg) -> MailTestResult:
    """Send a short message to your own address through the email settings, to check them."""
    if not email.mail_enabled(cfg):
        return MailTestResult(ok=False, error="set the SMTP server and the From address first")
    try:
        await email.send_test_email(cfg, user.email)
    except Exception as e:  # noqa: BLE001 - the server's answer is what's useful here
        return MailTestResult(ok=False, to=user.email, error=f"{type(e).__name__}: {e}"[:400])
    return MailTestResult(ok=True, to=user.email)


@router.get("/settings/bridge")
def bridge_status(user: AdminReader, cfg: Cfg, db: Db) -> BridgeStatus:
    """How the assistant's chat-room bridge (Matterbridge) is doing."""
    return BridgeStatus(**bridge.status(db, cfg))


@router.get("/settings/tunnel/status")
def tunnel_status(user: AdminReader, cfg: Cfg, db: Db) -> TunnelStatus:
    """How the Cloudflare tunnel (Settings › Remote access) is doing: its address, whether it's connected, and
    cloudflared's last lines."""
    return TunnelStatus(**tunnel.status(db, cfg))


@router.post("/settings/bridge/test")
def test_bridge(user: AdminWriter, cfg: Cfg, db: Db) -> BridgeTestResult:
    """Check that Matterbridge answers at its address with its token, and that the account to answer as exists."""
    error = bridge.check(db, cfg)
    return BridgeTestResult(ok=error is None, error=error)


@router.get("/settings/decisions/status")
def decision_status(user: AdminReader, cfg: Cfg) -> DecisionStatus:
    """Who takes routine decisions, and whether a local Laya model can here: on this machine (Apple Silicon, once
    fetched) or at a Laya server's address."""
    return DecisionStatus(
        engine=(cfg.get("decisions") or {}).get("engine") or "auto",
        by=decide.engine(cfg),
        apple_silicon=decide.laya_here(),
        laya=decide.laya_status(cfg),
        laya_models=[{"id": k, "about": v} for k, v in decide.LAYA_MODELS.items()],
    )


@router.post("/settings/decisions/test")
def test_decisions(user: AdminWriter, cfg: Cfg) -> DecisionTestResult:
    """Take one made-up decision with the settings, to check them; says who answered."""
    t0 = time.time()
    try:
        d = decide.choose(
            cfg,
            "Which team should handle this message?",
            {"billing": "invoices, payments and refunds", "technical": "bugs and outages", "sales": "new purchases"},
            "I was billed twice this month. Please refund the duplicate.",
        )
    except decide.Undecided as e:
        return DecisionTestResult(ok=False, error=str(e))
    return DecisionTestResult(ok=True, by=d["by"], choice=d["choice"], confidence=d["confidence"], ms=int((time.time() - t0) * 1000))


@router.post("/settings/embeddings/test")
def test_embeddings(user: AdminWriter, cfg: Cfg, db: Db) -> EmbedTestResult:
    """Embed one sentence with the configured model, to check the address, key and model name."""
    if not semantic.configured(cfg):
        return EmbedTestResult(ok=False, error="turn search by meaning on, with a base URL (or the LLM provider's) and a model")
    t0 = time.time()
    try:
        vec = semantic.embed(cfg, ["Lens checks that it can search by meaning."], timeout=30)[0]
    except semantic.EmbedError as e:
        return EmbedTestResult(ok=False, error=str(e))
    semantic.recovered(db)
    return EmbedTestResult(ok=True, dimension=len(vec), ms=int((time.time() - t0) * 1000), model=semantic.endpoint(cfg)[2])


@router.get("/admin/semantic")
def semantic_status(user: AdminReader, cfg: Cfg, db: Db) -> SemanticStatus:
    """Search by meaning: whether it's set up, its model, and how many recordings are indexed with it."""
    return SemanticStatus(**semantic.status(db, cfg))


@router.post("/admin/semantic/index")
def index_semantic(user: AdminWriter, cfg: Cfg, db: Db, limit: int = Query(500, ge=1, le=5000)) -> IndexQueued:
    """Queue the embed step for up to `limit` recordings not yet indexed with the configured model, oldest first (a
    recording with a job waiting or running is left for the next time)."""
    if not semantic.configured(cfg):
        raise HTTPException(400, "turn search by meaning on, with an embeddings server and a model, first")
    semantic.recovered(db)
    rids = semantic.unindexed(db, cfg, sorted(store.space_names(db)), limit + 1)
    for rid in rids[:limit]:
        jobs.enqueue(db, rid, ["embed"], by=user.email)
    auth.audit(db, user.as_audit(), "search.index_meaning", None, {"recordings": len(rids[:limit])})
    return IndexQueued(recordings=len(rids[:limit]), remaining=len(rids) > limit)


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
