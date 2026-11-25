"""Application settings, read from environment variables (or a .env file)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # PostgreSQL in production, e.g. postgresql+psycopg://user:pass@host:5432/db.
    # Falls back to a local SQLite file so the app runs with zero setup.
    database_url: str = "sqlite:///./transcriptions.db"

    # Gemini Live (cloud). If empty, users can paste their own key in the UI.
    gemini_api_key: str = ""
    gemini_live_model: str = "gemini-3.5-transcribe-live"

    # Self-hosted open-source engine (faster-whisper, runs on this server's CPU).
    whisper_enabled: bool = True
    whisper_model: str = "small"  # tiny | base | small | medium | large-v3 (small+ for Hindi)
    whisper_preload: bool = True
    whisper_compute_type: str = "int8"

    default_engine: str = "gemini"

    # Text-to-speech
    gemini_tts_model: str = "gemini-3.8-flash-tts"  # or gemini-3.8-flash-lite-tts
    piper_enabled: bool = True  # self-hosted open-source voices (Piper)
    piper_voices_dir: str = "./.cache/piper"
    piper_preload: bool = True
    piper_max_loaded: int = 3  # voices kept in memory at once (1 on 512 MB servers)


@lru_cache
def get_settings() -> Settings:
    return Settings()
