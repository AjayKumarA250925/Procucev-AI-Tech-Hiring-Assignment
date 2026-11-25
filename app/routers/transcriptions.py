"""CRUD REST API for saved transcriptions."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Transcription
from ..schemas import (
    Stats,
    TranscriptionCreate,
    TranscriptionOut,
    TranscriptionPage,
    TranscriptionUpdate,
)

router = APIRouter(prefix="/api/transcriptions", tags=["transcriptions"])


def _default_title(text: str) -> str:
    words = text.split()
    title = " ".join(words[:8])
    return title + ("…" if len(words) > 8 else "")


def _get_or_404(db: Session, transcription_id: int) -> Transcription:
    row = db.get(Transcription, transcription_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transcription not found")
    return row


@router.get("", response_model=TranscriptionPage)
def list_transcriptions(
    q: str | None = Query(default=None, description="Search in title and text"),
    engine: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    stmt = select(Transcription)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Transcription.title.ilike(like), Transcription.text.ilike(like)))
    if engine:
        stmt = stmt.where(Transcription.engine == engine)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Transcription.created_at.desc(), Transcription.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return TranscriptionPage(items=rows, total=total, page=page, page_size=page_size)


@router.get("/stats", response_model=Stats)
def stats(db: Session = Depends(get_db)):
    total = db.scalar(select(func.count(Transcription.id))) or 0
    seconds = db.scalar(select(func.coalesce(func.sum(Transcription.duration_seconds), 0))) or 0
    by_engine = dict(
        db.execute(select(Transcription.engine, func.count()).group_by(Transcription.engine)).all()
    )
    words = sum(len(t.split()) for t in db.scalars(select(Transcription.text)))
    return Stats(total=total, total_words=words, total_audio_seconds=float(seconds), by_engine=by_engine)


@router.post("", response_model=TranscriptionOut, status_code=status.HTTP_201_CREATED)
def create_transcription(body: TranscriptionCreate, db: Session = Depends(get_db)):
    data = body.model_dump()
    data["title"] = (data.get("title") or "").strip() or _default_title(data["text"])
    row = Transcription(**data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/{transcription_id}", response_model=TranscriptionOut)
def get_transcription(transcription_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, transcription_id)


@router.patch("/{transcription_id}", response_model=TranscriptionOut)
def update_transcription(
    transcription_id: int, body: TranscriptionUpdate, db: Session = Depends(get_db)
):
    row = _get_or_404(db, transcription_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, field, value.strip() if isinstance(value, str) else value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{transcription_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transcription(transcription_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, transcription_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
