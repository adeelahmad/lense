"""The mesh between Lens's machines (docs/mesh.md): the network and its machines for admins (Settings › Network and
Nodes), joining it for a new machine (with a join token), and the calls a machine on it makes (with its node token).

Adding a node makes a join token and turns the mesh on. Making and withdrawing tokens, joining, changing and removing
nodes are audited.
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.api.deps import AdminReader, AdminWriter, Cfg, Db, client_ip, domain_errors, web_origin
from app.api.v1.routes.jobs import _worker as worker_info
from app.domain import auth, jobs, keyring, mesh, settings
from app.schemas.common import Ok
from app.schemas.mesh import (
    MeshBeat,
    MeshJoin,
    MeshJoined,
    MeshNode,
    MeshNodeChange,
    MeshNodeConfig,
    MeshRenew,
    MeshStatus,
    MeshToken,
    MeshTokenCreate,
    MeshTokenCreated,
    MeshWorkerAccess,
)

router = APIRouter(prefix="/mesh", tags=["mesh"])


@router.get("")
def mesh_status(user: AdminReader, db: Db, cfg: Cfg) -> MeshStatus:
    """Whether the mesh is on, its network and port, where other machines reach it, its CA and how many machines are
    on it."""
    return MeshStatus(**mesh.status(db, cfg))


def _with_workers(db, rows) -> list[MeshNode]:
    """Each machine with the job workers running on it: a node's report as theirs, the rest (this machine's, and any
    started by hand against the database) as the primary's."""
    by_node: dict[Any, list] = {}
    for w in jobs.workers(db):
        by_node.setdefault(w.get("node"), []).append(w)
    ids = {n["id"] for n in rows}
    out = []
    for n in rows:
        mine = by_node.get(n["id"], [])
        if n["role"] == "primary":
            mine = mine + [w for k, ws in by_node.items() if k not in ids for w in ws]
        out.append(MeshNode(**n, workers=[worker_info(w) for w in mine]))
    return out


@router.get("/nodes")
def mesh_nodes(user: AdminReader, db: Db) -> list[MeshNode]:
    """Every machine: this one (the primary) first, then each node with its role, address, certificate, last report
    and the workers running on it. Removed nodes are listed too, with when."""
    rows = mesh.nodes(db)
    if not rows:  # no mesh yet: this machine and its workers
        rows = [{"id": 0, "name": "This machine", "role": "primary", "ip": "", "groups": [], "namespaces": []}]
    return _with_workers(db, rows)


@router.patch("/nodes/{node_id}")
def change_node(node_id: int, body: MeshNodeChange, user: AdminWriter, db: Db) -> MeshNode:
    """Rename a node, or change the namespaces it serves (its certificate's groups follow when it next renews)."""
    with domain_errors():
        n = mesh.update(db, node_id, name=body.name, namespaces=body.namespaces)
    auth.audit(db, user.as_audit(), "mesh.node.change", f"mesh_node:{node_id}", body.model_dump(exclude_none=True))
    return _with_workers(db, [n])[0]


@router.delete("/nodes/{node_id}")
def remove_node(node_id: int, user: AdminWriter, db: Db) -> MeshNode:
    """Take a node off the mesh: its certificates go on every machine's blocklist and its credentials stop working.
    POST /mesh/nodes/{id}/restore undoes it while its certificate is good."""
    with domain_errors():
        n = mesh.remove(db, node_id, by=user.email)
    auth.audit(db, user.as_audit(), "mesh.node.remove", f"mesh_node:{node_id}")
    return _with_workers(db, [n])[0]


@router.post("/nodes/{node_id}/restore")
def restore_node(node_id: int, user: AdminWriter, db: Db, cfg: Cfg) -> MeshNode:
    """Undo removing a node."""
    with domain_errors():
        n = mesh.undo_remove(db, node_id, cfg)
    auth.audit(db, user.as_audit(), "mesh.node.restore", f"mesh_node:{node_id}")
    return _with_workers(db, [n])[0]


@router.get("/tokens")
def mesh_tokens(user: AdminReader, db: Db) -> list[MeshToken]:
    """Join tokens that can still be used (their secrets are shown only when made)."""
    return [MeshToken(**t) for t in mesh.tokens(db)]


@router.post("/tokens")
def make_mesh_token(body: MeshTokenCreate, user: AdminWriter, request: Request, db: Db, cfg: Cfg) -> MeshTokenCreated:
    """Add a node: a token another machine joins the mesh with, shown this once. Makes the mesh's CA the first time,
    and turns the mesh on."""
    with domain_errors():
        try:
            tok, secret = mesh.make_token(db, cfg, user.email, body.role, body.name, body.namespaces, body.hours, body.uses)
        except mesh.MeshError as e:
            raise HTTPException(503, str(e)) from None
        if not mesh.conf(cfg)["enabled"]:
            settings.save(db, request.app.state.archive.base, "mesh", {"enabled": True}, user.email)
            auth.audit(db, user.as_audit(), "settings.save", "mesh", ["enabled"])
    auth.audit(db, user.as_audit(), "mesh.token.create", f"mesh_token:{tok['id']}", {"role": body.role, "uses": body.uses})
    url = web_origin(request)
    return MeshTokenCreated(token=MeshToken(**tok), secret=secret, join_url=url, install_line=mesh.join_line(cfg, secret, url))


