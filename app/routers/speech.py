"""Text-to-speech endpoint."""
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from ..config import get_settings
from ..tts import TTSError, synthesize

router = APIRouter(prefix="/api", tags=["text-to-speech"])


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1)
    engine: str = Field(default="gemini", pattern="^(gemini|piper)$")
    voice: str = Field(default="", max_length=64)
    api_key: str | None = Field(default=None, max_length=200)


@router.post(
    "/tts",
    response_class=Response,
    responses={200: {"content": {"audio/wav": {}}, "description": "WAV audio"}},
)
async def text_to_speech(body: SpeakRequest):
    """Read text aloud. Returns a WAV file."""
    try:
        audio = await synthesize(body.text, body.engine, body.voice, get_settings(), body.api_key)
    except TTSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(audio, media_type="audio/wav", headers={"Cache-Control": "private, max-age=3600"})
