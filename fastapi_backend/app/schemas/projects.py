from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class ThreadLast(ResponseModel):
    role: str = Field(description="user (a question) or assistant (an answer)")
    text: str = Field(description="its first 300 characters")
    at: str | None = None


class ProjectThread(ResponseModel):
    id: int = Field(description="the conversation (GET /chats/{id}, POST /chats/{id}/messages)")
    title: str
    title_by: str | None = None
    coordinator: bool = Field(False, description="the project's own conversation, about the project as a whole")
    done: bool = Field(False, description="marked done: it stays to read and to go on with")
    answering: bool = Field(False, description="an answer is being written in it now")
    messages: int = Field(0, description="how many messages it has, every version's")
    last: ThreadLast | None = Field(None, description="its last message, of the version showing")
    created_at: str | None = None
    updated_at: str | None = None


class ProjectScheduleRun(ResponseModel):
    at: str | None = Field(None, description="the time it was for")
    ok: bool = True
    error: str | None = Field(None, description="why it didn't ask, or what went wrong")


class ProjectSchedule(ResponseModel):
    id: str
    prompt: str = Field(description="what it asks each time, as a brief")
    cron: str = Field(description="when: five cron fields (minute hour day-of-month month day-of-week)")
    timezone: str = "UTC"
    thread: str = Field("new", description="new: a new thread each time; same: always the same one")
    on: bool = True
    chat: int | None = Field(None, description="its thread, when it asks in the same one (made on its first run)")
    when: str = Field("", description="the schedule in words")
    next_at: str | None = Field(None, description="its next time (none while off or the project is archived)")
    last: ProjectScheduleRun | None = None
    created_at: str | None = None


class ProjectSummary(ResponseModel):
    id: int
    name: str
    goal: str = ""
    namespaces: list[str] = Field(default_factory=list, description="the namespaces it draws on, which its conversations are scoped to")
    coordinator: int | None = Field(None, description="the project's own conversation (a chat)")
    archived: bool = False
    threads: int = Field(0, description="how many threads it has, besides the coordinator")
    open: int = Field(0, description="how many of them aren't done")
    created_at: str | None = None
    updated_at: str | None = None


class Project(ProjectSummary):
    instructions: str = Field("", description="what its owner tells the assistant in every one of its conversations")
    thread_list: list[ProjectThread] = Field(default_factory=list, description="its threads, the most recently added to first")
    schedules: list[ProjectSchedule] = Field(default_factory=list, description="prompts it asks on a schedule")


class ProjectCreate(RequestModel):
    name: str = Field(max_length=120)
    goal: str = Field("", max_length=4000, description="what the project is for, in your words: every answer in it reads it")
    instructions: str = Field("", max_length=4000, description="how the assistant should work in it")
    namespaces: list[str] = Field(min_length=1, max_length=20, description="the namespaces it draws on (ones you can read)")


class ProjectUpdate(RequestModel):
    """Only the fields you send change."""

    name: str | None = Field(None, max_length=120)
    goal: str | None = Field(None, max_length=4000)
    instructions: str | None = Field(None, max_length=4000)
    namespaces: list[str] | None = Field(None, min_length=1, max_length=20, description="its conversations draw on these from now on")
    archived: bool | None = Field(None, description="archived projects are listed apart; nothing in them changes")


class ThreadCreate(RequestModel):
    title: str | None = Field(None, max_length=120, description="left out, Lens titles it after its first answer")
    chat: int | None = Field(None, description="bring in one of your conversations instead of starting a new one")
    brief: str | None = Field(
        None,
        max_length=4000,
        description="what to do: asked as the thread's first question and answered in the background, with your tools and "
        "approvals (follow it with GET /chats/{id})",
    )


class ThreadUpdate(RequestModel):
    done: bool = Field(description="mark it done, or not done")


class ProjectScheduleCreate(RequestModel):
    prompt: str = Field(min_length=1, max_length=4000, description="what to ask each time, answered in the background")
    cron: str = Field(max_length=200, description="when: five cron fields, like 0 8 * * 1-5 (8:00 on weekdays), or @daily")
    timezone: str = Field("UTC", max_length=64, description="the time zone the schedule is in, like Europe/London")
    thread: Literal["new", "same"] = Field("new", description="new: a new thread each time; same: always the same one")


class ProjectScheduleUpdate(RequestModel):
    """Only the fields you send change."""

    prompt: str | None = Field(None, min_length=1, max_length=4000)
    cron: str | None = Field(None, max_length=200)
    timezone: str | None = Field(None, max_length=64)
    thread: Literal["new", "same"] | None = None
    on: bool | None = Field(None, description="switched off, it waits until it's switched on again")
