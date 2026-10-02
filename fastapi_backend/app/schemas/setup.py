from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class SetupAdmin(ResponseModel):
    from_env: bool


class SetupNamespaceView(ResponseModel):
    existing: list[str]
    locked: bool


class SetupLlmView(ResponseModel):
    values: dict[str, Any]
    locked: list[str]


class SetupStorageView(ResponseModel):
    data_dir: str
    database: str
    embedded: bool
    local_roots: list[str]
    max_upload_mb: int
    watches: int


class SetupView(ResponseModel):
    pending: bool
    admin: SetupAdmin
    namespace: SetupNamespaceView
    llm: SetupLlmView
    storage: SetupStorageView


class SetupNamespace(RequestModel):
    name: str = Field(min_length=1, max_length=41)
    graph: Literal["shared", "isolated"] = "shared"


class SetupLlm(RequestModel):
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    # empty: leave the saved key as it is
    api_key: str | None = Field(default=None, max_length=2000)


class SetupStorage(RequestModel):
    max_upload_mb: int | None = None
    # a folder inside sources.local_roots to watch into `namespace`
    folder: str | None = Field(default=None, max_length=4096)
    namespace: str | None = None


class SetupFinish(RequestModel):
    skipped: bool = False


class SetupSaved(ResponseModel):
    ok: bool = True
    saved: list[str] = []
    watch: int | None = None
