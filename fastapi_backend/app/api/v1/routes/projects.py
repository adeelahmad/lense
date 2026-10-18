"""Projects (projects.py, docs/projects.md): a goal, the namespaces it draws on, its coordinator conversation and its
threads. A project is its owner's alone, like their conversations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Acl, Cfg, CurrentUser, Db, Principal, Writer
from app.domain import projects
from app.domain.store import DB
from app.schemas.common import Created, Ok
from app.schemas.projects import (
    Project,
    ProjectCreate,
    ProjectSchedule,
    ProjectScheduleCreate,
    ProjectScheduleUpdate,
    ProjectSummary,
    ProjectThread,
    ProjectUpdate,
    ThreadCreate,
    ThreadUpdate,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def own(db: DB, pid: int, user: Principal) -> dict[str, Any]:
    try:
        return projects.get(db, pid, user.id)
    except KeyError:
        raise HTTPException(404, "not found") from None


def spaces(acl: Acl, names: list[str]) -> list[int]:
    """The namespaces' ids; each one the caller can read (404 otherwise)."""
    return [acl.namespace(n) for n in dict.fromkeys(n.strip() for n in names if n.strip())]


def summary(db: DB, p: dict[str, Any], threads: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    threads = projects.threads(db, p) if threads is None else threads
    return {
        **p,
        "namespaces": projects.namespaces(db, p),
        "threads": len(threads),
        "open": sum(1 for t in threads if not t["done"]),
    }


def view(db: DB, p: dict[str, Any]) -> Project:
    projects.coordinator(db, p)  # made again if its conversation was deleted
    threads = projects.threads(db, p)
    return Project.model_validate({**summary(db, p, threads), "thread_list": threads, "schedules": projects.schedules(db, p)})


@router.get("")
def list_projects(
    user: CurrentUser, db: Db, archived: bool | None = Query(False, description="true: only archived ones; left out: every one")
) -> list[ProjectSummary]:
    """Your projects, the most recently changed first (not archived ones, unless asked)."""
    return [ProjectSummary.model_validate(summary(db, p)) for p in projects.mine(db, user.id, archived)]


@router.post("")
def create_project(body: ProjectCreate, user: Writer, acl: Acl, db: Db) -> Project:
    """A project: its name, goal, instructions and the namespaces it draws on, with its coordinator conversation."""
    try:
        pid = projects.create(db, user.id, body.name, body.goal, spaces(acl, body.namespaces), body.instructions)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return view(db, projects.get(db, pid, user.id))


@router.get("/{pid}")
def get_project(pid: int, user: CurrentUser, db: Db) -> Project:
    """A project with its threads, the most recently added to first."""
    return view(db, own(db, pid, user))


@router.patch("/{pid}")
def update_project(pid: int, body: ProjectUpdate, user: Writer, acl: Acl, db: Db) -> Project:
    """Rename it, change its goal, instructions or namespaces (its conversations draw on the new ones), or archive it."""
    own(db, pid, user)
    try:
        p = projects.update(
            db,
            pid,
            user.id,
            body.name,
            body.goal,
            body.instructions,
            spaces(acl, body.namespaces) if body.namespaces is not None else None,
            body.archived,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return view(db, p)


@router.delete("/{pid}")
def delete_project(pid: int, user: Writer, db: Db) -> Ok:
    """Delete a project. Its conversations stay, as ordinary ones in Chat."""
    own(db, pid, user)
    projects.delete(db, pid, user.id)
    return Ok()


@router.get("/{pid}/threads")
def list_threads(pid: int, user: CurrentUser, db: Db) -> list[ProjectThread]:
    """The project's threads, the most recently added to first, the coordinator among them."""
    return [ProjectThread.model_validate(t) for t in projects.threads(db, own(db, pid, user), coordinator_too=True)]


@router.post("/{pid}/threads")
def start_thread(pid: int, user: Writer, db: Db, cfg: Cfg, body: ThreadCreate | None = None) -> Created:
    """A new thread: a conversation in the project's namespaces (ask in it with POST /chats/{id}/messages). With a
    `brief`, it is asked there at once and answered in the background, as you, with your tools and approvals: GET
    /chats/{id} shows the answer once it's written (the thread is `answering` until then). With `chat`, one of your
    conversations joins the project instead, drawing on its namespaces from then on (with a brief, asked there next)."""
    body = body or ThreadCreate()
    p = own(db, pid, user)
    try:
        cid = body.chat if body.chat is not None else projects.start_thread(db, p, body.title)
        if body.chat is not None:
            projects.adopt(db, p, body.chat)
        if body.brief and body.brief.strip():
            projects.brief(db, cfg, p, cid, body.brief, via="web" if user.via == "access" else "api")
        return Created(id=cid)
    except KeyError:
        raise HTTPException(404, "no such conversation of yours") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.patch("/{pid}/threads/{cid}")
def update_thread(pid: int, cid: int, body: ThreadUpdate, user: Writer, db: Db) -> Ok:
    """Mark a thread done (it stays to read and to go on with), or not done."""
    try:
        projects.set_done(db, own(db, pid, user), cid, body.done)
    except KeyError:
        raise HTTPException(404, "no such thread in this project") from None
    return Ok()


@router.delete("/{pid}/threads/{cid}")
def release_thread(pid: int, cid: int, user: Writer, db: Db) -> Ok:
    """Take a thread out of the project: the conversation stays in Chat (DELETE /chats/{id} deletes it)."""
    try:
        projects.release(db, own(db, pid, user), cid)
    except KeyError:
        raise HTTPException(404, "no such thread in this project") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return Ok()


def _one(db: DB, p: dict[str, Any], sid: str) -> ProjectSchedule:
    return ProjectSchedule.model_validate(next(s for s in projects.schedules(db, p) if s["id"] == sid))


@router.post("/{pid}/schedules")
def add_schedule(pid: int, body: ProjectScheduleCreate, user: Writer, db: Db) -> ProjectSchedule:
    """Ask a prompt on a schedule: each time, in a new thread or always the same one, answered in the background as
    you, with your tools, approvals and budgets."""
    p = own(db, pid, user)
    try:
        sc = projects.add_schedule(db, p, body.prompt, body.cron, body.timezone, body.thread)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return _one(db, p, sc["id"])


@router.patch("/{pid}/schedules/{sid}")
def update_schedule(pid: int, sid: str, body: ProjectScheduleUpdate, user: Writer, db: Db) -> ProjectSchedule:
    """Change a schedule, or switch it off or on."""
    p = own(db, pid, user)
    try:
        projects.update_schedule(db, p, sid, body.prompt, body.cron, body.timezone, body.thread, body.on)
    except KeyError:
        raise HTTPException(404, "no such schedule in this project") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return _one(db, p, sid)


@router.delete("/{pid}/schedules/{sid}")
def remove_schedule(pid: int, sid: str, user: Writer, db: Db) -> Ok:
    """Remove a schedule. The threads it started stay."""
    try:
        projects.remove_schedule(db, own(db, pid, user), sid)
    except KeyError:
        raise HTTPException(404, "no such schedule in this project") from None
    return Ok()
