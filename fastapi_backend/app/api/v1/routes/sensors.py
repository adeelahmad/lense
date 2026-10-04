"""Sensors (admins): everything that feeds Lens in one list, the readings stream sensors keep, the hub's logins; and
the address webhooks push to (their token is all they need)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import AdminReader, AdminWriter, Cfg, Db, domain_errors
from app.domain import auth, sensor_patterns, sensors
from app.schemas.common import Created, Ok
from app.schemas.sensors import (
    HubLogin,
    HubLoginCreate,
    Pattern,
    PatternUpdate,
    Pushed,
    Reading,
    Reviewed,
    SensorCatalog,
    SensorCreate,
    SensorCreated,
    SensorDetail,
    SensorToken,
    SensorType,
    SensorUpdate,
    SeriesPoint,
    Suggestion,
)

router = APIRouter(tags=["sensors"])
MAX_PUSH = 1024 * 1024


@router.get("/sensors")
def list_sensors(user: AdminReader, db: Db, cfg: Cfg) -> SensorCatalog:
    """Every sensor: storage, email and calendar sources (files), and MQTT devices, syslog senders and webhooks
    (streams); the kinds there are; and whether the hub is running."""
    keys = {"label", "family", "fields", "secrets", "help"}
    return SensorCatalog.model_validate(
        {
            "sensors": sensors.list_all(db, cfg),
            "types": {k: SensorType.model_validate({f: x for f, x in v.items() if f in keys}) for k, v in sensors.types().items()},
            "hub": sensors.hub_status(db, cfg),
        }
    )


@router.post("/sensors")
def create_sensor(body: SensorCreate, user: AdminWriter, db: Db, cfg: Cfg) -> SensorCreated:
    """Add a stream sensor before it reports (to give it a namespace and handling up front), or a webhook. A webhook's
    token is in the answer and never shown again. Storage, email and calendar sensors are added as sources."""
    with domain_errors():
        sid, token = sensors.create(
            db,
            cfg,
            body.type,
            body.name,
            body.params,
            body.space,
            body.handling.model_dump(exclude_unset=True) if body.handling else None,
            user.email,
            body.secrets,
        )
    auth.audit(db, user.as_audit(), "sensor.create", f"storage_source:{sid}")
    return SensorCreated(id=sid, token=token)


@router.post("/sensors/review")
def review_new(user: AdminWriter, db: Db, cfg: Cfg) -> Reviewed:
    """Give every new sensor its suggested handling, and mark them looked at."""
    n = 0
    for s in sensors.list_all(db, cfg):
        if s["family"] == "stream" and s["status"] == "new":
            with domain_errors():
                sensors.apply_suggestion(db, cfg, s["id"])
            n += 1
    auth.audit(db, user.as_audit(), "sensor.review", None, {"applied": n})
    return Reviewed(applied=n)


@router.get("/sensors/{sid}")
def get_sensor(sid: int, user: AdminReader, db: Db, cfg: Cfg) -> SensorDetail:
    with domain_errors():
        return SensorDetail.model_validate(sensors.detail(db, cfg, sid))


@router.patch("/sensors/{sid}")
def update_sensor(sid: int, body: SensorUpdate, user: AdminWriter, db: Db, cfg: Cfg) -> Ok:
    """Rename a stream sensor, change its status (new, active, paused: nothing kept, ignored: nothing kept and out of
    sight), its namespace (`space: null` for none) or its handling (a handling setting of null goes back to the
    settings' default)."""
    changes = body.model_dump(exclude_unset=True)
    if "handling" in changes and body.handling is not None:
        changes["handling"] = body.handling.model_dump(exclude_unset=True)
    with domain_errors():
        sensors.update(db, sid, cfg, **changes)
    auth.audit(db, user.as_audit(), "sensor.update", f"storage_source:{sid}", sorted(changes))
    return Ok()


@router.delete("/sensors/{sid}")
def delete_sensor(sid: int, user: AdminWriter, db: Db) -> Ok:
    """Remove a sensor and everything it kept (a file sensor's resources stay)."""
    with domain_errors():
        sensors.remove(db, sid)
    auth.audit(db, user.as_audit(), "sensor.delete", f"storage_source:{sid}")
    return Ok()


@router.post("/sensors/{sid}/suggestion")
def apply_suggestion(sid: int, user: AdminWriter, db: Db, cfg: Cfg) -> Suggestion:
    """Give a stream sensor the handling suggested for it, and mark it looked at."""
    with domain_errors():
        got = sensors.apply_suggestion(db, cfg, sid)
    auth.audit(db, user.as_audit(), "sensor.suggestion", f"storage_source:{sid}")
    return Suggestion.model_validate(got)


@router.post("/sensors/{sid}/token")
def new_token(sid: int, user: AdminWriter, db: Db) -> SensorToken:
    """A new token for a webhook; the old one stops working."""
    with domain_errors():
        token = sensors.new_token(db, sid)
    auth.audit(db, user.as_audit(), "sensor.token", f"storage_source:{sid}")
    return SensorToken(token=token)


@router.get("/sensors/{sid}/readings")
def list_readings(
    sid: int,
    user: AdminReader,
    db: Db,
    stream: str | None = Query(None, description="a stream's id"),
    before: str | None = Query(None, description="readings before this time, to page back"),
    limit: int = Query(100, ge=1, le=1000),
) -> list[Reading]:
    """A stream sensor's readings, newest first."""
    with domain_errors():
        sensors.get(db, sid)
    return [Reading.model_validate(r) for r in sensors.readings(db, sid, stream, before, limit)]


@router.get("/sensors/{sid}/series")
def get_series(
    sid: int,
    user: AdminReader,
    db: Db,
    stream: str = Query(description="a stream's id"),
    field: str | None = Query(None, description="one of a JSON stream's numbers"),
    hours: int = Query(168, ge=1, le=24 * 3660),
) -> list[SeriesPoint]:
    """Hourly summaries of a stream (count, min, max, average, last), oldest first."""
    with domain_errors():
        sensors.get(db, sid)
    return [SeriesPoint.model_validate(p) for p in sensors.series(db, sid, stream, field, hours)]


@router.get("/sensors/{sid}/patterns")
def list_patterns(
    sid: int,
    user: AdminReader,
    db: Db,
    stream: str | None = Query(None, description="a stream's id"),
    label: str | None = Query(None, description="routine, notable, alert, or none for those without one"),
    limit: int = Query(200, ge=1, le=2000),
) -> list[Pattern]:
    """A log sensor's kinds of line, busiest first."""
    with domain_errors():
        sensors.get(db, sid)
    return [Pattern.model_validate(p) for p in sensor_patterns.list_patterns(db, sid, stream, label, limit)]


@router.patch("/sensor-patterns/{pid}")
def update_pattern(pid: str, body: PatternUpdate, user: AdminWriter, db: Db) -> Ok:
    """Label a kind of line yourself (`label: null` clears it), or stop keeping its lines (`action: drop`; they're
    still counted)."""
    sent = body.model_dump(exclude_unset=True)
    with domain_errors():
        sensor_patterns.update(db, pid, body.label, body.action, user.email, clear_label="label" in sent and body.label is None)
    auth.audit(db, user.as_audit(), "sensor_pattern.update", f"sensor_pattern:{pid}", sorted(sent))
    return Ok()


# ---------- hub logins ----------
@router.get("/sensor-logins")
def list_logins(user: AdminReader, db: Db) -> list[HubLogin]:
    """The usernames devices sign in to the MQTT hub with."""
    return [HubLogin.model_validate(r) for r in sensors.logins(db)]


@router.post("/sensor-logins")
def create_login(body: HubLoginCreate, user: AdminWriter, db: Db) -> Created:
    with domain_errors():
        lid = sensors.create_login(db, body.username, body.password, body.space, user.email)
    auth.audit(db, user.as_audit(), "sensor_login.create", f"sensor_login:{lid}")
    return Created(id=lid)


@router.delete("/sensor-logins/{lid}")
def delete_login(lid: int, user: AdminWriter, db: Db) -> Ok:
    with domain_errors():
        sensors.remove_login(db, lid)
    auth.audit(db, user.as_audit(), "sensor_login.delete", f"sensor_login:{lid}")
    return Ok()


# ---------- webhooks ----------
async def _push(request: Request, token: str | None, stream: str | None) -> Pushed:
    if not token:
        auth_header = request.headers.get("authorization") or ""
        token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else None
    body = await request.body()
    if len(body) > MAX_PUSH:
        raise HTTPException(413, "send at most 1 MB at a time")
    archive = request.app.state.archive
    try:
        kept = await run_in_threadpool(
            sensors.push,
            archive.db,
            archive.current,
            token,
            stream or request.query_params.get("stream"),
            body,
            request.headers.get("content-type") or "",
        )
    except KeyError:
        raise HTTPException(401, "that token isn't a webhook's") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return Pushed(kept=kept)


@router.post("/sensors/push", status_code=202)
async def push(request: Request) -> Pushed:
    """Send readings to a webhook sensor, with its token as a bearer token. Plain text is a reading per line; JSON is
    one reading, or several as {readings: [{stream, value}]}. `?stream=` names the stream (default: default)."""
    return await _push(request, None, None)


@router.post("/sensors/push/{token}", status_code=202)
async def push_token(token: str, request: Request) -> Pushed:
    """The same, with the token in the address, for devices that can only be given a URL."""
    return await _push(request, token, None)


@router.post("/sensors/push/{token}/{stream:path}", status_code=202)
async def push_stream(token: str, stream: str, request: Request) -> Pushed:
    """The same, to a named stream."""
    return await _push(request, token, stream)
