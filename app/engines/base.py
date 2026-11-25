"""Common interface for streaming speech-to-text engines.

An engine consumes raw audio (16-bit little-endian PCM, 16 kHz, mono) from an
asyncio.Queue -- ``None`` marks the end of the stream -- and reports results
through an ``emit`` callback as small dicts:

    {"type": "ready"}                                 engine is listening
    {"type": "interim", "text": "..."}                partial, may still change
    {"type": "final", "text": "...", "language": ""}  settled segment, append it
    {"type": "error", "message": "..."}
"""
import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

SAMPLE_RATE = 16_000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # int16 mono

Emit = Callable[[dict], Awaitable[None]]
AudioQueue = asyncio.Queue  # of bytes | None


class EngineError(Exception):
    """A problem worth showing to the user (bad key, model not available...)."""


class StreamingEngine(ABC):
    name: str

    def __init__(self, language: str = "auto") -> None:
        self.language = language

    @abstractmethod
    async def run(self, audio: AudioQueue, emit: Emit) -> None:
        """Transcribe until the queue yields None, then flush and return."""


# UI language choice -> codes understood by each engine.
# "hi-en" is code-mixed Hindi + English (Hinglish).
LANGUAGES: dict[str, dict] = {
    "auto": {"label": "Auto-detect (any language, mixed OK)", "bcp47": [], "whisper": None},
    "hi-en": {"label": "Hindi + English (Hinglish)", "bcp47": ["hi-IN", "en-IN"], "whisper": None},
    "en": {"label": "English", "bcp47": ["en-US"], "whisper": "en"},
    "hi": {"label": "Hindi", "bcp47": ["hi-IN"], "whisper": "hi"},
    "bn": {"label": "Bengali", "bcp47": ["bn-IN"], "whisper": "bn"},
    "ta": {"label": "Tamil", "bcp47": ["ta-IN"], "whisper": "ta"},
    "te": {"label": "Telugu", "bcp47": ["te-IN"], "whisper": "te"},
    "mr": {"label": "Marathi", "bcp47": ["mr-IN"], "whisper": "mr"},
    "gu": {"label": "Gujarati", "bcp47": ["gu-IN"], "whisper": "gu"},
    "kn": {"label": "Kannada", "bcp47": ["kn-IN"], "whisper": "kn"},
}
