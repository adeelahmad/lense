from __future__ import annotations

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class VoiceInfo(ResponseModel):
    transcribe: bool = Field(description="the server turns speech into text itself (POST /voice/transcribe)")
    engine: str | None = Field(None, description="the speech-to-text engine it uses")
    speak: bool = Field(description="the server reads answers aloud (POST /voice/speak); else the browser does")


class Heard(ResponseModel):
    text: str
    language: str | None = None
    engine: str | None = None
    seconds: float = Field(0, description="how long the clip was")


class SpeakRequest(RequestModel):
    text: str = Field(max_length=8000)
