"""Text-to-speech: Piper (self-hosted, open source) and Gemini TTS (cloud).

Two ways to get audio:
  * ``synthesize``      -> a complete WAV file (used for downloads);
  * ``stream_speech``   -> raw 16-bit mono PCM chunks as soon as they exist, so
                           the browser can start playing after the first chunk.
Finished audio is cached, so replaying or downloading the same text is instant.
The browser's built-in speechSynthesis is a third, client-only option.
"""
import asyncio
import base64
import gc
import inspect
import io
import logging
import re
import threading
import urllib.request
import wave
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

from google import genai
from google.genai import types

from .config import Settings

log = logging.getLogger(__name__)


class TTSError(Exception):
    """A problem worth showing to the user."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# Gemini prebuilt voices (name -> style). All speak Hindi, English and mixes.
GEMINI_VOICES = {
    "Kore": "Firm", "Puck": "Upbeat", "Charon": "Informative", "Zephyr": "Bright",
    "Aoede": "Breezy", "Leda": "Youthful", "Orus": "Firm", "Fenrir": "Excitable",
    "Achird": "Friendly", "Sulafat": "Warm", "Gacrux": "Mature", "Despina": "Smooth",
    "Iapetus": "Clear", "Erinome": "Clear", "Vindemiatrix": "Gentle", "Achernar": "Soft",
    "Callirrhoe": "Easy-going", "Umbriel": "Easy-going", "Algieba": "Smooth",
    "Autonoe": "Bright", "Enceladus": "Breathy", "Algenib": "Gravelly",
    "Rasalgethi": "Informative", "Laomedeia": "Upbeat", "Alnilam": "Firm",
    "Schedar": "Even", "Pulcherrima": "Forward", "Zubenelgenubi": "Casual",
    "Sadachbia": "Lively", "Sadaltager": "Knowledgeable",
}
GEMINI_RATE = 24_000
GEMINI_TIMEOUT_S = 15   # max wait for the first audio (and between chunks)
GEMINI_TTS_MODELS = ("gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts")

# Piper voices (https://huggingface.co/rhasspy/piper-voices). Text containing
# Devanagari is read by the chosen Hindi voice; Latin-only text by the English one.
PIPER_VOICES = {
    "hi_IN-priyamvada-medium": "Priyamvada (Hindi, female)",
    "hi_IN-pratham-medium": "Pratham (Hindi, male)",
}
PIPER_ENGLISH_VOICE = "en_US-lessac-medium"
PIPER_RATE = 22_050  # all three "medium" voices
_PIPER_PATHS = {
    "hi_IN-priyamvada-medium": "hi/hi_IN/priyamvada/medium",
    "hi_IN-pratham-medium": "hi/hi_IN/pratham/medium",
    "en_US-lessac-medium": "en/en_US/lessac/medium",
}
_PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{path}/{name}.{ext}"

MAX_CHARS = 4000


def has_devanagari(text: str) -> bool:
    return any("ऀ" <= c <= "ॿ" for c in text)


def _wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _unwav(data: bytes) -> tuple[int, bytes]:
    with wave.open(io.BytesIO(data)) as w:
        return w.getframerate(), w.readframes(w.getnframes())


# ------------------------------------------------------------------ Gemini
def _gemini_client(api_key: str) -> genai.Client:
    # The SDK silently retries 429s with back-off, which looks like a hang;
    # fail fast instead and fall back to another model / voice.
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            api_version="v1beta", retry_options=types.HttpRetryOptions(attempts=1)
        ),
    )


def _gemini_error(exc: Exception) -> TTSError:
    if isinstance(exc, TTSError):
        return exc
    if isinstance(exc, TimeoutError):
        return TTSError("Gemini voice is not responding right now.", 504)
    text = str(exc)
    low = text.lower()
    if "429" in low or "rate limit" in low or "quota" in low or "resource_exhausted" in low:
        limit = re.search(r"limit: ([^)]+)\)", text)
        wait = re.search(r"retry in ((?:\d+h)?(?:\d+m)?)", text)
        msg = "Gemini's free voice limit is used up"
        if limit:
            msg += f" ({limit.group(1)})"
        if wait and wait.group(1):
            msg += f"; it resets in {wait.group(1)}"
        return TTSError(msg + ".", 429)
    if "api key" in low or "401" in low or "403" in low or "permission" in low:
        return TTSError("Gemini rejected the API key.", 401)
    return TTSError(f"Gemini voice error: {text[:200]}", 502)


def _is_retryable(err: TTSError) -> bool:
    # Each model has its own quota, so a limit on one is worth trying the other.
    return err.status in (429, 502, 504)


def _gemini_models(model: str) -> list[str]:
    return [model, *[m for m in GEMINI_TTS_MODELS if m != model]][:2]


def _check_key(api_key: str) -> None:
    if not api_key:
        raise TTSError("No Gemini API key. Set GEMINI_API_KEY or add a key in Settings.", 401)


def _strip_wav_header(chunk: bytes) -> bytes:
    if chunk.startswith(b"RIFF"):
        return _unwav(chunk)[1]
    return chunk


async def gemini_tts(text: str, voice: str, api_key: str, model: str) -> bytes:
    """Whole-file Gemini TTS (used for downloads). Returns a WAV."""
    _check_key(api_key)
    voice = voice if voice in GEMINI_VOICES else "Kore"
    client = _gemini_client(api_key)
    models = _gemini_models(model)
    for i, attempt in enumerate(models):
        try:
            result = await asyncio.wait_for(
                client.aio.interactions.create(
                    model=attempt,
                    input=text,
                    response_format={"type": "audio"},
                    generation_config={"speech_config": [{"voice": voice}]},
                ),
                timeout=GEMINI_TIMEOUT_S,
            )
            data = result.output_audio.data if result.output_audio else None
            if not data:
                raise TTSError("Gemini returned no audio.", 502)
            audio = base64.b64decode(data) if isinstance(data, str) else bytes(data)
            return audio if audio.startswith(b"RIFF") else _wav(audio, GEMINI_RATE)
        except Exception as exc:
            err = _gemini_error(exc)
            if i == len(models) - 1 or not _is_retryable(err):
                log.warning("Gemini TTS failed: %s", exc)
                raise err from exc
            log.warning("Gemini TTS %s failed (%s), trying %s", attempt, err, models[i + 1])
    raise TTSError("Gemini voice failed.", 502)  # pragma: no cover


def _event_audio(event) -> bytes | None:
    """Audio bytes carried by one streaming event (``step.delta`` of type audio)."""
    delta = getattr(event, "delta", None)
    if delta is None or getattr(delta, "type", None) != "audio":
        return None
    data = getattr(delta, "data", None)
    if not data:
        return None
    return base64.b64decode(data) if isinstance(data, str) else bytes(data)


async def gemini_stream(text: str, voice: str, api_key: str, model: str) -> AsyncIterator[bytes]:
    """Stream raw 24 kHz PCM from Gemini. Errors before the first chunk raise
    TTSError (so the caller can still send a proper HTTP error)."""
    _check_key(api_key)
    voice = voice if voice in GEMINI_VOICES else "Kore"
    client = _gemini_client(api_key)
    models = _gemini_models(model)
    for i, attempt in enumerate(models):
        try:
            stream = client.aio.interactions.create(
                model=attempt,
                input=text,
                stream=True,  # streamed audio is headerless 24 kHz 16-bit PCM
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": voice}]},
            )
            if inspect.isawaitable(stream):
                stream = await asyncio.wait_for(stream, timeout=GEMINI_TIMEOUT_S)
            events = stream.__aiter__()
            first = None
            while first is None:  # wait for the first audio, skipping other events
                event = await asyncio.wait_for(events.__anext__(), timeout=GEMINI_TIMEOUT_S)
                first = _event_audio(event)
            break
        except StopAsyncIteration:
            # Stream ended without audio deltas: fall back to the whole-file call.
            wav = await gemini_tts(text, voice, api_key, attempt)

            async def whole():
                yield _unwav(wav)[1]

            return whole()
        except Exception as exc:
            err = _gemini_error(exc)
            if i == len(models) - 1 or not _is_retryable(err):
                log.warning("Gemini TTS stream failed: %s", exc)
                raise err from exc
            log.warning("Gemini TTS %s failed (%s), trying %s", attempt, err, models[i + 1])

    async def rest() -> AsyncIterator[bytes]:
        yield _strip_wav_header(first)
        try:
            while True:
                event = await asyncio.wait_for(events.__anext__(), timeout=GEMINI_TIMEOUT_S)
                chunk = _event_audio(event)
                if chunk:
                    yield _strip_wav_header(chunk)
        except StopAsyncIteration:
            return
        except Exception as exc:  # mid-stream: we can only stop early
            log.warning("Gemini TTS stream stopped early: %r", exc)

    return rest()


# ------------------------------------------------------------------ Piper
_piper_cache: OrderedDict[str, object] = OrderedDict()
_piper_lock = threading.Lock()


def _piper_voice(name: str, voices_dir: str, max_loaded: int = 3):
    with _piper_lock:
        if name in _piper_cache:
            _piper_cache.move_to_end(name)
            return _piper_cache[name]
        # Each voice takes ~100-150 MB of RAM; on small servers keep only a few.
        while len(_piper_cache) >= max(1, max_loaded):
            _piper_cache.popitem(last=False)
            gc.collect()
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise TTSError("Piper is not installed on this server.", 503) from exc
        folder = Path(voices_dir)
        folder.mkdir(parents=True, exist_ok=True)
        for ext in ("onnx.json", "onnx"):
            target = folder / f"{name}.{ext}"
            if not target.exists():
                log.info("Downloading Piper voice %s.%s", name, ext)
                url = _PIPER_URL.format(path=_PIPER_PATHS[name], name=name, ext=ext)
                tmp = target.with_suffix(target.suffix + ".part")
                urllib.request.urlretrieve(url, tmp)
                tmp.rename(target)
        voice = PiperVoice.load(str(folder / f"{name}.onnx"))
        _piper_cache[name] = voice
        return voice


def piper_chunks(text: str, voice: str, voices_dir: str, max_loaded: int = 3) -> Iterator[bytes]:
    """Yield 22.05 kHz PCM sentence by sentence (first audio in ~30 ms)."""
    if voice not in PIPER_VOICES:
        voice = next(iter(PIPER_VOICES))
    name = voice if has_devanagari(text) else PIPER_ENGLISH_VOICE
    model = _piper_voice(name, voices_dir, max_loaded)
    for chunk in model.synthesize(text):
        yield chunk.audio_int16_bytes


def piper_tts_sync(text: str, voice: str, voices_dir: str, max_loaded: int = 3) -> bytes:
    return _wav(b"".join(piper_chunks(text, voice, voices_dir, max_loaded)), PIPER_RATE)


def preload_piper(voices_dir: str, max_loaded: int = 3) -> None:
    for name in [*PIPER_VOICES, PIPER_ENGLISH_VOICE][:max_loaded]:
        _piper_voice(name, voices_dir, max_loaded)


async def _thread_iter(make: Callable[[], Iterator[bytes]]) -> AsyncIterator[bytes]:
    """Run a blocking generator in a worker thread and yield its items."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    done = object()
    stop = threading.Event()

    def worker() -> None:
        try:
            for item in make():
                if stop.is_set():
                    return
                asyncio.run_coroutine_threadsafe(queue.put(item), loop).result()
            asyncio.run_coroutine_threadsafe(queue.put(done), loop).result()
        except BaseException as exc:  # hand errors to the async side
            asyncio.run_coroutine_threadsafe(queue.put(exc), loop).result()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        while (item := await queue.get()) is not done:
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        while not queue.empty():  # unblock the worker if it is waiting on put()
            queue.get_nowait()


