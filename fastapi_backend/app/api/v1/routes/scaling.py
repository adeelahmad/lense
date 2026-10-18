"""Auto scaling (app/domain/scaling.py, docs/scaling.md; admins only): providers, scaling groups with their rules,
the units they launch, and what the scaler is doing.

Anything an admin's API key, an app or an agent may do, they do here too: add and change providers and groups, set
the desired count within a group's bounds, pause, terminate a unit. Approving a group's terms is the one pick that
needs a person signed in to the web app (`POST /scaling/groups/{id}/approve` answers 403 to a token or an app), and a
group made or changed from the web app carries that approval with it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import AdminReader, AdminWriter, Cfg, Db, Principal, domain_errors
from app.domain import auth, scaling
from app.domain import scaling_drivers as drivers
from app.schemas.common import Ok
from app.schemas.scaling import (
    Desired,
    DriverSpec,
    Group,
    GroupChange,
    GroupIn,
    Metrics,
    Overview,
    Paused,
    Protected,
    Provider,
    ProviderChange,
    ProviderIn,
    Terminate,
    Unit,
)

router = APIRouter(prefix="/scaling", tags=["scaling"])


def _via(user: Principal) -> str:
    return "web" if user.via == "access" else user.via


def _errors():
    """The domain's errors as answers: a provider's refusal is 502, a pick that isn't the caller's 403."""

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, et, e, tb):
            if et is None:
                return False
            if issubclass(et, scaling.Unapproved):
                raise HTTPException(403, str(e)) from None
            if issubclass(et, drivers.DriverError):
                raise HTTPException(502, str(e)) from None
            if issubclass(et, ValueError):
                raise HTTPException(400, str(e)) from None
            if issubclass(et, KeyError):
                raise HTTPException(404, "not found") from None
            return False

    return _Ctx()


@router.get("")
def overview(user: AdminReader, db: Db, cfg: Cfg) -> Overview:
    """Everything at a glance: the switch, providers, groups with their units, the drivers on offer, the metrics now."""
    return Overview(**scaling.overview(db, cfg))


@router.get("/drivers")
def list_drivers(user: AdminReader) -> list[DriverSpec]:
    """The kinds of provider Lens can launch on, with the settings each asks for."""
    return [DriverSpec(**d) for d in scaling.driver_specs()]


@router.get("/metrics")
def metrics(user: AdminReader, db: Db, cfg: Cfg, steps: str | None = Query(None, description="comma-separated steps for the queue metrics")) -> Metrics:
    """The values rules can watch, now."""
    want = [s.strip() for s in steps.split(",") if s.strip()] if steps else None
    return Metrics(metrics=scaling.metrics(db, cfg, want), about=scaling.METRICS)


# ---------- providers ----------
@router.get("/providers")
def list_providers(user: AdminReader, db: Db) -> list[Provider]:
    return [Provider(**p) for p in scaling.providers(db)]


@router.post("/providers", status_code=201)
def add_provider(body: ProviderIn, user: AdminWriter, db: Db, cfg: Cfg) -> Provider:
    """Add a provider. Its credentials are sealed (never shown again, only whether they're set) and checked with a
    read-only call; its library is fetched the first time."""
    with _errors():
        p = scaling.add_provider(db, cfg, body.name, body.driver, body.settings, body.secrets, body.price, body.note, user.email, body.check)
    auth.audit(db, user.as_audit(), "scaling.provider.add", f"scale_provider:{p['id']}", {"driver": body.driver})
    return Provider(**p)


@router.get("/providers/{pid}")
def get_provider(pid: int, user: AdminReader, db: Db) -> Provider:
    with _errors():
        return Provider(**scaling.provider_view(scaling._provider(db, pid)))


@router.patch("/providers/{pid}")
def change_provider(pid: int, body: ProviderChange, user: AdminWriter, db: Db, cfg: Cfg) -> Provider:
    changes = body.model_dump(exclude_unset=True, exclude={"check"})
    with _errors():
        p = scaling.update_provider(db, cfg, pid, changes, user.email, body.check)
    auth.audit(db, user.as_audit(), "scaling.provider.change", f"scale_provider:{pid}", sorted(changes))
    return Provider(**p)


@router.post("/providers/{pid}/check")
def check_provider(pid: int, user: AdminReader, db: Db, cfg: Cfg) -> dict[str, Any]:
    """Prove the provider's credentials with a read-only call: {ok, error?}."""
    with _errors():
        return scaling.check_provider(db, cfg, pid)


@router.delete("/providers/{pid}")
def remove_provider(pid: int, user: AdminWriter, db: Db, cfg: Cfg) -> Ok:
    with _errors():
        scaling.remove_provider(db, cfg, pid, user.email)
    auth.audit(db, user.as_audit(), "scaling.provider.remove", f"scale_provider:{pid}")
    return Ok()


# ---------- groups ----------
@router.get("/groups")
def list_groups(user: AdminReader, db: Db, cfg: Cfg) -> list[Group]:
    gs = scaling.groups(db)
    for g in gs:
        g["waiting_for"] = scaling.waiting_for(g, cfg)
    return [Group(**g) for g in gs]


@router.post("/groups", status_code=201)
def create_group(body: GroupIn, user: AdminWriter, db: Db, cfg: Cfg) -> Group:
    """Make a group. From the web app it's approved as it's made (that is your pick); from a token or an app it
    waits for a person's approval before it launches anything."""
    spec = body.model_dump(exclude_unset=True)
    with _errors():
        g = scaling.create_group(db, cfg, spec, user.email, _via(user))
    auth.audit(db, user.as_audit(), "scaling.group.create", f"scale_group:{g['id']}", {"max": g["max"], "via": _via(user)})
    g["waiting_for"] = scaling.waiting_for(g, cfg)
    return Group(**g)


@router.get("/groups/{gid}")
def get_group(gid: int, user: AdminReader, db: Db, cfg: Cfg) -> Group:
    with _errors():
        g = scaling.get_group(db, gid)
    g["waiting_for"] = scaling.waiting_for(g, cfg)
    return Group(**g)


@router.patch("/groups/{gid}")
def change_group(gid: int, body: GroupChange, user: AdminWriter, db: Db, cfg: Cfg) -> Group:
    """Change a group. A change that widens its approved terms (another provider or unit, a higher max or budget)
    takes the approval away until a person gives it again, unless it's made from the web app."""
    changes = body.model_dump(exclude_unset=True)
    with _errors():
        g = scaling.update_group(db, cfg, gid, changes, user.email, _via(user))
    auth.audit(db, user.as_audit(), "scaling.group.change", f"scale_group:{gid}", sorted(changes))
    g["waiting_for"] = scaling.waiting_for(g, cfg)
    return Group(**g)


@router.post("/groups/{gid}/approve")
def approve_group(gid: int, user: AdminWriter, db: Db, cfg: Cfg) -> Group:
    """Approve the group's terms as they stand: a person's pick, from a signed-in session only (403 otherwise)."""
    with _errors():
        g = scaling.approve_group(db, cfg, gid, user.email, _via(user))
    auth.audit(db, user.as_audit(), "scaling.group.approve", f"scale_group:{gid}", g["terms"])
    g["waiting_for"] = scaling.waiting_for(g, cfg)
    return Group(**g)


@router.post("/groups/{gid}/desired")
def set_desired(gid: int, body: Desired, user: AdminWriter, db: Db, cfg: Cfg) -> Group:
    """Ask for a number of units now, within the group's min and max."""
    with _errors():
        g = scaling.set_desired(db, cfg, gid, body.desired, user.email, _via(user))
    auth.audit(db, user.as_audit(), "scaling.group.desired", f"scale_group:{gid}", {"desired": body.desired})
    g["waiting_for"] = scaling.waiting_for(g, cfg)
    return Group(**g)


@router.post("/groups/{gid}/pause")
def pause_group(gid: int, body: Paused, user: AdminWriter, db: Db, cfg: Cfg) -> Group:
    """Pause a group (nothing new launches; what runs stays) or resume it."""
    with _errors():
        g = scaling.pause_group(db, cfg, gid, body.paused, body.why, user.email)
    auth.audit(db, user.as_audit(), "scaling.group.pause" if body.paused else "scaling.group.resume", f"scale_group:{gid}")
    g["waiting_for"] = scaling.waiting_for(g, cfg)
    return Group(**g)


@router.delete("/groups/{gid}")
def remove_group(gid: int, user: AdminWriter, db: Db, cfg: Cfg) -> Ok:
    """Remove a group: its units are terminated first."""
    with _errors():
        scaling.remove_group(db, cfg, gid, user.email)
    auth.audit(db, user.as_audit(), "scaling.group.remove", f"scale_group:{gid}")
    return Ok()


@router.get("/groups/{gid}/history")
def group_history(
    gid: int, user: AdminReader, db: Db, limit: int = Query(50, ge=1, le=200), before: int | None = Query(None)
) -> list[dict[str, Any]]:
    """What happened to the group: launches, joins, terminations, changes and the cost it accrued (the ledger)."""
    with _errors():
        scaling._group(db, gid)
    return scaling.history(db, gid, limit, before)


# ---------- units ----------
@router.get("/units")
def list_units(
    user: AdminReader, db: Db, group: int | None = Query(None), alive: bool = Query(False, description="only units not yet gone")
) -> list[Unit]:
    return [Unit(**n) for n in scaling.nodes(db, group, alive)]


@router.post("/units/{nid}/protect")
def protect_unit(nid: int, body: Protected, user: AdminWriter, db: Db, cfg: Cfg) -> Unit:
    """Keep a unit out of scale-in (or let it go again)."""
    with _errors():
        n = scaling.protect(db, cfg, nid, body.protected, user.email)
    auth.audit(db, user.as_audit(), "scaling.unit.protect", f"scale_node:{nid}", {"protected": body.protected})
    return Unit(**n)


@router.post("/units/{nid}/terminate")
def terminate_unit(nid: int, body: Terminate, user: AdminWriter, db: Db, cfg: Cfg) -> Unit:
    """Let a unit go now: a running worker drains first, so its job is handed back."""
    with _errors():
        n = scaling.terminate_node(db, cfg, nid, user.email, body.why)
    auth.audit(db, user.as_audit(), "scaling.unit.terminate", f"scale_node:{nid}", {"why": body.why})
    return Unit(**n)


@router.post("/tick")
def tick(user: AdminWriter, db: Db, cfg: Cfg) -> dict[str, Any]:
    """Run one pass of the scaler now, in this process (the scheduled one carries on): what it did."""
    with domain_errors():
        out = scaling.Scaler(db, lambda: cfg, f"api-tick-{user.id}").tick(lease=False)
    return out or {}
