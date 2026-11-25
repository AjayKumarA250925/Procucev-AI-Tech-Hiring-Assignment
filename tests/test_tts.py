import base64
import io
import wave
from pathlib import Path

import pytest

from app import tts


def _wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


def test_tts_validation(client):
    assert client.post("/api/tts", json={"text": "", "engine": "piper"}).status_code == 422
    assert client.post("/api/tts", json={"text": "hi", "engine": "nope"}).status_code == 422
    r = client.post("/api/tts", json={"text": "x" * 5000, "engine": "piper"})
    assert r.status_code == 400 and "too long" in r.json()["detail"]


def test_gemini_tts_needs_key(client):
    r = client.post("/api/tts", json={"text": "hello", "engine": "gemini"})
    assert r.status_code == 400 and "API key" in r.json()["detail"]


def test_gemini_tts_wraps_raw_pcm(client, monkeypatch):
    calls = []

    class FakeInteractions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            pcm = b"\x00\x00" * 24_000  # 1 s of silence, raw PCM, base64 like the API
            audio = type("A", (), {"data": base64.b64encode(pcm).decode()})
            return type("R", (), {"output_audio": audio})

    class FakeClient:
        def __init__(self, **_):
            self.aio = type("Aio", (), {"interactions": FakeInteractions()})

    monkeypatch.setattr(tts.genai, "Client", FakeClient)
    tts._cache.clear()
    r = client.post("/api/tts", json={"text": "नमस्ते  world", "engine": "gemini",
                                      "voice": "Puck", "api_key": "k"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert _wav_seconds(r.content) == 1.0
    assert calls[0]["generation_config"] == {"speech_config": [{"voice": "Puck"}]}
    assert calls[0]["input"] == "नमस्ते world"
    # Same request again is served from cache.
    client.post("/api/tts", json={"text": "नमस्ते world", "engine": "gemini", "voice": "Puck", "api_key": "k"})
    assert len(calls) == 1


@pytest.mark.skipif(not Path(".cache/piper/hi_IN-priyamvada-medium.onnx").exists(),
                    reason="Piper voices not downloaded")
def test_piper_speaks_hindi_and_english(client):
    tts._cache.clear()
    hi = client.post("/api/tts", json={"text": "नमस्ते, आप कैसे हैं?", "engine": "piper",
                                       "voice": "hi_IN-pratham-medium"})
    en = client.post("/api/tts", json={"text": "Hello, how are you?", "engine": "piper"})
    assert hi.status_code == en.status_code == 200
    assert _wav_seconds(hi.content) > 0.8 and _wav_seconds(en.content) > 0.8


def test_config_lists_voices(client):
    cfg = client.get("/api/config").json()["tts"]
    assert "Kore" in cfg["gemini"]["voices"]
    assert "hi_IN-priyamvada-medium" in cfg["piper"]["voices"]


def test_gemini_tts_hang_times_out(client, monkeypatch):
    import asyncio

    attempts = []

    class HangingInteractions:
        async def create(self, **kwargs):
            attempts.append(kwargs["model"])
            await asyncio.sleep(60)

    class FakeClient:
        def __init__(self, **_):
            self.aio = type("Aio", (), {"interactions": HangingInteractions()})

    monkeypatch.setattr(tts.genai, "Client", FakeClient)
    monkeypatch.setattr(tts, "GEMINI_TIMEOUT_S", 0.05)
    tts._cache.clear()
    r = client.post("/api/tts", json={"text": "hang test", "engine": "gemini", "api_key": "k"})
    assert r.status_code == 400 and "not responding" in r.json()["detail"]
    # Tried the configured model, then fell back to the other one.
    assert attempts == ["gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts"]


def test_gemini_tts_falls_back_to_other_model(client, monkeypatch):
    import asyncio

    pcm = base64.b64encode(b"\x00\x00" * 24_000).decode()

    class FlakyInteractions:
        async def create(self, **kwargs):
            if kwargs["model"] == "gemini-3.8-flash-lite-tts":
                await asyncio.sleep(60)  # first model hangs
            return type("R", (), {"output_audio": type("A", (), {"data": pcm})})

    class FakeClient:
        def __init__(self, **_):
            self.aio = type("Aio", (), {"interactions": FlakyInteractions()})

    monkeypatch.setattr(tts.genai, "Client", FakeClient)
    monkeypatch.setattr(tts, "GEMINI_TIMEOUT_S", 0.05)
    tts._cache.clear()
    r = client.post("/api/tts", json={"text": "fallback test", "engine": "gemini", "api_key": "k"})
    assert r.status_code == 200 and _wav_seconds(r.content) == 1.0
