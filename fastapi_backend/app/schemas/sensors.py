"""Sensors: everything that feeds Lens. File sensors are the storage sources; stream sensors are MQTT devices, syslog
senders and webhooks, whose readings are kept with retention (admins)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

StreamType = Literal["mqtt", "syslog", "webhook", "bridge"]
SensorStatus = Literal["new", "active", "paused", "ignored"]
StoreMode = Literal["all", "changes", "summary", "none"]


class SensorType(ResponseModel):
    label: str
    family: Literal["files", "stream"]
    fields: dict[str, str] = Field(description="parameters and their defaults")
    secrets: list[str]
    help: str | None = None


class Handling(ResponseModel):
    store: StoreMode = Field("all", description="all readings, only changes, hourly summaries only, or none (counted and dropped)")
    raw_days: int | None = Field(None, description="days readings are kept; none: for good")
    rollup_days: int | None = Field(None, description="days hourly summaries are kept; none: for good")
    important_days: int | None = Field(None, description="days log lines of warning or worse are kept, when longer than raw_days")
    max_per_minute: int | None = Field(None, description="readings a stream may send a minute; more are dropped")
    triage: bool = Field(False, description="the decision model labels new log patterns routine, notable or alert")
    digest: bool = Field(False, description="a daily digest becomes a document in the sensor's namespace")


class HandlingChange(RequestModel):
    """A setting left out stays as it is; null goes back to the settings' default; 0 days keeps them for good."""

    store: StoreMode | None = None
    raw_days: int | None = None
    rollup_days: int | None = None
    important_days: int | None = None
    max_per_minute: int | None = None
    triage: bool | None = None
    digest: bool | None = None


class Suggestion(ResponseModel):
    handling: dict[str, Any]
    reason: str


class Sensor(ResponseModel):
    id: int
    name: str
    type: str
    label: str
    family: Literal["files", "stream"]
    params: dict[str, Any] = {}
    status: SensorStatus = "active"
    space: int | None = Field(None, description="the namespace a stream sensor's data belongs to")
    namespace: str | None = None
    device: str | None = None
    handling: Handling | None = None
    own_handling: dict[str, Any] | None = Field(None, description="what this sensor sets itself; the rest comes from the settings")
    suggested: Suggestion | None = Field(None, description="for a new stream sensor: how it might be handled")
    channels: int = Field(0, description="watched folders of a file sensor, streams of a stream sensor")
    readings: int = 0
    has_token: bool = False
    secrets: dict[str, dict[str, bool]] | None = Field(None, description="which secrets are set (a bridge's password)")
    health: dict[str, Any] | None = None
    last_seen_at: str | None = None
    created_at: str | None = None
    created_by: str | None = None


class Stream(ResponseModel):
    id: str
    name: str
    kind: str | None = Field(None, description="number, boolean, json, text or log")
    fields: list[str] | None = Field(None, description="the numbers a JSON stream carries")
    last_value: float | None = None
    last_text: str | None = None
    last_at: str | None = None
    count: int = 0
    stored: int = 0
    dropped: int = 0
    created_at: str | None = None


class SensorDetail(Sensor):
    streams: list[Stream] = []
    watches: list[dict[str, Any]] = []


class HubStatus(ResponseModel):
    enabled: bool
    mqtt: bool
    mqtt_port: int | None = None
    syslog: bool
    syslog_port: int | None = None
    processes: list[dict[str, Any]] = Field(description="the processes running the hub now, and what they listen on")
    new: int = Field(description="sensors found that nobody has looked at yet")


class SensorCatalog(ResponseModel):
    sensors: list[Sensor]
    types: dict[str, SensorType]
    hub: HubStatus


class SensorCreate(RequestModel):
    type: StreamType
    name: str | None = None
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="mqtt: {prefix}; syslog: {address}; webhook: none; bridge: {host, port, tls, topics, user}",
    )
    secrets: dict[str, str] | None = Field(None, description="bridge: {pass}")
    space: int | None = None
    handling: HandlingChange | None = None


class SensorCreated(ResponseModel):
    id: int
    token: str | None = Field(None, description="a webhook's token, shown this once")


class SensorUpdate(RequestModel):
    name: str | None = None
    status: SensorStatus | None = None
    space: int | None = Field(None, description="the namespace; null takes it out of any")
    handling: HandlingChange | None = None
    params: dict[str, Any] | None = Field(None, description="a bridge's connection")
    secrets: dict[str, str | None] | None = Field(None, description="a bridge's password; empty or null removes it")


class SensorToken(ResponseModel):
    token: str


class Reviewed(ResponseModel):
    applied: int


class Reading(ResponseModel):
    id: str
    stream: str
    stream_name: str | None = None
    at: str
    value: float | None = None
    text: str | None = None
    fields: dict[str, float] | None = None
    level: int | None = Field(None, description="syslog severity: 0 emergency to 7 debug")


class SeriesPoint(ResponseModel):
    hour: str
    n: int
    min: float | None = None
    max: float | None = None
    avg: float | None = None
    last: float | None = None


class Pushed(ResponseModel):
    kept: int


class HubLogin(ResponseModel):
    id: int
    username: str
    space: int | None = None
    namespace: str | None = None
    enabled: bool = True
    created_at: str | None = None
    last_seen_at: str | None = None


class HubLoginCreate(RequestModel):
    username: str
    password: str
    space: int | None = Field(None, description="the namespace new devices that sign in with it go to")


class Pattern(ResponseModel):
    id: str
    stream: str
    stream_name: str | None = None
    template: str = Field(description="the line with what changes taken out: <ip>, <name>, <n>...")
    example: str | None = None
    count: int = 0
    first_at: str | None = None
    last_at: str | None = None
    label: Literal["routine", "notable", "alert"] | None = None
    label_by: str | None = Field(None, description="jev, llm, or who set it")
    confidence: float | None = None
    sure: bool | None = Field(None, description="false: the model wasn't sure; a person should look")
    action: Literal["keep", "drop"] = "keep"


class PatternUpdate(RequestModel):
    label: Literal["routine", "notable", "alert"] | None = Field(None, description="null clears it")
    action: Literal["keep", "drop"] | None = None
