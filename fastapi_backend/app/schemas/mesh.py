"""The mesh between Lens's machines (docs/mesh.md): its status, the machines on it and the tokens they join with."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel
from app.schemas.jobs import WorkerInfo

NodeRole = Literal["worker", "compute"]


class MeshAuthority(ResponseModel):
    fingerprint: str = Field(description="the CA certificate's fingerprint: every machine on the mesh trusts it")
    not_after: str | None = Field(None, description="when the CA runs out")
    created_at: str | None = None


class MeshRelay(ResponseModel):
    port: int = Field(description="the port on this machine's mesh address")
    to: str = Field(description="the container it passes connections to")
    listening: bool
    open: int = Field(0, description="connections through it now")
    error: str | None = None


class MeshService(ResponseModel):
    available: bool = Field(description="this install runs the primary's Nebula in the `mesh` container (Docker installs)")
    state: str | None = Field(None, description="off, waiting (for its key to be signed), starting, running or error")
    nebula: bool = Field(False, description="Nebula runs on this machine now")
    since: str | None = Field(None, description="when Nebula last started")
    restarts: int = 0
    relays: list[MeshRelay] = Field(default_factory=list, description="what the mesh reaches on this machine")
    error: str | None = None
    log: list[str] = Field(default_factory=list, description="Nebula's last lines")
    seen_at: str | None = Field(None, description="when the container last said how it runs")
    manual: str = Field(description="the line that starts the container on this machine, where there's no upgrade agent")


class MeshStatus(ResponseModel):
    enabled: bool = Field(description="the mesh is on (mesh.enabled): adding a node turns it on")
    network: str = Field(description="the mesh's addresses (mesh.network)")
    port: int = Field(description="the UDP port other machines reach the primary at (mesh.port)")
    hosts: list[str] = Field(description="where other machines reach that port (mesh.hosts); empty: where each joined")
    primary_ip: str = Field(description="this machine's address on the mesh")
    ca: MeshAuthority | None = Field(None, description="the mesh's certificate authority, once made")
    nodes: int = Field(description="machines on the mesh besides this one")
    removed: int = Field(description="machines taken off it")
    tokens: int = Field(description="join tokens that can still be used")
    nebula: str = Field(description="the Nebula version Lens runs")
    tools: bool = Field(description="Nebula's programs are on this server (else they're fetched when first needed)")
    service: MeshService = Field(description="Nebula on this machine, in the `mesh` container")


class MeshNode(ResponseModel):
    id: int
    name: str
    role: Literal["primary", "worker", "compute"]
    ip: str = Field(description="its address on the mesh")
    groups: list[str] = Field(default_factory=list, description="its certificate's groups: role and namespaces, for the firewall")
    namespaces: list[str] = Field(default_factory=list, description="the namespaces it serves (none: every one)")
    fingerprint: str | None = Field(None, description="its current certificate's fingerprint")
    not_after: str | None = Field(None, description="when its certificate runs out (it renews it at two thirds)")
    joined_at: str | None = None
    joined_by: str | None = Field(None, description="the admin who made the token it joined with")
    last_seen: str | None = Field(None, description="its last report, every minute while it runs")
    status: dict[str, Any] = Field(default_factory=dict, description="what it last reported: Nebula, its peers, its machine")
    revoked_at: str | None = Field(None, description="when it was taken off the mesh")
    revoked_by: str | None = None
    hosts: list[str] = Field(default_factory=list, description="the address it reached this install at to join")
    workers: list[WorkerInfo] = Field(default_factory=list, description="the job workers running on it")


class MeshNodeChange(RequestModel):
    name: str | None = Field(None, max_length=63)
    namespaces: list[str] | None = Field(None, max_length=100, description="the namespaces it serves (empty: every one)")


class MeshToken(ResponseModel):
    id: int
    role: NodeRole
    name: str | None = None
    namespaces: list[str] = Field(default_factory=list)
    expires_at: str | None = None
    uses: int = Field(1, description="how many machines may join with it")
    used: int = Field(0, description="how many have")
    expired: bool = False
    created_by: str | None = None
    created_at: str | None = None
    last_used_at: str | None = None


class MeshTokenCreate(RequestModel):
    role: NodeRole = Field("worker", description="worker: runs jobs against this install's database; compute: runs sandboxes only")
    name: str | None = Field(None, max_length=63, description="the name it shows as (none: the name the machine sends)")
    namespaces: list[str] = Field(default_factory=list, max_length=100, description="the namespaces it serves (empty: every one)")
    hours: int = Field(24, description="how long the token can be used")
    uses: int = Field(1, description="how many machines may join with it")


class MeshTokenCreated(ResponseModel):
    token: MeshToken
    secret: str = Field(description="the join token itself, shown this once")
    join_url: str = Field(description="this install's address the machine joins at")


class MeshJoin(RequestModel):
    token_id: int = Field(description="the number in the join token (lensjoin1.<id>.<secret>)")
    nonce: str = Field(min_length=16, max_length=64, description="random, made for this request")
    name: str = Field(min_length=1, max_length=63, description="the machine's name")
    public_key: str = Field(max_length=500, description="its Nebula public key (nebula-cert keygen): its private key never leaves it")
    proof: str = Field(max_length=128, description="HMAC-SHA256 of the request, keyed with the token's secret (docs/mesh.md)")
    info: dict[str, Any] | None = Field(None, description="about the machine: os, arch, cpus, memory")


class MeshJoined(ResponseModel):
    node: int
    sealed: str = Field(description="the answer, sealed with a key from the token's secret: certificate, config, credentials")


class MeshNodeConfig(ResponseModel):
    config: dict[str, Any] = Field(description="its Nebula config, without its private key")
    version: str = Field(description="changes whenever the config does")
    not_after: str | None = None
    renew_days: int = Field(description="how many days before its certificate runs out it renews it")
    crt: str | None = Field(None, description="the new certificate (renew)")


class MeshRenew(RequestModel):
    public_key: str = Field(max_length=500, description="its Nebula public key, the same or a new one")


class MeshBeat(RequestModel):
    nebula: str | None = Field(None, max_length=40, description="running, starting, stopped or failed")
    version: str | None = Field(None, max_length=40, description="the Lens version it runs")
    peers: list[dict[str, Any]] | None = Field(None, max_length=500, description="the machines it has tunnels to")
    machine: dict[str, Any] | None = Field(None, description="its processors, memory and system")
    sandbox: dict[str, Any] | None = Field(None, description="the sandboxes it can run")
    error: str | None = Field(None, max_length=500)
    log: list[str] | None = Field(None, max_length=40, description="Nebula's last lines")
