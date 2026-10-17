"""New releases, upgrading from the web app and the database backups (admins only; docs/updates.md)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

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
    public: bool | None = Field(
        None, description="the web app made reachable from other machines on the network (true) or this one only (false) by this run"
    )
    target: Literal["full", "lean"] | None = Field(None, description="the edition this run changed to")
    state: Literal["backing-up", "queued", "running", "rolling-back", "done", "failed", "rolled-back"] = Field(
        description="rolling-back / rolled-back: the new version didn't start, so the agent went back to the one before"
    )
    step: Literal["backup", "download", "build", "start", "check"] | None = Field(
        None, description="while running: what the agent is on (the backup comes before); backup: backing up failed"
    )
    restore: str | None = Field(None, description="after a rollback: whether the backup was put back, and why")
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


class InstallLines(ResponseModel):
    public: str = Field(description="the install line that makes the web app reachable from other machines (LENS_PUBLIC=1)")
    private: str = Field(description="the install line that keeps it to this machine (LENS_PUBLIC=0)")
    full: str = Field(description="the install line for the full edition (LENS_TARGET=full)")
    lean: str = Field(description="the install line for the lean edition (LENS_TARGET=lean)")


class InstallStatus(ResponseModel):
    public: bool | None = Field(
        None,
        description="the web app is reachable from other machines on the network (LENS_BIND), or from this one only; "
        "none: Lens wasn't started by install.sh or docker-compose.prod.yml, so it doesn't know",
    )
    target: Literal["full", "lean"] | None = Field(
        None,
        description="the edition (LENS_TARGET): full has LibreOffice and Chromium, to read Office files, web pages and "
        "emails; lean doesn't. None: not known here",
    )
    agent: str | None = Field(None, description="what changes them on this machine (systemd, launchd); none: run a `manual` line there")
    manual: InstallLines = Field(description="the line to run on the machine Lens runs on for each choice, when there's no agent")
    reconfigure: UpgradeRun | None = Field(None, description="the last run that changed either (as in /admin/updates)")


class InstallChange(RequestModel):
    public: bool | None = Field(None, description="reachable from other machines on the network (true) or this machine only (false)")
    target: Literal["full", "lean"] | None = Field(None, description="the edition: full or lean")

    @model_validator(mode="after")
    def _something(self) -> InstallChange:
        if self.public is None and self.target is None:
            raise ValueError("say what to change: public, target or both")
        return self


class UpgradeRequest(RequestModel):
    ref: str | None = Field(None, max_length=100, description="a release tag; default: the latest release")
    backup: bool = Field(True, description="back the database up first")


class Backup(ResponseModel):
    name: str
    label: str = Field(description="manual, or before-upgrade")
    kind: Literal["export", "folder"] = Field(description="export: a SurrealDB export (.surql.gz); folder: an embedded database's copy")
    bytes: int = Field(description="the database's part and the files kept with it")
    files: bool = Field(False, description="the files under data_dir are kept with it, and put back when it is restored")
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


class Service(ResponseModel):
    profile: str = Field(description="the Docker Compose profile: mcp (the MCP runner), browser, monitoring")
    label: str
    description: str
    on: bool = Field(description="as last asked here; never asked here: whether it runs")
    running: bool | None = Field(None, description="whether it answers where Lens reaches it now; null: Lens can't tell")
    state: Literal["queued", "running", "done", "failed"] | None = Field(None, description="how the machine's last change went")
    error: str | None = None
    asked_at: str | None = None
    finished_at: str | None = None
    agent: str | None = Field(None, description="what changes it on this machine (systemd, launchd); none: run `manual` there")
    manual: str = Field(description="the line that turns it on by hand, on the machine Lens runs on")


class Services(ResponseModel):
    services: list[Service]


class ServiceRequest(RequestModel):
    on: bool = Field(description="start (true) or stop (false) the profile's containers")


class SandboxGateway(ResponseModel):
    on: bool = Field(description="the sandbox profile (the code sandbox's OpenShell gateway) as last asked")
    state: Literal["queued", "running", "done", "failed"] | None = Field(None, description="how the machine's last change went")
    error: str | None = None
    asked_at: str | None = None
    finished_at: str | None = None
    agent: str | None = Field(None, description="what changes it on this machine (systemd, launchd); none: run `manual` there")
    manual: str = Field(description="the line that starts it by hand, on the machine Lens runs on")
    preflight: list[str] = Field(description="what stands in the way of running it on this machine, a sentence each")
    address: str = Field(description="where Lens reaches the gateway (sandbox.gateway)")
    reachable: bool | None = Field(None, description="whether the gateway answered and let Lens in; null: not checked")
    problem: str | None = Field(None, description="why it didn't, when it didn't")


class SandboxGatewayRequest(RequestModel):
    on: bool = Field(description="start (true) or stop (false) the code sandbox's gateway; stopping deletes its sandboxes")
