"""Talking to Lens (docs/assistant.md#voice): speech to text on the server, and answers read aloud."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.deps import Cfg, CurrentUser, OptionalUser, Principal, signed_out
from app.domain import speech, voice
from app.schemas.voice import Heard, SpeakRequest, VoiceInfo, VoiceList

router = APIRouter(prefix="/voice", tags=["voice"])
CLIP = {"requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}}


def raw_user(request: Request, user: OptionalUser, cfg: Cfg) -> Principal | None:
    # Invalid/scoped credentials never silently downgrade to anonymous access.
    if user is None and (request.headers.get("authorization") or (cfg.get("voice") or {}).get("public_enabled") is not True):
        raise signed_out(request)
    return user


RawUser = Annotated[Principal | None, Depends(raw_user)]


@router.get("")
def voice_info(user: RawUser, cfg: Cfg) -> VoiceInfo:
    """Whether the server hears and speaks itself; the web app uses the browser's speech for what it doesn't. Starts
    loading the speech-to-text engine, so the first thing said isn't kept waiting."""
    engine = voice.can_transcribe(cfg)
    if user is not None:
        voice.warm(cfg)
    return VoiceInfo(transcribe=engine is not None, engine=engine, speak=voice.can_speak(cfg))


@router.post("/transcribe", openapi_extra=CLIP, responses={413: {"description": "Clip exceeds 25 MiB or 300 seconds"}, 429: {"description": "Voice service busy"}, 503: {"description": "Transcription failed"}})
async def transcribe_clip(request: Request, response: Response, user: RawUser, cfg: Cfg) -> Heard:
    """Raw media to text, without archiving. At most 25 MiB and 300 seconds. 409 when no engine is configured.
    Anonymous access is opt-in via voice.public_enabled and uses only the configured engine."""
    if not voice.can_transcribe(cfg):
        raise HTTPException(409, "this server has no speech-to-text engine yet")
    try:
        with voice.upload_admission():
            data = bytearray()
            async with asyncio.timeout(30):
                async for piece in request.stream():
                    if len(data) + len(piece) > voice.MAX_BYTES:
                        raise HTTPException(413, "clips must be no larger than 25 MiB")
                    data += piece
            if not data:
                raise HTTPException(400, "send an audio or video clip")
            response.headers["Cache-Control"] = "no-store"
            # Raw clips must not inherit archive transcription's 30-minute provider timeout.
            speech_cfg = cfg.get("speech") or {}
            raw_cfg = {**cfg, "speech": {**speech_cfg, "timeout": min(float(speech_cfg.get("timeout") or 120), 120)}}
            return Heard(**await run_in_threadpool(voice.run_admitted, voice.transcribe, raw_cfg, bytes(data), anonymous=user is None))
    except voice.Busy as e:
        raise HTTPException(429, str(e), headers={"Retry-After": "60"}) from None
    except TimeoutError:
        raise HTTPException(408, "clip upload timed out") from None
    except voice.ClipTooLong as e:
        raise HTTPException(413, str(e)) from None
    except voice.InvalidClip as e:
        raise HTTPException(400, str(e)) from None
    except RuntimeError:
        # Provider errors may contain URLs, credentials or response bodies; don't return those to callers.
        raise HTTPException(503, "speech-to-text service is unavailable") from None


@router.post(
    "/speak",
    response_class=Response,
    responses={200: {"content": {"audio/mpeg": {}, "audio/wav": {}}, "description": "the text read aloud"},
               204: {"description": "the browser reads it"}, 429: {"description": "Voice service busy"},
               503: {"description": "Server speech unavailable (server_only=true)"}},
)
async def speak_text(body: SpeakRequest, user: RawUser, cfg: Cfg) -> Response:
    """Raw text to audio using configured providers. Set server_only for a real error instead of browser fallback.
    Anonymous callers cannot select private account voices; they use the administrator's configured default."""
    if not body.text.strip():
        raise HTTPException(422, "text must not be blank")
    if user is None and body.voice is not None:
        raise HTTPException(403, "sign in to select a voice")
    try:
        out = await run_in_threadpool(voice.run_admitted, voice.speak, cfg, body.text, body.voice, anonymous=user is None)
    except voice.Busy as e:
        raise HTTPException(429, str(e), headers={"Retry-After": "60"}) from None
    except RuntimeError:
        # Includes activity.OverBudget. Never expose provider or budget internals.
        out = None
    if out is None:
        if body.server_only or user is None:
            raise HTTPException(503, "text-to-speech service is unavailable")
        return Response(status_code=204)
    audio, media = out
    return Response(content=audio, media_type=media, headers={"Cache-Control": "no-store"})


@router.get("/voices")
async def list_voices(
    user: CurrentUser, cfg: Cfg, provider: str | None = Query(None, pattern="^(" + "|".join(speech.TTS_PROVIDERS) + ")$")
) -> VoiceList:
    """The voices a text-to-speech provider (else the one in the voice settings) reads in, from the provider when it
    lists them (ElevenLabs, Deepgram, macOS say, OpenAI-compatible servers that do), else the ones it's known to have."""
    p = provider or speech.tts_provider(cfg)
    found, error = await run_in_threadpool(speech.voices, cfg, p)
    return VoiceList(provider=p, current=speech.tts_voice(cfg) if p == speech.tts_provider(cfg) else None, voices=found, error=error)
