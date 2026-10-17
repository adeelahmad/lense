"""Conversations with the archive, the assistant's approvals and checking an answer against its sources."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.schemas.common import RequestModel, ResponseModel


class ChatScope(RequestModel):
    """What a conversation may draw on. Every field narrows it; leave them out to use everything you can read."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    namespaces: list[str] | None = None
    recordings: list[int] | None = None
    collections: list[int] | None = Field(
        default=None, description="saved collections (yours or shared): their assets as they are when it answers"
    )
    speakers: list[int] | None = None
    date_from: str | None = Field(default=None, alias="from", description="YYYY-MM-DD")
    date_to: str | None = Field(default=None, alias="to", description="YYYY-MM-DD")


class ChatCreate(RequestModel):
    title: str | None = None
    kind: Literal["chat", "setup"] = Field(
        "chat",
        description="setup (admins): the assistant sets the server up with you, and makes the changes it proposes without asking "
        "(telemetry still asks)",
    )
    scope: ChatScope | None = None
    model: str | None = Field(
        None, description="the model that answers in it (one of GET /chats/capabilities `models`); null: the configured one"
    )


class ChatUpdate(RequestModel):
    title: str | None = None
    scope: ChatScope | None = None
    model: str | None = Field(
        None, description="the model that answers in it (one of GET /chats/capabilities `models`); null: the configured one"
    )


class ChatSummary(ResponseModel):
    id: int
    title: str
    kind: Literal["chat", "setup", "bridge"] = Field("chat", description="bridge: a conversation in a chat room, through Matterbridge")
    scope: dict[str, Any] = {}
    model: str | None = Field(None, description="the model chosen for this conversation; null: the configured one")
    created_at: str | None = None
    updated_at: str | None = None


class Passage(ResponseModel):
    """A numbered excerpt an answer can cite: a moment of an asset, or a web page (`source` "web", with its `url`)."""

    n: int
    recording_id: int | None = Field(None, description="the asset it's from; none for a web page")
    source: str | None = Field(
        None, description="what it is: said (speech), page (a document's), screen (text shown in a video), memory, or web"
    )
    url: str | None = Field(None, description="a web page's address (source web): the citation opens it")
    site: str | None = Field(None, description="a web page's site, like example.com")
    fetched_at: str | None = Field(None, description="when the web page or search result was read")
    title: str | None = None
    namespace: str | None = None
    recorded_at: str | None = None
    t0: int | float | None = None
    time: str | None = Field(None, description='where it is: a time ("12:34"), or for a document its page ("p. 3")')
    page: int | None = Field(None, description="a document's or an image's page it's on (from 0)")
    speaker: str | None = None
    text: str
    used: bool | None = Field(default=None, description="cited in the answer")


class AnswerCheck(ResponseModel):
    claims: int = Field(description="sentences that cite a source")
    supported: int = Field(description="claims the cited excerpts support")
    verdicts: list[dict[str, Any]] = []
    uncited: list[str] = []


class ToolStep(ResponseModel):
    tool: str
    args: dict[str, Any] = {}
    summary: str = ""


class PageContext(RequestModel):
    """The page a question was asked from (chat on any page): where it is, and what of it the person shared."""

    url: str = Field(max_length=2000, pattern=r"^/([^/]|$)", description="the page's path in Lens, e.g. /resources/12?t=30")
    title: str | None = Field(None, max_length=300)
    text: str | None = Field(
        None,
        max_length=200_000,
        description="the page's visible text, when the person shares the page (only the first 12,000 characters are used)",
    )
    selection: str | None = Field(
        None, max_length=50_000, description="text the person highlighted on it (only the first 4,000 characters are used)"
    )


class SharedContext(ResponseModel):
    """What a question shared from the page it was asked on. The page's text itself isn't kept."""

    url: str
    title: str | None = None
    selection: str | None = None
    page: bool = Field(False, description="the page's text was shared")


class Attachment(ResponseModel):
    id: str = Field(description="the upload")
    filename: str
    size: int
    recording: int | None = Field(None, description="the asset it became, once imported")


