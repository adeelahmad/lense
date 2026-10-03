"""Talking to Lens (docs/assistant.md#voice): speech to text on the server, and answers read aloud."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.deps import Cfg, CurrentUser
from app.domain import voice
from app.schemas.voice import Heard, SpeakRequest, VoiceInfo

router = APIRouter(prefix="/voice", tags=["voice"])
CLIP = {"requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}}


@router.get("")
def voice_info(user: CurrentUser, cfg: Cfg) -> VoiceInfo:
    """Whether the server hears and speaks itself; the web app uses the browser's speech for what it doesn't. Starts
    loading the speech-to-text engine, so the first thing said isn't kept waiting."""
    engine = voice.can_transcribe(cfg)
    voice.warm(cfg)
    return VoiceInfo(transcribe=engine is not None, engine=engine, speak=voice.can_speak(cfg))


@router.post("/transcribe", openapi_extra=CLIP)
async def transcribe_clip(request: Request, user: CurrentUser, cfg: Cfg) -> Heard:
    """What was said in a short clip, sent as the raw request body (webm, ogg, mp4 or wav, up to 25 MB), by the
    server's speech-to-text engine. Nothing is kept. 409 when the server has no engine (use the browser's)."""
    if not voice.can_transcribe(cfg):
        raise HTTPException(409, "this server has no speech-to-text engine yet")
    data = bytearray()
    async for piece in request.stream():
        data += piece
        if len(data) > voice.MAX_BYTES:
            raise HTTPException(413, "that clip is too long; say it in shorter parts")
    try:
        return Heard(**await run_in_threadpool(voice.transcribe, cfg, bytes(data)))
    except RuntimeError as e:
        raise HTTPException(400, f"couldn't read that clip: {e}") from None


@router.post(
    "/speak",
    response_class=Response,
    responses={200: {"content": {"audio/mpeg": {}}, "description": "the text read aloud"}, 204: {"description": "the browser reads it"}},
)
async def speak_text(body: SpeakRequest, user: CurrentUser, cfg: Cfg) -> Response:
    """The text read aloud by the text-to-speech model (Settings → AI assistant → Voice). 204 when there's none, or it
    failed: the browser reads it instead."""
    out = await run_in_threadpool(voice.speak, cfg, body.text)
    if out is None:
        return Response(status_code=204)
    audio, media = out
    return Response(content=audio, media_type=media, headers={"Cache-Control": "no-store"})
