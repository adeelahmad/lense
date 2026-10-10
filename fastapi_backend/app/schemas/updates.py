"""New releases, upgrading from the web app and the database backups (admins only; docs/updates.md)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class Release(ResponseModel):
    tag: str = Field(description="vX.Y.Z")
    name: str
    url: str = Field(description="the release's page on GitHub")
    published_at: str | None = None
    notes: str = Field("", description="what changed, in Markdown")


class UpgradeRun(ResponseModel):
    id: str
    ref: str = Field(description="the release tag (or branch) being installed")
    state: Literal["backing-up", "queued", "running", "rolling-back", "done", "failed", "rolled-back"] = Field(
        description="rolling-back / rolled-back: the new version didn't start, so the agent went back to the one before"
    )
    step: Literal["download", "build", "start", "check"] | None = Field(
        None, description="while running: what the agent is on (the backup comes before)"
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
