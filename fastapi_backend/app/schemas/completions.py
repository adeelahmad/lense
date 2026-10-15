"""OpenAI's chat completions shapes, as a namespace's own endpoint takes and gives them (completions.py)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ResponseModel


class ChatCompletionRequest(BaseModel):
    """OpenAI's request. Fields it has that aren't listed here (n, top_p, user, max_tokens, tools, …) are taken and
    left alone."""

    model_config = ConfigDict(extra="allow")

    messages: list[dict[str, Any]] = Field(
        description="the conversation: system, user, assistant (and tool) messages; the last is the question", max_length=1000
    )
    model: str | None = Field(None, description="lens (the configured model), or one people may pick in Lens's chat; anything else is lens")
    stream: bool = Field(False, description="send the answer as server-sent events, OpenAI's chunks, ending in data: [DONE]")
    temperature: float | None = Field(None, ge=0, le=2)
    stream_options: dict[str, Any] | None = None
    response_format: dict[str, Any] | None = Field(
        None, description="any format asked for: the answer then has no Sources list after it (the format itself isn't enforced yet)"
    )


class CompletionSource(ResponseModel):
    n: int = Field(description="what the answer cites it as: [n]")
    recording_id: int = Field(description="the asset it comes from")
    title: str | None = None
    time: str | None = Field(None, description='the moment, as "12:34", or a document\'s page, as "p. 2"')
    speaker: str | None = Field(None, description="who spoke, in audio or video")
    url: str = Field(description="the moment in the web app")
    cited: bool = Field(description="whether the answer cites it")


class CompletionMessage(ResponseModel):
    role: str = "assistant"
    content: str


class CompletionChoice(ResponseModel):
    index: int = 0
    message: CompletionMessage
    finish_reason: str = "stop"


class CompletionLens(ResponseModel):
    namespace: str
    sources: list[CompletionSource]
    steps: list[dict[str, Any]] = Field(default_factory=list, description="the tools it looked things up with")
    notice: str | None = None


class ChatCompletion(ResponseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[CompletionChoice]
    citations: list[str] = Field(description="the links of what it can cite, in order: [n] is the n-th")
    lens: CompletionLens


class CompletionModel(ResponseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "lens"


class CompletionModels(ResponseModel):
    object: str = "list"
    data: list[CompletionModel]
