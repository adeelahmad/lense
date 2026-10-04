"""A namespace's notification targets: webhooks, Matterbridge, Slack and Discord (docs/notifications.md)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

TargetKind = Literal["webhook", "matterbridge", "slack", "discord"]
EventType = Literal["job.succeeded", "job.failed", "job.cancelled", "batch.finished", "recording.added"]


class LastSend(ResponseModel):
    at: str
    ok: bool
    code: int | None = Field(None, description="the HTTP status it answered with")
    error: str | None = None
    event: str | None = None


class NotifyTarget(ResponseModel):
    id: int
    name: str
    kind: TargetKind
    url: str | None = Field(None, description="scheme, host and port of its address; the rest stays sealed")
    events: list[EventType]
    enabled: bool
    gateway: str | None = Field(None, description="Matterbridge: the gateway it sends to")
    username: str | None = Field(None, description="Matterbridge: the name messages are sent under")
    secret_set: bool = Field(False, description="webhook: deliveries are signed")
    token_set: bool = Field(False, description="Matterbridge: an API token is sent")
    last: LastSend | None = Field(None, description="how the latest send went")
    by: str | None = None
    at: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None


class EventInfo(ResponseModel):
    type: EventType
    label: str


class NotifyTargets(ResponseModel):
    events: list[EventInfo]
    targets: list[NotifyTarget]
    enabled: bool = Field(description="notifications are on for the server (an admin's notifications.enabled)")


class NotifyTargetCreate(RequestModel):
    name: str = Field(min_length=1, max_length=80)
    kind: TargetKind
    url: str = Field(
        min_length=1,
        max_length=2000,
        description="where to POST: the webhook's URL, or the Matterbridge API's address (/api/message is added)",
    )
    events: list[EventType] | None = Field(None, description="default: job.failed and batch.finished")
    enabled: bool = True
    gateway: str | None = Field(None, max_length=100, description="Matterbridge: the [[gateway]] name to send to")
    username: str | None = Field(None, max_length=60, description="Matterbridge: the name to send as (default Lens)")
    token: str | None = Field(None, max_length=500, description="Matterbridge: the API token ([api] Token), if it has one")


class NotifyTargetUpdate(RequestModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    url: str | None = Field(None, max_length=2000, description="a new address, in full; leave it out to keep the one saved")
    events: list[EventType] | None = None
    enabled: bool | None = None
    gateway: str | None = Field(None, max_length=100)
    username: str | None = Field(None, max_length=60)
    token: str | None = Field(None, max_length=500, description='Matterbridge: a new token; "" removes it')


class NotifyTargetCreated(ResponseModel):
    target: NotifyTarget
    secret: str | None = Field(None, description="webhook: the signing secret, shown this once")


class NotifySecret(ResponseModel):
    secret: str = Field(description="the webhook's new signing secret, shown this once")


class NotifyTestResult(ResponseModel):
    ok: bool
    code: int | None = None
    error: str | None = None


class NotifyDelivery(ResponseModel):
    id: str
    event: str
    status: Literal["pending", "sending", "sent", "failed", "dropped"]
    attempts: int = 0
    code: int | None = None
    error: str | None = None
    text: str | None = Field(None, description="the message, as chat targets get it")
    created_at: str
    sent_at: str | None = None
    next_at: str | None = Field(None, description="when it's tried again, while pending")
