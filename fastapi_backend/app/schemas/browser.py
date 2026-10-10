from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel

BrowserTool = Literal["tabs", "open", "read", "click", "type", "press", "import"]


class BrowserConnect(RequestModel):
    label: str | None = Field(None, max_length=80, description="what to call this browser, e.g. 'Chrome on the laptop'")


class BrowserSession(ResponseModel):
    id: str
    label: str
    created_at: str | None = None
    seen_at: str | None = None
    live: bool = Field(description="its side panel is connected now")


class BrowserAnswer(RequestModel):
    ok: bool = Field(description="whether the browser did it")
    result: Any = Field(None, description="what it found or did (the page, the tabs, the file it attached...)")
    error: str | None = Field(None, max_length=1000, description="why not, when it didn't (the person said no, no such ref...)")


class BrowserRun(RequestModel):
    tool: BrowserTool = Field(description="the action: one of the assistant's browser_ tools, without browser_")
    args: dict[str, Any] = Field(default_factory=dict, description="its arguments, as the browser_ tool takes them")
    wait: int = Field(60, ge=1, le=120, description="seconds to wait for the browser")


class BrowserResult(ResponseModel):
    ok: bool
    result: Any = None
    error: str | None = None
