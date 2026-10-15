"""Topics: each namespace's controlled vocabulary (docs/topics.md), and which recordings are about which topic.

Reads cover the namespaces you have a role in; changes need the editor role there. Each change drops the cached
knowledge graph, which shows topics too.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import Access, Acl, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, topics
from app.domain.store import DB, R
from app.schemas.common import Ok
from app.schemas.topics import (
    RecordingTopic,
    TopicCandidate,
    TopicChange,
    TopicCreate,
    TopicDeleted,
    TopicDetail,
    TopicList,
    TopicMerge,
    TopicMerged,
    TopicRecordings,
    TopicSkip,
    TopicUpdate,
)

router = APIRouter(tags=["topics"])


def _topic_space(db: DB, acl: Access, tid: int, role: str = "viewer") -> int:
    row = db.one("SELECT space FROM $r", r=R("topic", int(tid)))
    if not row:
        raise HTTPException(404, "not found")
    acl.need(row["space"], role)
    return row["space"]


def _changed(request: Request) -> None:
    request.app.state.graph_cache.clear()


def _detail(db: DB, acl: Access, tid: int) -> TopicDetail:
    return TopicDetail.model_validate(topics.detail(db, tid))


@router.get("/topics")
def list_topics(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    q: str = "",
    ns: str = "",
    top: bool = False,
    broader: int | None = None,
    limit: int = 200,
    offset: int = 0,
) -> TopicList:
    """Topics of the namespaces you can read (or of `ns`), by label. `q` matches labels and other labels, `top` keeps
    those with no broader topic, `broader` lists the narrower topics of one."""
    spaces = set(acl.roles)
    if ns:
        sid = acl.nsid(ns)
        acl.need(sid)
        spaces = {sid}
    if broader is not None:
        _topic_space(db, acl, broader)
    return TopicList.model_validate(topics.list_topics(db, spaces, q, top, broader, limit, offset))


@router.get("/topics/{tid}")
def get_topic(tid: int, user: CurrentUser, acl: Acl, db: Db) -> TopicDetail:
    """A topic with its broader, narrower and related topics and the assets about it."""
    _topic_space(db, acl, tid)
    return _detail(db, acl, tid)


@router.post("/namespaces/{name}/topics", status_code=201)
def create_topic(name: str, body: TopicCreate, request: Request, user: Writer, acl: Acl, db: Db) -> TopicDetail:
    """Add a topic to the namespace's vocabulary."""
    sid = acl.nsid(name)
    acl.need(sid, "curate")
    with domain_errors():
        tid = topics.create(db, sid, body.label, body.alt, body.definition, body.broader, body.related, user=user.email)
    auth.audit(db, user.as_audit(), "topic.create", f"topic:{tid}", body.model_dump())
    _changed(request)
    return _detail(db, acl, tid)


@router.get("/namespaces/{name}/topics/candidates")
def topic_candidates(name: str, user: CurrentUser, acl: Acl, db: Db, limit: int = 30) -> list[TopicCandidate]:
    """What the namespace's summaries say assets are about that no topic covers yet, the most assets first.
    Adding one as a topic suggests it for those assets; skipping one stops it being offered."""
    sid = acl.nsid(name)
    acl.need(sid)
    return [TopicCandidate(**c) for c in topics.candidates(db, sid, min(max(limit, 1), 100))]


@router.post("/namespaces/{name}/topics/candidates/skip")
def skip_topic_candidate(name: str, body: TopicSkip, user: Writer, acl: Acl, db: Db) -> Ok:
    """Stop offering a label as a new topic."""
    sid = acl.nsid(name)
    acl.need(sid, "curate")
    with domain_errors():
        topics.skip_candidate(db, sid, body.label)
    auth.audit(db, user.as_audit(), "topic.skip", f"namespace:{name}", {"label": body.label})
    return Ok()


@router.patch("/topics/{tid}")
def update_topic(tid: int, body: TopicUpdate, request: Request, user: Writer, acl: Acl, db: Db) -> TopicDetail:
    """Rename it, change its other labels or definition, or what it is narrower than or related to."""
    _topic_space(db, acl, tid, "curate")
    changes = body.model_dump(exclude_unset=True)
    with domain_errors():
        topics.update(
            db,
            tid,
            body.label,
            body.alt,
            body.definition or None,
            body.broader,
            body.related,
            user=user.email,
            clear_definition="definition" in changes and not body.definition,
        )
    auth.audit(db, user.as_audit(), "topic.update", f"topic:{tid}", changes)
    _changed(request)
    return _detail(db, acl, tid)