class ChatMessage(ResponseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    context: SharedContext | None = Field(None, description="a question asked from a page: the page, and any highlighted text")
    attachments: list[Attachment] = Field(default_factory=list, description="files sent with it (POST /uploads with hold)")
    passages: list[Passage] | None = None
    created_at: str | None = None
    stopped: bool = Field(False, description="the answer was stopped (POST /chats/{cid}/stop): `content` is what came before")
    steps: list[ToolStep] = Field(default_factory=list, description="the tools the assistant used for this answer, in order")
    notice: str | None = Field(None, description="e.g. the model can't use tools, so the answer came from a search")
    error: str | None = Field(None, description="why there's no answer")
    check: AnswerCheck | None = Field(None, description="its latest source check (POST .../messages/{mid}/check)")
    model: str | None = Field(None, description="the model that wrote the answer")


class ChatCapabilities(ResponseModel):
    configured: bool = Field(description="a language model is set up; without one, answers are the best-matching passages")
    model: str | None = Field(None, description="the model answers come from")
    tools: bool = Field(description="the assistant looks things up with tools (and may propose work for approval)")
    max_steps: int = Field(description="the most tool calls it makes for one answer")
    check: bool = Field(description="answers can be checked against their sources")
    models: list[str] = Field(
        default_factory=list, description="the models people may pick, the configured one first (llm.chat_models, else the server's list)"
    )


class StopResult(ResponseModel):
    ok: bool = True
    stopping: bool = Field(description="an answer was being written, and stops after its current piece or step")


class Chat(ChatSummary):
    account: int
    messages: list[ChatMessage] = []


class MessageCreate(RequestModel):
    content: str = Field("", description="the question (up to 4000 characters); may be empty when files are attached")
    context: PageContext | None = Field(None, description="asked from a page: the page, its text and any highlighted part")
    attachments: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="your uploads started with `hold` and finished: the assistant can import them into a namespace",
    )
    model: str | None = Field(
        None, description="answer this one with another model (one of GET /chats/capabilities `models`), e.g. to retry"
    )
    edit: int | None = Field(
        None,
        description="edit one of your earlier questions (its message id): it and everything after it are replaced by this "
        "question and a new answer",
    )
    browser: str | None = Field(
        None,
        max_length=64,
        description="asked from a browser's Lens side panel: its session (POST /browser/sessions). While it's connected, the "
        "assistant can use that browser (docs/browser-assistant.md)",
    )


class ApprovalOrigin(ResponseModel):
    automation: int | None = None
    run: int | None = None
    node: str | None = None


class ApprovalIntent(ResponseModel):
    """What an automation's action would do, as the inbox shows it (credential headers hidden)."""

    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    effect: str | None = None
    risk: str | None = None
    untrusted: bool | None = Field(default=None, description="its run read text from outside Lens")
    node: str | None = None
    status: str | None = None
    spaces: list[int] | None = None


class ApprovalWhy(ResponseModel):
    """The activity that caused it, as it was when it asked."""

    activity: str
    kind: str | None = None
    summary: str | None = None
    published: str | None = None
    url: str | None = None


class ApprovalTold(ResponseModel):
    channel: str
    label: str | None = None
    chat: str | None = None


class ApprovalCan(ResponseModel):
    """What you can do with it from where you asked: approving an automation's action is yours, signed in or in the
    chat it told you in; an API key, an app or an agent can only decline it."""

    approve: bool = False
    decline: bool = False
    edit: bool = False
    always: bool = False


class Approval(ResponseModel):
    id: int
    chat: int | None = None
    tool: str
    summary: str | None = None
    estimate: Any = None
    status: str = Field(description="pending (waiting), running, done, failed, declined or expired")
    created_at: str | None = None
    result: Any = None
    error: str | None = None
    origin: ApprovalOrigin | None = Field(default=None, description="the automation run that asked, when one did")
    intent: ApprovalIntent | None = Field(default=None, description="an automation's action: what it would do")
    automation: str | None = Field(default=None, description="the automation's name")
    space: int | None = None
    why: ApprovalWhy | None = None
    expires_at: str | None = Field(default=None, description="when it expires and nothing runs (automations only)")
    decided_by: str | None = None
    decided_at: str | None = None
    told: list[ApprovalTold] = Field(default_factory=list, description="the chats you were told in")
    can: ApprovalCan | None = None


class ApprovalCount(ResponseModel):
    waiting: int
    automations: int = Field(description="of them, waiting for an automation")


class ApprovalDecision(RequestModel):
    decision: Literal["approve", "sample", "decline", "edit", "always"] = Field(
        "approve",
        description="sample: a batch run on 3 assets first; edit: an automation's action with `params` changed, then "
        "carried out; always: carried out, and that action node of the automation runs without asking from now on "
        "(signed in only)",
    )
    params: dict[str, Any] | None = Field(default=None, description="edit: the params to change (null leaves one out)")


class ApprovalOutcome(ResponseModel):
    status: str
    batch: int | None = Field(default=None, description="the batch run started, for run_template")
    error: str | None = Field(default=None, description="why an automation's action failed")
    result: Any = Field(default=None, description="what an automation's action gave back")
    always: bool | None = Field(default=None, description="that node runs without asking from now on")