@router.delete("/tokens/{token_id}")
def withdraw_mesh_token(token_id: int, user: AdminWriter, db: Db) -> Ok:
    """Withdraw a join token: no machine can join with it any more (those that did stay)."""
    with domain_errors():
        mesh.revoke_token(db, token_id)
    auth.audit(db, user.as_audit(), "mesh.token.withdraw", f"mesh_token:{token_id}")
    return Ok()


@router.post("/join")
def join_mesh(body: MeshJoin, request: Request, db: Db, cfg: Cfg) -> MeshJoined:
    """A machine joins the mesh with a join token, without sending it: the token's id, the machine's public key, and
    an HMAC of the request keyed with the token's secret. The answer is sealed with a key from that secret
    (docs/mesh.md, Joining). Too many wrong tries from one address are turned away for a while."""
    key = f"mesh-join:{client_ip(request)}"
    if auth.throttled(key):
        raise HTTPException(429, "too many tries; wait a few minutes")
    host = urllib.parse.urlsplit(web_origin(request)).hostname or ""
    try:
        nid, sealed = mesh.join(db, cfg, body.token_id, body.nonce, body.name, body.public_key, body.proof, host, body.info)
    except PermissionError as e:
        auth.hit(key)
        raise HTTPException(403, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except mesh.MeshError as e:
        raise HTTPException(503, str(e)) from None
    auth.audit(db, None, "mesh.join", f"mesh_node:{nid}", {"token": body.token_id, "name": body.name, "from": client_ip(request)})
    return MeshJoined(node=nid, sealed=sealed)


def _node(request: Request, db: Db) -> dict[str, Any]:
    """The node calling, from its node token (Authorization: Bearer lensnode1.<id>.<secret>)."""
    h = request.headers.get("authorization") or ""
    row = mesh.by_token(db, h[7:] if h.lower().startswith("bearer ") else "")
    if not row:
        raise HTTPException(401, "not a node on this install's mesh (or it was removed)")
    return row


Node = Annotated[dict[str, Any], Depends(_node)]


@router.get("/node/config")
def node_config(node: Node, db: Db, cfg: Cfg) -> MeshNodeConfig:
    """The calling node's Nebula config as it is now (the blocklist, the lighthouse's addresses): it reloads when the
    version changes."""
    with domain_errors():
        return MeshNodeConfig(**mesh.config_for(db, cfg, node))


@router.post("/node/renew")
def renew_node(body: MeshRenew, node: Node, db: Db, cfg: Cfg) -> MeshNodeConfig:
    """A new certificate for the calling node's public key, with the groups it has now."""
    try:
        return MeshNodeConfig(**mesh.renew(db, cfg, node, body.public_key))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except mesh.MeshError as e:
        raise HTTPException(503, str(e)) from None


@router.get("/node/worker")
def node_worker(node: Node, db: Db, cfg: Cfg) -> MeshWorkerAccess:
    """What the calling worker node's `lens worker` signs in to the database with: its own user of it. 403 for a node
    of another role, 409 where the database is embedded."""
    try:
        return MeshWorkerAccess(**mesh.worker_access(db, cfg, node))
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    except mesh.MeshError as e:
        raise HTTPException(409, str(e)) from None


@router.get("/node/media/{recording_id}", response_class=StreamingResponse)
def node_media(recording_id: int, node: Node, request: Request, db: Db, cfg: Cfg) -> Response:
    """A recording's file, plain, for a job on the calling worker node (a vault's never). Its ETag is its size and
    time: a node that has it sends If-None-Match and gets 304."""
    try:
        path = mesh.media(db, cfg, node, recording_id)
        st = os.stat(path)
        tag = f'"{st.st_size}-{st.st_mtime_ns}"'
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers={"ETag": tag})
        f = keyring.open_plain(db, cfg, path)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from None
    except keyring.Locked:
        raise HTTPException(423, "its namespace is a locked vault") from None

    def chunks():
        with f:
            while block := f.read(1 << 20):
                yield block

    return StreamingResponse(chunks(), media_type="application/octet-stream", headers={"ETag": tag})


@router.post("/node/beat")
def node_beat(body: MeshBeat, node: Node, db: Db) -> Ok:
    """The calling node's report, every minute: Nebula, its peers, its machine."""
    mesh.beat(db, node, body.model_dump(exclude_none=True))
    return Ok()