@router.delete("/topics/{tid}")
def delete_topic(tid: int, request: Request, user: Writer, acl: Acl, db: Db) -> TopicDeleted:
    """Delete it: its narrower topics move up, assets stop being about it, and an entity it was made from shows
    again. `undo` takes it back (POST /topics/changes/{undo}/undo)."""
    _topic_space(db, acl, tid, "curate")
    with domain_errors():
        eid, cid = topics.delete(db, tid, user=user.email)
    auth.audit(db, user.as_audit(), "topic.delete", f"topic:{tid}", {"entity": eid} if eid else None)
    _changed(request)
    return TopicDeleted(undo=cid)


@router.post("/topics/merge")
def merge_topics(body: TopicMerge, request: Request, user: Writer, acl: Acl, db: Db) -> TopicMerged:
    """Fold topics into one: their labels become its other labels and their assets are about it. `undo` takes it
    back (POST /topics/changes/{undo}/undo)."""
    for t in [body.keep, *body.others]:
        _topic_space(db, acl, t, "curate")
    with domain_errors():
        cid = topics.merge(db, body.keep, body.others, user=user.email)
    auth.audit(db, user.as_audit(), "topic.merge", f"topic:{body.keep}", {"others": body.others})
    _changed(request)
    return TopicMerged(**_detail(db, acl, body.keep).model_dump(), undo=cid)


@router.get("/namespaces/{name}/topics/changes")
def list_topic_changes(name: str, user: CurrentUser, acl: Acl, db: Db, limit: int = 50) -> list[TopicChange]:
    """The namespace's latest topic merges and deletes, newest first, each with whether it was undone."""
    sid = acl.nsid(name)
    acl.need(sid, "viewer")
    return [TopicChange(**c) for c in topics.changes(db, sid, min(max(limit, 1), 200))]


@router.post("/topics/changes/{cid}/undo")
def undo_topic_change(cid: int, request: Request, user: Writer, acl: Acl, db: Db) -> Ok:
    """Take back a merge or a delete: the topics come back with their labels, assets and pages. What changed
    since stays. Refused (400) when another topic has taken one of their labels meanwhile."""
    row = db.one("SELECT space FROM $r", r=R("topic_change", int(cid)))
    if not row:
        raise HTTPException(404, "not found")
    acl.need(row["space"], "curate")
    with domain_errors():
        out = topics.undo(db, cid, user=user.email)
    auth.audit(db, user.as_audit(), f"topic.{out['kind']}.undo", f"topic_change:{cid}", {"topics": out["topics"]})
    _changed(request)
    return Ok()


@router.post("/topics/{tid}/recordings")
def tag_recordings(tid: int, body: TopicRecordings, request: Request, user: Writer, acl: Acl, db: Db) -> TopicDetail:
    """Say assets of the topic's namespace are about it (accepting suggestions too), or with `remove` that they aren't."""
    _topic_space(db, acl, tid, "curate")
    with domain_errors():
        topics.tag(db, tid, body.recordings, body.remove, user=user.email)
    auth.audit(db, user.as_audit(), "topic.untag" if body.remove else "topic.tag", f"topic:{tid}", {"recordings": body.recordings})
    _changed(request)
    return _detail(db, acl, tid)


@router.get("/recordings/{rid}/topics")
def recording_topics(rid: int, user: CurrentUser, acl: Acl, db: Db) -> list[RecordingTopic]:
    """The topics an asset is about, and those suggested for it."""
    acl.recording(rid)
    return [RecordingTopic(**t) for t in topics.of_recording(db, rid)]


@router.post("/entities/{eid}/topic", status_code=201)
def entity_to_topic(eid: int, request: Request, user: Writer, acl: Acl, db: Db) -> TopicDetail:
    """Make a topic-like entity (type TERM) a topic: its names become the topic's labels and the assets that
    mention it are about the topic. The entity is hidden; deleting the topic shows it again."""
    row = db.one("SELECT space FROM $r", r=R("entity", int(eid)))
    if not row:
        raise HTTPException(404, "not found")
    acl.need(row["space"], "curate")
    with domain_errors():
        tid = topics.from_entity(db, eid, user=user.email)
    auth.audit(db, user.as_audit(), "topic.from_entity", f"topic:{tid}", {"entity": eid})
    _changed(request)
    return _detail(db, acl, tid)
