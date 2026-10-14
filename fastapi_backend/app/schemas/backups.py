"""Restoring backups, downloading them by link, and the backup schedule (app/domain/backups.py, admins only). The list
itself is updates.Backups."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class RestoreWork(ResponseModel):
    running: bool = Field(False, description="a restore is running on this server now")
    name: str | None = Field(None, description="the backup being (or last) restored")
    error: str | None = Field(None, description="why the last restore here failed (what was there was put back)")
    finished_at: str | None = None
    before: str | None = Field(None, description="the before-restore backup of what the database held")


class BackupSchedule(ResponseModel):
    schedule: str | None = Field(None, description="backups.schedule: a cron expression, or none")
    timezone: str = "UTC"
    keep: int = Field(7, description="how many scheduled backups are kept")
    next_at: str | None = None
    last_at: str | None = None
    last_name: str | None = None
    last_error: str | None = None


class RestoreStart(RequestModel):
    confirm: str = Field(description="the backup's name again, to show this is meant: restoring replaces everything")


class BackupLink(ResponseModel):
    url: str = Field(description="opens the download, without signing in, until it expires")
    expires_in: int = Field(description="seconds")


class CopyWork(ResponseModel):
    running: Literal["send", "fetch"] | None = Field(None, description="copying a backup to (send) or from (fetch) the connection")
    name: str | None = None
    error: str | None = None
    finished_at: str | None = None
