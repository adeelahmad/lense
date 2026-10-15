"""New releases, upgrading from the web app and the database backups (admins only; docs/updates.md)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.backups import BackupSchedule, CopyWork, RestoreWork
from app.schemas.common import RequestModel, ResponseModel


class Release(ResponseModel):
    tag: str = Field(description="vX.Y.Z")
    name: str
    url: str = Field(description="the release's page on GitHub")
    published_at: str | None = None
    notes: str = Field("", description="what changed, in Markdown")


class UpgradeRun(ResponseModel):
    id: str
    ref: str = Field(description="the release tag (or branch) being installed; empty: the one running, set up again")
    https: bool | None = Field(None, description="the HTTPS ports opened (true) or closed (false) by this run (docs/https.md)")
    state: Literal["backing-up", "queued", "running", "done", "failed"]
    by: str | None = None
    asked_at: str | None = None
    queued_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    backup: str | None = Field(None, description="the backup taken first, in Backups")
    version: str | None = Field(None, description="the version installed, once done")
    error: str | None = None
    log: list[str] = Field(default_factory=list, description="the last lines install.sh printed")


class UpdateStatus(ResponseModel):
    current: str = Field(description="the version running")
    latest: Release | None = None
    available: bool = Field(description="latest is newer than current")
    checked_at: str | None = None
    error: str | None = Field(None, description="why the last check failed")
    check: bool = Field(description="updates.check: the daily check is on")
    auto_install: bool
    agent: str | None = Field(None, description="what upgrades this machine (systemd, launchd); none: run `manual` there")
    manual: str = Field(description="the line that upgrades it by hand, on the machine Lens runs on")
    upgrade: UpgradeRun | None = Field(None, description="the last upgrade asked for")


class UpgradeRequest(RequestModel):
    ref: str | None = Field(None, max_length=100, description="a release tag; default: the latest release")
    backup: bool = Field(True, description="back the database up first")


class Backup(ResponseModel):
    name: str
    label: str = Field(description="manual, or before-upgrade")
    kind: Literal["export", "folder"] = Field(description="export: a SurrealDB export (.surql.gz); folder: an embedded database's copy")
    bytes: int
    taken_at: str


class Backups(ResponseModel):
    folder: str
    backups: list[Backup]
    database: Literal["server", "embedded", "memory"] | None = Field(
        None, description="the database this Lens uses: a restore needs a backup of the same kind (export or folder)"
    )
    in_progress: list[str] = Field(default_factory=list, description="backups being written now, by any Lens process")
    restore: RestoreWork | None = Field(None, description="the restore running on this server, or the last one")
    schedule: BackupSchedule | None = None


class RemoteBackups(ResponseModel):
    connection: int | None = Field(None, description="backups.connection: where copies go, or none")
    folder: str | None = None
    backups: list[Backup] = Field(default_factory=list, description="the copies there, newest first")
    error: str | None = Field(None, description="why the connection couldn't be listed")
    last_at: str | None = Field(None, description="when the last backup was copied there (or tried)")
    last_name: str | None = None
    last_error: str | None = Field(None, description="why copying the last backup failed")
    copying: CopyWork


class Monitoring(ResponseModel):
    on: bool = Field(description="the monitoring profile (Prometheus, node-exporter, Grafana) as last asked")
    state: Literal["queued", "running", "done", "failed"] | None = Field(None, description="how the machine's last change went")
    error: str | None = None
    asked_at: str | None = None
    finished_at: str | None = None
    grafana_port: int = Field(3001, description="the port Grafana listens on, where the web app does")
    agent: str | None = Field(None, description="what changes it on this machine (systemd, launchd); none: run `manual` there")
    manual: str = Field(description="the line that turns it on by hand, on the machine Lens runs on")


class MonitoringRequest(RequestModel):
    on: bool = Field(description="start (true) or stop (false) Prometheus, node-exporter and Grafana")
