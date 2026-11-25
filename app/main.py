"""FastAPI entry point: REST API, WebSocket and the static web UI."""
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .database import Base, engine
from .engines import LANGUAGES
from .routers import live, transcriptions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    settings = get_settings()
    if settings.whisper_enabled and settings.whisper_preload:
        # Warm the self-hosted model in the background so the first user does not wait.
        from .engines.whisper_local import load_model

        threading.Thread(
            target=_safe_preload, args=(load_model, settings), daemon=True
        ).start()
    yield


def _safe_preload(load_model, settings) -> None:
    try:
        load_model(settings.whisper_model, settings.whisper_compute_type)
    except Exception:
        logging.getLogger(__name__).exception("Whisper preload failed")


app = FastAPI(title="Speech to Text", version="1.0.0", lifespan=lifespan)
app.include_router(transcriptions.router)
app.include_router(live.router)


@app.get("/api/config", tags=["meta"])
def public_config():
    """What the UI needs to know: which engines work and their defaults."""
    s = get_settings()
    return {
        "default_engine": s.default_engine,
        "engines": {
            "gemini": {"server_key": bool(s.gemini_api_key), "model": s.gemini_live_model},
            "whisper": {"enabled": s.whisper_enabled, "model": s.whisper_model},
        },
        "languages": {k: v["label"] for k, v in LANGUAGES.items()},
    }


@app.get("/healthz", tags=["meta"])
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
