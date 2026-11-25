"""WebSocket endpoint that streams microphone audio to a speech engine.

Protocol (client -> server):
  1. text  {"type": "start", "engine": "gemini"|"whisper", "language": "auto",
            "api_key": "<optional>", "model": "<optional>"}
  2. binary frames of 16-bit PCM, 16 kHz, mono
  3. text  {"type": "stop"}
Server -> client: JSON events from the engine (ready / interim / final /
error), then {"type": "done"} once the last results have been flushed.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import get_settings
from ..engines import EngineError, create_engine

log = logging.getLogger(__name__)
router = APIRouter()

MAX_SESSION_SECONDS = 10 * 60  # Gemini Live transcription sessions are capped at 10 min


@router.websocket("/ws/transcribe")
async def transcribe(ws: WebSocket) -> None:
    await ws.accept()
    settings = get_settings()

    async def emit(event: dict) -> None:
        await ws.send_text(json.dumps(event, ensure_ascii=False))

    try:
        start = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=15))
        if start.get("type") != "start":
            raise EngineError("First message must be a start message.")
        engine = create_engine(
            start.get("engine") or settings.default_engine,
            settings,
            language=start.get("language") or "auto",
            api_key=start.get("api_key"),
            model=start.get("model"),
        )
    except (EngineError, ValueError, asyncio.TimeoutError, WebSocketDisconnect) as exc:
        if not isinstance(exc, WebSocketDisconnect):
            await emit({"type": "error", "message": str(exc) or "Invalid start message."})
            await ws.close()
        return

    audio: asyncio.Queue = asyncio.Queue()
    engine_task = asyncio.create_task(engine.run(audio, emit))

    async def pump_microphone() -> None:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                raise WebSocketDisconnect()
            if msg.get("bytes"):
                await audio.put(msg["bytes"])
            elif msg.get("text") and json.loads(msg["text"]).get("type") == "stop":
                await audio.put(None)
                return

    pump = asyncio.create_task(pump_microphone())
    try:
        done, _ = await asyncio.wait(
            {engine_task, pump}, timeout=MAX_SESSION_SECONDS, return_when=asyncio.FIRST_COMPLETED
        )
        if not done:  # session cap reached: flush what we have
            await audio.put(None)
        if engine_task not in done:
            if pump in done:
                pump.result()  # surface a client disconnect
            await engine_task
        engine_task.result()
        await emit({"type": "done"})
    except WebSocketDisconnect:
        log.info("Client disconnected")
    except EngineError as exc:
        await _safe(emit({"type": "error", "message": str(exc)}))
    except Exception:
        log.exception("Transcription session failed")
        await _safe(emit({"type": "error", "message": "Unexpected server error."}))
    finally:
        for task in (engine_task, pump):
            task.cancel()
        await _safe(ws.close())


async def _safe(coro) -> None:
    try:
        await coro
    except Exception:
        pass
