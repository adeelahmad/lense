"""Conversations with the archive, the assistant's approvals and checking an answer against its sources."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.schemas.activity import ActivityEntry
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
    tools: list[str] | None = Field(
        None,
        max_length=100,
        description="what its answers may use: tool groups and tools (GET /chats/tools); [] for none; left out: your latest "
        "choice (PATCH /chats/{cid}), else Auto",
    )
    max_tool_calls: int | None = Field(
        None, ge=1, description="at most this many tool calls in each answer (1 to ai.max_tool_calls); left out: your latest choice"
    )


class ChatUpdate(RequestModel):
    title: str | None = None
    scope: ChatScope | None = None
    model: str | None = Field(
        None, description="the model that answers in it (one of GET /chats/capabilities `models`); null: the configured one"
    )
    tools: list[str] | None = Field(
        None,
        max_length=100,
        description="what its answers may use: tool groups and tools (GET /chats/tools); [] for none; null: Auto (everything "
        "you may use). Your new conversations start with it too",
    )
    max_tool_calls: int | None = Field(
        None, ge=1, description="at most this many tool calls in each answer (1 to ai.max_tool_calls); null: no limit but the rounds"
    )


class ChatSummary(ResponseModel):
    id: int
    title: str
    kind: Literal["chat", "setup", "bridge"] = Field("chat", description="bridge: a conversation in a chat room, through Matterbridge")
    scope: dict[str, Any] = {}
    model: str | None = Field(None, description="the model chosen for this conversation; null: the configured one")
    tools: list[str] | None = Field(None, description="the tool groups and tools its answers may use; null: Auto")
    max_tool_calls: int | None = Field(None, description="at most this many tool calls in each answer; null: no limit but the rounds")
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
    ms: int | None = Field(None, description="how long the tool took")
    failed: bool | None = Field(None, description="the tool failed (or was unknown or blocked); its summary says why")


class RecordTools(ResponseModel):
    picked: list[str] | None = Field(None, description="the tools or groups the question asked for; null: Auto")
    offered: list[str] = Field(default_factory=list, description="every tool the model was offered")


class RecordLimits(ResponseModel):
    rounds: int | None = Field(None, description="rounds of tool calls it had (calls in one round run together)")
    tool_calls: int | None = Field(None, description="tool calls it had; null: only the rounds limit them")


class RecordUse(ResponseModel):
    rounds: int = 0
    tool_calls: int = 0


class RecordTokens(ResponseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)

    in_: int = Field(0, alias="in")
    out: int = 0


class RecordOptions(ResponseModel):
    """What a question was asked with (only what it set)."""

    model: str | None = None
    tools: list[str] | None = None
    max_tool_calls: int | None = None
    scope: dict[str, Any] | None = None
    research: bool | None = None


class MessageRecord(ResponseModel):
    """What produced a message, recorded as it was written (docs/assistant.md, "What each answer records"). A question's
    has `via` and `options`; an answer's the rest. Messages from before it was recorded have none."""

    via: Literal["web", "api", "room", "voice", "automation", "completions"] | None = Field(None, description="a question: how it was sent")
    options: RecordOptions | None = Field(None, description="a question: the options it was asked with")
    model: str | None = Field(None, description="the model that wrote the answer")
    provider: str | None = Field(None, description="the host name of the model server it ran on (never its address or key)")
    tools: RecordTools | None = None
    scope: dict[str, Any] | None = Field(None, description="the scope it answered in, after a conversation over everything was narrowed")
    limits: RecordLimits | None = None
    used: RecordUse | None = None
    outcome: Literal["answered", "direct", "limit", "fallback", "stopped", "error"] | None = Field(
        None,
        description="answered (after looking things up), direct (without looking anything up), limit (its rounds ran "
        "out, so it answered from what it found), fallback (from a search: the model can't use tools, or none is set "
        "up), stopped, or error",
    )
    tokens: RecordTokens | None = Field(None, description="the tokens of the model calls made for it (the activity ledger)")
    cost_usd: float | None = Field(None, description="what those calls cost, from the prices in Settings")
    cost_estimated: bool | None = Field(None, description="some calls had a price but no token counts: the cost is at least this")
    ms: int | None = Field(None, description="how long it took to write, from the question")
    flagged: list[str] | None = Field(None, description="tools whose results read like instructions to an AI")
    research: int | None = Field(None, description="the research it belongs to")
    version: str | None = Field(None, description="the version of Lens that wrote it")


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


class Versions(ResponseModel):
    """A message's versions: it and the others with the same parent and role, oldest first."""

    index: int = Field(description="which of them this one is, from 1")
    count: int
    ids: list[int]


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
    parent: int | None = Field(None, description="the message it follows; null for a first question")
    versions: Versions | None = Field(None, description="it and the others said in its place, from regenerating or editing")
    meta: MessageRecord | None = Field(None, description="what produced it; none on messages from before it was recorded")


