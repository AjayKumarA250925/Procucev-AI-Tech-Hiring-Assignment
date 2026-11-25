"""Self-hosted, open-source real-time transcription with faster-whisper.

Whisper is not a streaming model, so we make it feel live:
  * audio is buffered per utterance;
  * about once a second the current utterance is re-transcribed and sent as an
    ``interim`` result (greedy decoding, fast);
  * when the speaker pauses (simple energy-based voice activity detection) or
    the utterance gets long, it is transcribed once more with beam search and
    sent as ``final``.
Whisper's multilingual models handle code-mixed Hindi/English speech.
"""
import asyncio
import logging
import threading

import numpy as np

from .base import BYTES_PER_SECOND, LANGUAGES, AudioQueue, Emit, EngineError, StreamingEngine

log = logging.getLogger(__name__)

INTERIM_EVERY_S = 0.8     # re-transcribe the open utterance this often (when idle)
END_SILENCE_S = 0.7       # pause that closes an utterance
MAX_UTTERANCE_S = 15.0    # force a final result for long monologues
MIN_UTTERANCE_S = 0.4
SPEECH_RMS = 0.012        # energy threshold (float audio in [-1, 1])
# Nudges Whisper to keep each language in its own script when they are mixed.
HINGLISH_PROMPT = "नमस्ते, आज हम meeting के बारे में बात करेंगे. Let's get started."

_model = None
_model_lock = threading.Lock()


def load_model(size: str, compute_type: str):
    """Load the Whisper model once per process (downloads on first use)."""
    global _model
    with _model_lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise EngineError("faster-whisper is not installed on this server.") from exc
            log.info("Loading Whisper model %s (%s)", size, compute_type)
            _model = WhisperModel(size, device="cpu", compute_type=compute_type)
        return _model


def _rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(x * x)))


class WhisperEngine(StreamingEngine):
    name = "whisper"

    def __init__(self, model_size: str, compute_type: str, language: str = "auto") -> None:
        super().__init__(language)
        self.model_size = model_size
        self.compute_type = compute_type
        opts = LANGUAGES.get(language, LANGUAGES["auto"])
        self.whisper_lang = opts["whisper"]
        self.prompt = HINGLISH_PROMPT if language in ("auto", "hi-en") else None

    def _transcribe(self, pcm: bytes, final: bool) -> tuple[str, str]:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        seconds = len(audio) / 16_000
        segments, info = load_model(self.model_size, self.compute_type).transcribe(
            audio,
            language=self.whisper_lang,
            beam_size=5 if final else 1,
            temperature=0.0,  # no fallback re-decoding: keeps latency bounded
            initial_prompt=self.prompt,
            condition_on_previous_text=False,
            without_timestamps=True,
            # Guard against Whisper's repetition loops on short / cut-off audio.
            max_new_tokens=min(440, int(25 * seconds) + 10),
            repetition_penalty=1.1,
            no_repeat_ngram_size=4,
            vad_filter=False,
        )
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.8).strip()
        return text, info.language or ""

    async def run(self, audio: AudioQueue, emit: Emit) -> None:
        await asyncio.to_thread(load_model, self.model_size, self.compute_type)
        await emit({"type": "ready", "engine": self.name, "model": self.model_size})

        buf = bytearray()
        speech_seen = False
        silence_s = 0.0
        since_interim = 0.0
        interim_task: asyncio.Task | None = None
        utterance_id = 0  # interims from an already-finalised utterance are dropped

        async def interim(pcm: bytes, uid: int) -> None:
            text, _ = await asyncio.to_thread(self._transcribe, pcm, False)
            if text and uid == utterance_id:
                await emit({"type": "interim", "text": text})

        async def finalize() -> None:
            nonlocal buf, speech_seen, silence_s, since_interim, utterance_id
            pcm = bytes(buf)
            buf, speech_seen, silence_s, since_interim = bytearray(), False, 0.0, 0.0
            utterance_id += 1
            if interim_task and not interim_task.done():
                await interim_task  # one decode at a time keeps CPU use predictable
            if len(pcm) / BYTES_PER_SECOND < MIN_UTTERANCE_S:
                return
            text, lang = await asyncio.to_thread(self._transcribe, pcm, True)
            await emit({"type": "final", "text": text, "language": lang} if text
                       else {"type": "interim", "text": ""})

        try:
            while (chunk := await audio.get()) is not None:
                buf.extend(chunk)
                dur = len(chunk) / BYTES_PER_SECOND
                if _rms(chunk) >= SPEECH_RMS:
                    speech_seen, silence_s = True, 0.0
                else:
                    silence_s += dur

                if not speech_seen:
                    # Only silence so far: keep a short lead-in, drop the rest.
                    keep = int(0.3 * BYTES_PER_SECOND) & ~1
                    if len(buf) > keep:
                        del buf[: len(buf) - keep]
                    continue

                if silence_s >= END_SILENCE_S or len(buf) / BYTES_PER_SECOND >= MAX_UTTERANCE_S:
                    await finalize()
                    continue

                since_interim += dur
                # Decode in the background so incoming audio is never blocked;
                # skip this round if the previous interim is still running.
                if since_interim >= INTERIM_EVERY_S and (interim_task is None or interim_task.done()):
                    since_interim = 0.0
                    interim_task = asyncio.create_task(interim(bytes(buf), utterance_id))

            if speech_seen:
                await finalize()
        finally:
            if interim_task and not interim_task.done():
                interim_task.cancel()