# ------------------------------------------------------------------ entry points
_cache: OrderedDict[tuple, bytes] = OrderedDict()
_CACHE_SIZE = 64


def _prepare(text: str) -> str:
    text = " ".join(text.split())
    if not text:
        raise TTSError("Nothing to read.")
    if len(text) > MAX_CHARS:
        raise TTSError(f"Text is too long for speech (max {MAX_CHARS} characters).")
    return text


def _remember(key: tuple, wav: bytes) -> None:
    _cache[key] = wav
    _cache.move_to_end(key)
    if len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)


async def synthesize(
    text: str, engine: str, voice: str, settings: Settings, api_key: str | None = None
) -> bytes:
    """Complete WAV file for the text."""
    text = _prepare(text)
    key = (engine, voice, text)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    if engine == "gemini":
        audio = await gemini_tts(
            text, voice, (api_key or "").strip() or settings.gemini_api_key, settings.gemini_tts_model
        )
    elif engine == "piper":
        if not settings.piper_enabled:
            raise TTSError("The Piper voice is disabled on this server.")
        audio = await asyncio.to_thread(
            piper_tts_sync, text, voice, settings.piper_voices_dir, settings.piper_max_loaded
        )
    else:
        raise TTSError(f"Unknown speech engine '{engine}'.")
    _remember(key, audio)
    return audio


