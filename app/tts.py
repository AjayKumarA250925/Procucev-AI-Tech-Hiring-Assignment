"""Text-to-speech: Gemini TTS (cloud) and Piper (self-hosted, open source).

Both return a complete WAV file. The browser's built-in speechSynthesis is a
third, client-only option handled in the front end.
"""
import asyncio
import base64
import gc
import io
import logging
import threading
import urllib.request
import wave
from collections import OrderedDict
from pathlib import Path

from google import genai

from .config import Settings

log = logging.getLogger(__name__)


class TTSError(Exception):
    """A problem worth showing to the user."""


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

# Piper voices (https://huggingface.co/rhasspy/piper-voices). Text containing
# Devanagari is read by the chosen Hindi voice; Latin-only text by the English one.
PIPER_VOICES = {
    "hi_IN-priyamvada-medium": "Priyamvada (Hindi, female)",
    "hi_IN-pratham-medium": "Pratham (Hindi, male)",
}
PIPER_ENGLISH_VOICE = "en_US-lessac-medium"
_PIPER_PATHS = {
    "hi_IN-priyamvada-medium": "hi/hi_IN/priyamvada/medium",
    "hi_IN-pratham-medium": "hi/hi_IN/pratham/medium",
    "en_US-lessac-medium": "en/en_US/lessac/medium",
}
_PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{path}/{name}.{ext}"

MAX_CHARS = 4000


def has_devanagari(text: str) -> bool:
    return any("ऀ" <= c <= "ॿ" for c in text)


# ------------------------------------------------------------------ Gemini
async def gemini_tts(text: str, voice: str, api_key: str, model: str) -> bytes:
    if not api_key:
        raise TTSError("No Gemini API key. Set GEMINI_API_KEY or add a key in Settings.")
    if voice not in GEMINI_VOICES:
        voice = "Kore"
    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
    try:
        for attempt in range(3):  # retry brief network blips, not API errors
            try:
                result = await client.aio.interactions.create(
                    model=model,
                    input=text,
                    response_format={"type": "audio"},
                    generation_config={"speech_config": [{"voice": voice}]},
                )
                break
            except Exception as exc:
                if attempt == 2 or "connect" not in type(exc).__name__.lower():
                    raise
                log.warning("Gemini TTS connection failed, retrying: %s", exc)
                await asyncio.sleep(0.5 * (attempt + 1))
    except Exception as exc:
        log.exception("Gemini TTS failed")
        msg = str(exc).lower()
        if "api key" in msg or "401" in msg or "403" in msg or "permission" in msg:
            raise TTSError("Gemini rejected the API key.") from exc
        if "429" in msg or "quota" in msg or "resource_exhausted" in msg:
            raise TTSError("Gemini TTS quota exceeded. Try the Piper voice.") from exc
        raise TTSError(f"Gemini TTS error: {str(exc)[:200]}") from exc
    data = result.output_audio.data if result.output_audio else None
    if not data:
        raise TTSError("Gemini returned no audio.")
    audio = base64.b64decode(data) if isinstance(data, str) else bytes(data)
    if not audio.startswith(b"RIFF"):  # raw 24 kHz PCM -> wrap in a WAV header
        audio = _wav(audio, 24_000)
    return audio


def _wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


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
            raise TTSError("Piper is not installed on this server.") from exc
        folder = Path(voices_dir)
        folder.mkdir(parents=True, exist_ok=True)
        onnx = folder / f"{name}.onnx"
        for ext in ("onnx.json", "onnx"):
            target = folder / f"{name}.{ext}"
            if not target.exists():
                log.info("Downloading Piper voice %s.%s", name, ext)
                url = _PIPER_URL.format(path=_PIPER_PATHS[name], name=name, ext=ext)
                tmp = target.with_suffix(target.suffix + ".part")
                urllib.request.urlretrieve(url, tmp)
                tmp.rename(target)
        voice = PiperVoice.load(str(onnx))
        _piper_cache[name] = voice
        return voice


def piper_tts_sync(text: str, voice: str, voices_dir: str, max_loaded: int = 3) -> bytes:
    if voice not in PIPER_VOICES:
        voice = next(iter(PIPER_VOICES))
    name = voice if has_devanagari(text) else PIPER_ENGLISH_VOICE
    model = _piper_voice(name, voices_dir, max_loaded)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        model.synthesize_wav(text, w)
    return buf.getvalue()


def preload_piper(voices_dir: str, max_loaded: int = 3) -> None:
    for name in [*PIPER_VOICES, PIPER_ENGLISH_VOICE][:max_loaded]:
        _piper_voice(name, voices_dir, max_loaded)


# ------------------------------------------------------------------ entry point
_cache: OrderedDict[tuple, bytes] = OrderedDict()
_CACHE_SIZE = 64


async def synthesize(
    text: str, engine: str, voice: str, settings: Settings, api_key: str | None = None
) -> bytes:
    text = " ".join(text.split())
    if not text:
        raise TTSError("Nothing to read.")
    if len(text) > MAX_CHARS:
        raise TTSError(f"Text is too long for speech (max {MAX_CHARS} characters).")
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

    _cache[key] = audio
    if len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)
    return audio
