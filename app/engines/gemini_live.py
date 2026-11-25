"""Real-time transcription with the Gemini Live API.

Default model is ``gemini-3.5-transcribe-live`` (Google's dedicated live
transcription model: interim + final transcripts, automatic language
detection). General Live models such as ``gemini-3.1-flash-live-preview`` also
work: we enable input-audio transcription and simply ignore the model's reply.
"""
import asyncio
import logging
import time

from google import genai
from google.genai import types

from .base import LANGUAGES, AudioQueue, Emit, EngineError, StreamingEngine

log = logging.getLogger(__name__)

MIME = "audio/pcm;rate=16000"
FLUSH_IDLE_SECONDS = 1.5   # after the mic stops, wait this long with no new text
FLUSH_MAX_SECONDS = 6.0    # ...but never longer than this


class GeminiLiveEngine(StreamingEngine):
    name = "gemini"

    def __init__(self, api_key: str, model: str, language: str = "auto") -> None:
        super().__init__(language)
        if not api_key:
            raise EngineError(
                "No Gemini API key. Set GEMINI_API_KEY on the server or paste a key in Settings."
            )
        self.api_key = api_key
        self.model = model

    def build_config(self) -> types.LiveConnectConfig:
        codes = LANGUAGES.get(self.language, LANGUAGES["auto"])["bcp47"]
        transcription = types.AudioTranscriptionConfig(language_codes=codes or None)
        if "transcribe" in self.model:
            return types.LiveConnectConfig(
                response_modalities=["TEXT"], input_audio_transcription=transcription
            )
        # General conversational Live model: it must answer in audio, but we only
        # want the transcript of what the user said, so tell it to stay quiet.
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=transcription,
            system_instruction="You are a silent listener. Never respond.",
        )

    async def run(self, audio: AudioQueue, emit: Emit) -> None:
        client = genai.Client(api_key=self.api_key, http_options={"api_version": "v1beta"})
        try:
            async with client.aio.live.connect(model=self.model, config=self.build_config()) as session:
                await emit({"type": "ready", "engine": self.name, "model": self.model})
                await self._stream(session, audio, emit)
        except EngineError:
            raise
        except Exception as exc:  # network / auth / quota errors from the SDK
            log.exception("Gemini Live session failed")
            raise EngineError(_friendly(exc)) from exc

    async def _stream(self, session, audio: AudioQueue, emit: Emit) -> None:
        last_activity = time.monotonic()

        async def send() -> None:
            while (chunk := await audio.get()) is not None:
                await session.send_realtime_input(audio=types.Blob(data=chunk, mime_type=MIME))
            await session.send_realtime_input(audio_stream_end=True)

        async def receive() -> None:
            nonlocal last_activity
            while True:  # session.receive() ends after each turn; keep listening
                async for msg in session.receive():
                    sc = msg.server_content
                    if sc is None:
                        continue
                    if sc.interim_input_transcription and sc.interim_input_transcription.text:
                        last_activity = time.monotonic()
                        await emit({"type": "interim", "text": sc.interim_input_transcription.text})
                    if sc.input_transcription and sc.input_transcription.text:
                        last_activity = time.monotonic()
                        await emit({
                            "type": "final",
                            "text": sc.input_transcription.text,
                            "language": sc.input_transcription.language_code or "",
                        })

        sender = asyncio.create_task(send())
        receiver = asyncio.create_task(receive())
        try:
            done, _ = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()  # re-raise errors
            # Mic stopped: give Gemini a moment to deliver the last final transcript.
            stop_at = time.monotonic() + FLUSH_MAX_SECONDS
            last_activity = max(last_activity, time.monotonic())
            while (
                not receiver.done()
                and time.monotonic() < stop_at
                and time.monotonic() - last_activity < FLUSH_IDLE_SECONDS
            ):
                await asyncio.sleep(0.1)
            if receiver.done():
                receiver.result()
        finally:
            for task in (sender, receiver):
                task.cancel()


def _friendly(exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    if "api key" in low or "permission" in low or "401" in low or "403" in low:
        return "Gemini rejected the API key. Check it in Settings."
    if "quota" in low or "429" in low or "resource_exhausted" in low:
        return "Gemini quota exceeded. Wait a minute or use the self-hosted Whisper engine."
    if "not found" in low or "404" in low:
        return "Gemini model not available for this key. Pick another model in Settings."
    return f"Gemini Live error: {text[:300]}"
