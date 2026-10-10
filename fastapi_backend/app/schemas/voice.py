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
    voice: str | None = Field(None, max_length=200, description="read in this voice (an id from GET /voice/voices); else the settings'")


class VoiceOption(ResponseModel):
    id: str = Field(description="what voice.tts_voice is set to")
    name: str
    detail: str | None = Field(None, description="what the provider says about it: accent, gender, style, language")


class VoiceList(ResponseModel):
    provider: str
    current: str | None = Field(None, description="the voice answers are read in now (null: the provider's default)")
    voices: list[VoiceOption]
    error: str | None = Field(None, description="why the provider's voices couldn't be listed")
