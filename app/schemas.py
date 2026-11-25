"""Request / response models for the REST API."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TranscriptionBase(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    text: str = Field(min_length=1)
    language: str | None = Field(default=None, max_length=64)
    engine: str = Field(default="manual", max_length=32)
    duration_seconds: float | None = Field(default=None, ge=0)

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank")
        return v.strip()


class TranscriptionCreate(TranscriptionBase):
    pass


class TranscriptionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    text: str | None = Field(default=None, min_length=1)
    language: str | None = Field(default=None, max_length=64)

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("text must not be blank")
        return v.strip() if v else v


class TranscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    text: str
    language: str | None
    engine: str
    duration_seconds: float | None
    word_count: int
    created_at: datetime
    updated_at: datetime


class TranscriptionPage(BaseModel):
    items: list[TranscriptionOut]
    total: int
    page: int
    page_size: int


class Stats(BaseModel):
    total: int
    total_words: int
    total_audio_seconds: float
    by_engine: dict[str, int]
