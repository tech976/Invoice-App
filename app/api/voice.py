"""Voice entry endpoints.

Three steps, deliberately separate: hear it, read it, save it. The middle step
returns proposals rather than a record, because a spoken rate has nothing to
verify it against and must be seen by the person who said it before it counts.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Trade, VoiceClip
from app.voice import snap, speech, vocabulary
from app.voice.service import parse_spoken_trade, save_trade

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["voice"])


RECORDINGS = Path(settings.data_dir) / "voice"


def _keep_recording(data: bytes, suffix: str) -> Path | None:
    """Save the clip so a bad reading can be listened to afterwards."""
    try:
        RECORDINGS.mkdir(parents=True, exist_ok=True)
        import hashlib
        name = hashlib.sha256(data).hexdigest()[:16] + suffix
        path = RECORDINGS / name
        if not path.exists():
            path.write_bytes(data)
        return path
    except OSError as exc:  # noqa: BLE001 - never block a booking over this
        log.warning("could not keep recording: %s", exc)
        return None


def _record_clip(db: Session, filename: str, heard: str, engine: str,
                 duration_ms: int) -> None:
    """Note the recording so it can be labelled and taught from later."""
    if db.scalar(select(VoiceClip).where(VoiceClip.filename == filename)):
        return
    db.add(VoiceClip(filename=filename, heard=heard, engine=engine,
                     duration_ms=duration_ms))
    db.commit()


@router.get("/status")
def voice_status() -> dict:
    """Whether speech recognition is ready on this machine."""
    return {
        "speech_available": speech.available(),
        "model": settings.speech_model,
        "language": settings.speech_language or "auto",
    }


@router.post("/parse")
async def parse_utterance(
    audio: UploadFile | None = File(None),
    text: str | None = Form(None),
    db: Session = Depends(get_db),
) -> dict:
    """Read one spoken (or typed) sentence into trade fields.

    Nothing is saved here. The response is what the review screen shows, with
    the words each value came from so the broker can see why.
    """
    heard = (text or "").strip()
    keep: Path | None = None
    engine = "typed"
    duration_ms = 0

    if audio is not None:
        data = await audio.read()
        if not data:
            raise HTTPException(400, "The recording is empty.")
        if len(data) > settings.max_audio_mb * 1024 * 1024:
            raise HTTPException(413, f"Longer than the {settings.max_audio_mb} MB limit.")
        suffix = Path(audio.filename or "clip.webm").suffix or ".webm"
        # Kept, not discarded. A reading that came out wrong cannot be
        # diagnosed from the words alone — the recording is the evidence.
        keep = _keep_recording(data, suffix)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as handle:
            handle.write(data)
            handle.flush()
            try:
                result = speech.transcribe(
                    Path(handle.name), learned=vocabulary.learned_terms(db))
            except speech.SpeechUnavailable as exc:
                raise HTTPException(503, str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                log.exception("transcription failed")
                raise HTTPException(500, f"Could not transcribe: {exc}") from exc
        heard = result.text
        engine = result.engine
        duration_ms = result.duration_ms

    if not heard:
        raise HTTPException(400, "Nothing was said.")

    parsed = parse_spoken_trade(heard).as_dict()
    # A name close to one already booked is snapped to it, and the screen is
    # told what was changed. Typed input is left alone: if the broker wrote
    # it, he meant it.
    if audio is not None:
        parsed = snap.snap_parsed(
            parsed,
            vocabulary.known_values(db, ("seller", "buyer")),
            vocabulary.known_values(db, ("goods",)),
        )
    if audio is not None and keep is not None:
        log.info("voice %s -> %r", keep.name, heard)
        parsed["clip"] = keep.name
        _record_clip(db, keep.name, heard, engine, duration_ms)
    return {"engine": engine, "duration_ms": duration_ms, **parsed}


@router.get("/clips")
def list_clips(status: str | None = None, limit: int = 100,
               db: Session = Depends(get_db)) -> dict:
    """Recordings kept for teaching, newest first."""
    query = select(VoiceClip).order_by(VoiceClip.id.desc()).limit(limit)
    if status:
        query = query.where(VoiceClip.status == status)
    rows = db.scalars(query).all()
    counts = {
        name: db.scalar(select(func.count()).select_from(VoiceClip)
                        .where(VoiceClip.status == name)) or 0
        for name in ("new", "confirmed", "corrected")
    }
    return {"counts": counts, "clips": [
        {"id": c.id, "filename": c.filename, "heard": c.heard, "said": c.said,
         "status": c.status, "language": c.language,
         "recorded": c.created_at.isoformat() if c.created_at else None}
        for c in rows]}


@router.post("/clips/{clip_id}")
def label_clip(clip_id: int, payload: dict, db: Session = Depends(get_db)) -> dict:
    """Record what was really said, which is what a fine-tune learns from."""
    clip = db.get(VoiceClip, clip_id)
    if clip is None:
        raise HTTPException(404, "No such recording.")
    said = (payload.get("said") or "").strip()
    if not said:
        raise HTTPException(400, "Type what was actually said.")
    clip.said = said
    clip.status = "confirmed" if said == (clip.heard or "").strip() else "corrected"
    db.commit()
    return {"id": clip.id, "status": clip.status}


@router.get("/clips/{clip_id}/audio")
def clip_audio(clip_id: int, db: Session = Depends(get_db)):
    """The recording itself, so it can be heard before it is labelled."""
    from fastapi.responses import FileResponse

    clip = db.get(VoiceClip, clip_id)
    if clip is None:
        raise HTTPException(404, "No such recording.")
    path = RECORDINGS / clip.filename
    if not path.exists():
        raise HTTPException(404, "The recording is no longer on disk.")
    return FileResponse(path)


@router.post("/trades")
def create_trade(payload: dict, db: Session = Depends(get_db)) -> dict:
    """Book a trade the broker has checked."""
    if payload.get("quantity") is None or payload.get("rate") is None:
        raise HTTPException(400, "A trade needs both a quantity and a rate.")
    trade = save_trade(
        db, payload,
        heard=payload.get("heard"),
        parsed=payload.get("parsed"),
    )
    db.commit()
    return {"id": trade.id, "value": float(trade.value or 0), "status": trade.status}


@router.get("/trades")
def list_trades(limit: int = 50, db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Trade).order_by(Trade.id.desc()).limit(limit)).all()
    return {
        "total": len(rows),
        "trades": [
            {
                "id": t.id,
                "traded_on": t.traded_on.isoformat() if t.traded_on else None,
                "seller": t.seller,
                "buyer": t.buyer,
                "goods": t.goods,
                "quantity": float(t.quantity) if t.quantity is not None else None,
                "uom": t.uom,
                "rate": float(t.rate) if t.rate is not None else None,
                "value": float(t.value) if t.value is not None else None,
                "heard": t.heard,
                "status": t.status,
            }
            for t in rows
        ],
    }
