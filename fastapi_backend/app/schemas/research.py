"""Deep research: a goal's plan, its run in the background and its report (app/domain/research.py)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.chats import ChatScope, MessageRecord
from app.schemas.common import RequestModel, ResponseModel
from app.schemas.podcasts import Style

Source = Literal["archive", "graph", "notes", "web", "code"]


class ResearchQuestion(ResponseModel):
    question: str
    answers: list[str] = Field(default_factory=list, description="suggested answers to pick from")


class ResearchPlan(ResponseModel):
    title: str
    goal: str
    sub_questions: list[str] = Field(description="what it answers, one look-up at a time, in order")
    sources: list[Source] = Field(description="where it looks: the archive, the graph, notes, the web, the code sandbox")
    max_tool_calls: int = Field(description="the most tool calls the run makes in all (research.max_tool_calls at most)")
    report_outline: list[str] = Field(default_factory=list, description="the report's sections")


class ResearchEstimate(ResponseModel):
    usd: float | None = Field(None, description="what it will likely cost, from the prices in Settings; null without a price")
    tokens: int | None = None
    tool_calls: int | None = None
    basis: str | None = Field(None, description="what the estimate is drawn from")
    estimate: bool = True


class ResearchProgress(ResponseModel):
    sub_questions: int = 0
    done: int = Field(0, description="sub-questions answered")
    sources: int = Field(0, description="sources found so far")
    tool_calls: int = 0
    cost_usd: float = Field(0.0, description="spent so far (the activity ledger rows of research:<id>)")
    tokens: int = 0
    cost_estimated: bool = False
    minutes: float = Field(0.0, description="how long it has run")


class ResearchFinding(ResponseModel):
    question: str
    tool_calls: int = 0


class ResearchHeld(ResponseModel):
    why: str
    budget: str | None = Field(None, description="the budget it waits for (all, space:<id>)")


class Research(ResponseModel):
    id: int
    chat: int
    message: int | None = Field(None, description="the question that asked for it")
    plan_message: int | None = Field(None, description="the answer that shows the plan")
    answer: int | None = Field(None, description="the report, once saved in the conversation")
    approval: int | None = Field(
        None, description="the plan's approval (POST /approvals/{id}: approve, with `params` its edits, or decline)"
    )
    status: Literal["planned", "declined", "queued", "running", "held", "done", "stopped", "failed"] = Field(
        description="planned (waiting for approval), declined, queued (approved, waiting its turn), running, held (by a "
        "budget), done, stopped or failed"
    )
    stage: Literal["gather", "write", "check", "done"] | None = None
    goal: str | None = None
    plan: ResearchPlan
    questions: list[ResearchQuestion] = Field(default_factory=list, description="follow-up questions; answer them with revise")
    answers: dict[str, str] = Field(default_factory=dict, description="the answers given to them")
    scope: dict[str, Any] = Field(default_factory=dict)
    estimate: ResearchEstimate | None = None
    progress: ResearchProgress
    findings: list[ResearchFinding] = Field(default_factory=list, description="the sub-questions answered so far")
    held: ResearchHeld | None = None
    error: str | None = None
    ended: Literal["stopped", "timeout", "budget"] | None = Field(
        None, description="why it ended early: stopped, out of time (research.max_minutes), or a budget"
    )
    revisions: int = 0
    notes: list[int] = Field(default_factory=list, description="notes made from the report")
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class ResearchStart(RequestModel):
    goal: str = Field(min_length=1, max_length=4000, description="what to research")
    tools: list[str] | None = Field(None, max_length=100, description="what it may use (GET /chats/tools); null: the conversation's choice")
    max_tool_calls: int | None = Field(None, ge=1, description="the most tool calls the plan may make (research.max_tool_calls at most)")
    scope: ChatScope | None = Field(None, description="what it draws on, instead of the conversation's scope")
    review: bool = Field(
        True,
        description="false: start the run at once, without the plan waiting for review (signed in; an API key or an app "
        "only with research.agents_skip_review)",
    )


class ResearchStarted(ResponseModel):
    research: Research
    question: int = Field(description="the question saved in the conversation")
    message: int = Field(description="the answer that shows the plan")
    text: str = Field(description="the plan as the conversation shows it")
    meta: MessageRecord | None = None


class ResearchRevise(RequestModel):
    answers: dict[str, str] = Field(default_factory=dict, description="the follow-up questions' answers, by question")
    plan: dict[str, Any] | None = Field(
        None, description="the plan as edited: any of title, sub_questions, sources, max_tool_calls, report_outline"
    )


class ResearchNote(RequestModel):
    namespace: str | None = Field(None, description="where the note goes; needed when the research looked in several")


class ResearchNoteMade(ResponseModel):
    page: int
    namespace: str


class ResearchPodcast(RequestModel):
    prompt: str | None = Field(None, max_length=2000, description="an angle; else the research goal")
    length: int = Field(10, ge=1, le=120, description="minutes (up to podcasts.max_minutes)")
    style: Style = "deep-dive"
    title: str | None = Field(None, max_length=200, description="else the report's title")


class ResearchStop(ResponseModel):
    ok: bool = True
    stopping: bool = Field(description="it stops after the step it's on (or now, when it hadn't started or waited on a budget)")
