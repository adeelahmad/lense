"""Extensions: tools, skills, hooks and plugins added to the assistant, as code, on the canvas or from chat."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

Kind = Literal["tool", "skill", "hook", "plugin", "mcp"]
Visibility = Literal["private", "namespace", "everyone"]
Origin = Literal["code", "canvas", "chat"]


class ExtensionVersionInfo(ResponseModel):
    version: int
    notes: str | None = None
    origin: str | None = None
    created_at: str | None = None
    created_by: str | None = None


class Extension(ResponseModel):
    id: int
    name: str = Field(description="what the assistant calls it: its tool or skill name")
    kind: Kind
    title: str | None = None
    description: str | None = None
    visibility: Visibility = "private"
    namespaces: list[str] = Field(default_factory=list, description="who sees it, when shared with namespaces")
    enabled: bool = Field(description="whether it's switched on in conversations")
    owner_email: str | None = None
    editable: bool = False
    current: int
    version: int
    spec: dict[str, Any] = Field(description="the tool, skill, hook or plugin itself, as its manifest has it")
    origin: str | None = Field(None, description="how this version was made: code, canvas, chat or import")
    source: dict[str, Any] | None = Field(None, description="where it was imported from: a marketplace's plugin, a link or a file")
    updated_at: str | None = None


class ExtensionDetail(Extension):
    manifest: str = Field(description="the extension as a manifest to read, change and import again")
    history: list[ExtensionVersionInfo] = []


class ExtensionCreate(RequestModel):
    manifest: dict[str, Any] | None = Field(None, description="{name, kind, description, ...settings}")
    text: str | None = Field(None, description="the manifest as code: YAML, JSON, or Markdown with frontmatter")
    enabled: bool = True
    origin: Origin = "code"


class ExtensionVersionCreate(RequestModel):
    manifest: dict[str, Any] | None = None
    text: str | None = None
    notes: str | None = None
    origin: Origin = "code"


class ExtensionUpdate(RequestModel):
    title: str | None = None
    description: str | None = None
    enabled: bool | None = None
    visibility: Visibility | None = None
    namespaces: list[str] | None = None


class ManifestCheck(RequestModel):
    text: str


class ManifestChecked(ResponseModel):
    manifest: dict[str, Any] = Field(description="the manifest, read and checked")


class ExtensionTest(RequestModel):
    tool: str | None = Field(None, description="which of a plugin's tools (a tool extension: itself)")
    args: dict[str, Any] = {}
    confirm: bool = Field(False, description="needed to try a tool that changes something: it really runs")


class ExtensionTestResult(ResponseModel):
    output: Any = None


class McpConfigText(RequestModel):
    text: str = Field(description="an MCP config from another app: Claude's .mcp.json or mcpServers, VS Code's servers, or one server")


class McpServerFound(ResponseModel):
    name: str = Field(description="a name for it, from the config or guessed from its package or address")
    description: str | None = None
    spec: dict[str, Any] = Field(description="{url, headers}, or {command, args, env} for one that runs as a program")
    fill: list[str] = Field(default_factory=list, description="values written as ${NAME}, which its app filled in and Lens can't")


class McpServerSkipped(ResponseModel):
    name: str
    why: str


class McpConfig(ResponseModel):
    servers: list[McpServerFound] = []
    skipped: list[McpServerSkipped] = []


class McpServerTry(RequestModel):
    spec: dict[str, Any] = Field(description="{url, headers}, or {command, args, env} for one that runs as a program")
    extension: int | None = Field(None, description="the MCP server whose saved secrets fill in values sent as ••••")


class McpServerTools(ResponseModel):
    tools: list[dict[str, Any]] = Field(description="its tools, as it lists them now: {name, description, schema, read_only}")


class McpToolSwitch(RequestModel):
    tool: str = Field(description="the tool's name, as the server lists it")
    on: bool | None = Field(None, description="switch it on or off")
    ask: bool | None = Field(None, description="whether it asks first (default: unless the server says it only reads)")


class Marketplace(ResponseModel):
    id: int
    name: str = Field(description="the name its marketplace.json gives it")
    title: str | None = None
    description: str | None = None
    source: str = Field(description="where it is: a repository, or a link to its marketplace.json")
    plugins: int = Field(description="how many plugins it lists")
    added_by: str | None = None
    fetched_at: str | None = None


class MarketplaceSuggestion(ResponseModel):
    name: str
    title: str
    source: str


class MarketplaceCreate(RequestModel):
    source: str = Field(description="owner/repo on GitHub, a GitHub or GitLab link, or a link to a marketplace.json")
    title: str | None = None


class MarketplacePlugin(ResponseModel):
    name: str
    description: str | None = None
    version: str | None = None
    category: str | None = None
    author: str | None = None
    homepage: str | None = None
    installable: bool = Field(description="whether Lens can read it (why_not says why when it can't)")
    why_not: str | None = None
    installed: int | None = Field(None, description="your extension installed from it, if you installed it")


class ExtensionSkipped(ResponseModel):
    kind: str = Field(description="what was left out: hooks, mcp, skill, command, agent or file")
    name: str
    why: str


class ExtensionImportPreview(ResponseModel):
    manifest: dict[str, Any] = Field(description="the plugin as Lens would install it")
    skipped: list[ExtensionSkipped] = []
    source: str


class ExtensionImported(ResponseModel):
    id: int
    version: int
    skipped: list[ExtensionSkipped] = []


class ExtensionImportLink(RequestModel):
    url: str = Field(description="a plugin or skill: a GitHub or GitLab repository or folder, or a .zip or .tar.gz link")
    preview: bool = Field(False, description="say what it would install, without installing it")


class ExtensionImportArchive(RequestModel):
    filename: str | None = None
    data: str = Field(max_length=28_000_000, description="a .zip, .skill or .tar.gz with a plugin or a skill, as base64 (20 MB at most)")
    preview: bool = False