async def stream_speech(
    text: str, engine: str, voice: str, settings: Settings, api_key: str | None = None
) -> tuple[int, AsyncIterator[bytes]]:
    """(sample_rate, PCM chunks). Raises TTSError before any audio is sent."""
    text = _prepare(text)
    key = (engine, voice, text)
    if key in _cache:
        rate, pcm = _unwav(_cache[key])

        async def cached() -> AsyncIterator[bytes]:
            for i in range(0, len(pcm), 32_768):
                yield pcm[i : i + 32_768]

        return rate, cached()

    if engine == "gemini":
        rate = GEMINI_RATE
        source = await gemini_stream(
            text, voice, (api_key or "").strip() or settings.gemini_api_key, settings.gemini_tts_model
        )
    elif engine == "piper":
        if not settings.piper_enabled:
            raise TTSError("The Piper voice is disabled on this server.")
        rate = PIPER_RATE
        source = _thread_iter(
            lambda: piper_chunks(text, voice, settings.piper_voices_dir, settings.piper_max_loaded)
        )
    else:
        raise TTSError(f"Unknown speech engine '{engine}'.")

    async def recording() -> AsyncIterator[bytes]:
        parts: list[bytes] = []
        completed = False
        try:
            async for chunk in source:
                parts.append(chunk)
                yield chunk
            completed = True
        finally:
            if completed and parts:  # cache complete audio for replays/downloads
                _remember(key, _wav(b"".join(parts), rate))

    return rate, recording()