class MessageDetail(ChatMessage):
    ledger: list[ActivityEntry] = Field(
        default_factory=list, description="its rows in the activity ledger: the model and tool calls made for it, newest first"
    )


class ChatNode(ResponseModel):
    """A message of any version, as `tree` lists them."""

    id: int
    parent: int | None = None
    role: Literal["user", "assistant"]
    created_at: str | None = None


class ChatCapabilities(ResponseModel):
    configured: bool = Field(description="a language model is set up; without one, answers are the best-matching passages")
    model: str | None = Field(None, description="the model answers come from")
    tools: bool = Field(description="the assistant looks things up with tools (and may propose work for approval)")
    max_steps: int = Field(description="the most rounds of tool calls it makes for one answer (a round makes one call or more)")
    max_tool_calls: int = Field(50, description="the most tool calls a question may give one answer (`max_tool_calls`)")
    check: bool = Field(description="answers can be checked against their sources")
    models: list[str] = Field(
        default_factory=list, description="the models people may pick, the configured one first (llm.chat_models, else the server's list)"
    )


class StopResult(ResponseModel):
    ok: bool = True
    stopping: bool = Field(description="an answer was being written, and stops after its current piece or step")


class Chat(ChatSummary):
    account: int
    head: int | None = Field(None, description="the last message of the version showing, which the next question follows")
    messages: list[ChatMessage] = Field(default_factory=list, description="the version showing, from the first message to `head`")
    tree: list[ChatNode] = Field(default_factory=list, description="every message of every version, oldest first")


class HeadUpdate(RequestModel):
    message: int = Field(description="a message of the version to show: the head moves to the newest message below it")


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
    parent: int | None = Field(
        None, description="the message to go on from (one of this conversation's, of any version); null: the conversation's `head`"
    )
    regenerate: int | None = Field(
        None,
        description="an answer's message id: write a new answer to its question, as another version beside it (`content` is "
        "not needed; `model` picks another model)",
    )
    edit: int | None = Field(
        None,
        description="edit one of your earlier questions (its message id): this question becomes a new version beside it, and "
        "is answered; the old one and what followed it stay as the other version",
    )
    browser: str | None = Field(
        None,
        max_length=64,
        description="asked from a browser's Lens side panel: its session (POST /browser/sessions). While it's connected, the "
        "assistant can use that browser (docs/browser-assistant.md)",
    )
    via: Literal["voice"] | None = Field(None, description="voice: said in a voice conversation (the question's record keeps it)")
    tools: list[str] | None = Field(
        None,
        max_length=100,
        description="what this answer may use: tool groups (archive, notes, mcp:tracker) and tools, as GET /chats/tools lists "
        "them; [] for none (it answers from a search); null: the conversation's choice, else Auto (everything you may use). An "
        "admin's switch still wins",
    )
    max_tool_calls: int | None = Field(
        None, ge=1, description="at most this many tool calls in this answer (1 to ai.max_tool_calls); null: the conversation's"
    )
    scope: ChatScope | None = Field(
        None,
        description="what this answer draws on, instead of the conversation's scope (which stays as it is); {} for everything you can read",
    )


class ToolInfo(ResponseModel):
    name: str
    effect: Literal["read", "asks", "out"] = Field(
        description="read: it looks things up; asks: it waits for your yes; out: it sends what it's given to another host"
    )
    out: bool = Field(False, description="it sends what it's given to another host (and asks first after outside text)")
    available: bool = Field(description="you have it now")


class ToolGroup(ResponseModel):
    name: str = Field(description="what `tools` names it by: a built-in group, mcp:<server> or ext:<extension>")
    label: str
    tools: list[ToolInfo]
    asks: bool = Field(description="one of its tools waits for your yes")
    out: bool = Field(description="one of its tools sends data to another host")
    available: bool = Field(description="you have one of its tools now")
    why: str | None = Field(None, description="why you don't, when you have none")
    setting: str | None = Field(None, description="the setting that would change that (section.key), for admins")


class ToolChoice(ResponseModel):
    tools: list[str] | None = None
    max_tool_calls: int | None = None


class ChatTools(ResponseModel):
    groups: list[ToolGroup] = Field(description="the built-in groups, then your extensions' (mcp:<server>, ext:<extension>)")
    max_tool_calls: int = Field(description="the most tool calls one answer may be given (ai.max_tool_calls)")
    remembered: ToolChoice = Field(description="your latest choice, which new conversations start with")
    namespaces_note: str = Field(
        "Web and MCP tools reach beyond your namespaces: the namespaces you pick narrow the archive's tools only.",
        description="what the choice of namespaces doesn't narrow",
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
