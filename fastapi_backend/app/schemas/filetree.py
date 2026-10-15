"""Assets as folders and files (docs/file-tree.md)."""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class TreeEntry(ResponseModel):
    name: str
    folder: bool
    modified: str | None = None
    size: int | None = Field(None, description="a file's size in bytes")
    content_type: str | None = None


class TreeFolder(ResponseModel):
    path: str = Field(description="the folder, from the top: '' for the namespaces")
    entries: list[TreeEntry]


class S3Key(ResponseModel):
    id: str = Field(description="the access key id")
    name: str
    created_at: str | None = None
    expires_at: str | None = None
    last_used_at: str | None = None
    email: str | None = Field(None, description="its owner (the list of everyone's keys)")


class S3KeyCreate(RequestModel):
    name: str = Field("S3 key", max_length=80)
    days: int | None = Field(None, ge=0, description="how long it lasts (the API keys' limits); none: their default")


class S3KeyCreated(ResponseModel):
    id: str = Field(description="the access key id")
    secret: str = Field(description="the secret access key, shown this once")
    port: int = Field(description="the S3 server's port")
    webdav_port: int


class S3Service(ResponseModel):
    process: str | None = None
    at: str | None = None
    running: bool | None = None
    port: int | None = None
    since: str | None = None
    error: str | None = None


class ShareServer(ResponseModel):
    enabled: bool = Field(description="whether an admin turned it on (shares.enabled and its own switch)")
    port: int = Field(description="the port it listens on in the worker (Docker publishes LENS_S3_PORT, LENS_WEBDAV_PORT)")
    service: S3Service | None = Field(None, description="what the process running it says; none while it's off")
    add_files: bool = Field(False, description="whether files people add through it are imported (shares.add_files)")


class S3Status(ShareServer):
    """The S3 server (these fields), the WebDAV server, and your access keys (they sign in to both)."""

    webdav: ShareServer
    keys: list[S3Key] = Field(description="your access keys")
