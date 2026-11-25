from ..config import Settings
from .base import LANGUAGES, EngineError, StreamingEngine


def create_engine(
    name: str,
    settings: Settings,
    language: str = "auto",
    api_key: str | None = None,
    model: str | None = None,
) -> StreamingEngine:
    """Build the engine the client asked for."""
    if language not in LANGUAGES:
        raise EngineError(f"Unsupported language '{language}'.")
    if name == "gemini":
        from .gemini_live import GeminiLiveEngine

        return GeminiLiveEngine(
            api_key=(api_key or "").strip() or settings.gemini_api_key,
            model=(model or "").strip() or settings.gemini_live_model,
            language=language,
        )
    if name == "whisper":
        if not settings.whisper_enabled:
            raise EngineError("The self-hosted Whisper engine is disabled on this server.")
        from .whisper_local import WhisperEngine

        return WhisperEngine(settings.whisper_model, settings.whisper_compute_type, language)
    raise EngineError(f"Unknown engine '{name}'.")


__all__ = ["create_engine", "EngineError", "StreamingEngine", "LANGUAGES"]
