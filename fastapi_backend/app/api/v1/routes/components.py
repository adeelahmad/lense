"""What Lens needs to do its work, and where each worker is with it (docs/components.md). Admins only."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AdminReader, AdminWriter, Cfg, Db
from app.domain import auth, components, jobs, machine
from app.schemas.common import Ok
from app.schemas.components import Components

router = APIRouter(prefix="/components", tags=["admin"])


@router.get("")
def list_components(user: AdminReader, db: Db, cfg: Cfg) -> Components:
    """Every component (programs, packages, models), whether the settings need it, and each worker's machine and where
    it is with each: ready, being fetched, failed or missing. Steps that need one being fetched wait for it."""
    return Components(
        auto=bool((cfg.get("components") or {}).get("auto", True)),
        machine=machine.probe(cfg["data_dir"]),
        recommended=components.recommendation(cfg),
        components=components.catalog(cfg),
        workers=[
            {k: w.get(k) for k in ("name", "host", "heartbeat_at", "machine")} | {"components": w.get("components") or {}}
            for w in jobs.workers(db)
        ],
    )


@router.post("/check")
def check_components(user: AdminWriter, db: Db) -> Ok:
    """Ask every worker to check what it needs now, and to try again what failed. Audited as ``components.check``."""
    components.poke(db)
    auth.audit(db, user.as_audit(), "components.check", "components", None)
    return Ok()
