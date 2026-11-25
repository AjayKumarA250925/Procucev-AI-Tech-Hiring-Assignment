import asyncio

import pytest

from app.engines.base import StreamingEngine
from app.routers import live


class FakeEngine(StreamingEngine):
    """Echoes how much audio it received, like a tiny speech engine."""
    name = "fake"

    async def run(self, audio, emit):
        await emit({"type": "ready", "engine": self.name, "model": "test"})
        total = 0
        while (chunk := await audio.get()) is not None:
            total += len(chunk)
            await emit({"type": "interim", "text": f"{total} bytes"})
        await asyncio.sleep(0.05)
        await emit({"type": "final", "text": "नमस्ते, how are you?", "language": "hi"})


@pytest.fixture()
def fake_engine(monkeypatch):
    monkeypatch.setattr(live, "create_engine", lambda *a, **k: FakeEngine())


def test_stream_protocol(client, fake_engine):
    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_json({"type": "start", "engine": "fake"})
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(b"\x00" * 3200)
        assert ws.receive_json() == {"type": "interim", "text": "3200 bytes"}
        ws.send_bytes(b"\x00" * 3200)
        assert ws.receive_json() == {"type": "interim", "text": "6400 bytes"}
        ws.send_json({"type": "stop"})
        assert ws.receive_json() == {"type": "final", "text": "नमस्ते, how are you?", "language": "hi"}
        assert ws.receive_json() == {"type": "done"}


def test_gemini_without_key_reports_error(client):
    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_json({"type": "start", "engine": "gemini"})
        msg = ws.receive_json()
        assert msg["type"] == "error" and "API key" in msg["message"]


def test_unknown_engine_and_bad_start(client):
    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_json({"type": "start", "engine": "nope"})
        assert "Unknown engine" in ws.receive_json()["message"]
    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_json({"type": "hello"})
        assert ws.receive_json()["type"] == "error"


def test_gemini_config_builder():
    from app.engines.gemini_live import GeminiLiveEngine

    cfg = GeminiLiveEngine("k", "gemini-3.5-transcribe-live", "hi-en").build_config()
    assert cfg.response_modalities == ["TEXT"]
    assert cfg.input_audio_transcription.language_codes == ["hi-IN", "en-IN"]
    cfg = GeminiLiveEngine("k", "gemini-3.1-flash-live-preview", "auto").build_config()
    assert cfg.response_modalities == ["AUDIO"]
    assert cfg.input_audio_transcription.language_codes is None
