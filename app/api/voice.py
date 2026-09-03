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
from app.voice import english, extract_llm, runner, snap, speech, translate, vocabulary
from app.voice.service import parse_spoken_trade, save_trade
from app.voice.translate import romanise

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
    """What is actually loaded on this machine, and what is queued."""
    model_up = extract_llm.available()
    return {
        "speech_available": speech.available(),
        "model": settings.speech_model,
        "language": settings.speech_language or "auto",
        "nlp_available": model_up,
        "nlp_model": settings.nlp_model if model_up else None,
        "read_by": "model" if (model_up and settings.nlp_backend == "llm") else "rules",
        "queue": {"asr": runner.depth("asr"), "nlp": runner.depth("nlp")},
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
        # Written and closed before it is read. The decoder opens this path
        # itself, and Windows refuses a second handle on a file the process
        # still holds open — which surfaced as a bare 'Permission denied' from
        # inside av, naming the temp file and nothing else.
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(data)
            clip = Path(handle.name)
        try:
            # Off the event loop and behind a one-deep queue: the server
            # keeps answering, and two recordings never fight for cores.
            result = await runner.run(
                "asr", speech.transcribe, clip,
                learned=vocabulary.learned_terms(db))
        except speech.SpeechUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            log.exception("transcription failed")
            raise HTTPException(500, f"Could not transcribe: {exc}") from exc
        finally:
            clip.unlink(missing_ok=True)
        heard = result.text
        engine = result.engine
        duration_ms = result.duration_ms

    if not heard:
        raise HTTPException(400, "Nothing was said.")

    # Names are snapped to spellings this book already uses, but only for
    # speech. Typed input is left alone: if the broker wrote it, he meant it.
    parsed = await _read(
        heard,
        known_parties=vocabulary.known_values(db, ("seller", "buyer")) if audio else [],
        known_goods=vocabulary.known_values(db, ("goods",)) if audio else [],
    )
    if audio is not None and keep is not None:
        log.info("voice %s -> %r", keep.name, heard)
        parsed["clip"] = keep.name
        _record_clip(db, keep.name, heard, engine, duration_ms)
    return {"engine": engine, "duration_ms": duration_ms, **parsed}


async def _read(heard: str, *, known_parties: list[str] | None = None,
                known_goods: list[str] | None = None) -> dict:
    """Turn one sentence into fields, using each reader for what it is good at.

    Measured on the same sentences, the two disagree in a consistent way.

    The language model understands meaning. It reads a commodity it was never
    given a word for, in any of the three languages, because it read the word
    rather than looked it up.

    It is hopeless with Indian numerals. Over a run of twenty Hindi and
    Marathi sentences it did not get a single figure right: 'तैंतीस बोरी'
    came back as 115 bags, 'नौ सौ पचास' as 918, 'दो सौ क्विंटल' as 2909.
    A three-billion-parameter model is simply not good at arithmetic, and a
    wrong rate is the one error this system cannot afford.

    The rule-based parser is the reverse: it cannot read a word it was never
    given, but its numeral tables are exact and it has no opinion to be wrong
    about.

    So the split is by what each is reliable at, and it is not a vote:

    * figures are the rules' alone. Where the rules could not read one, the
      field is left blank rather than filled from the model — a blank gets
      typed in, a confident wrong number gets saved. What the model thought
      is shown beside it as a hint, never as the value.
    * goods are the model's, except where the rules recognised a commodity
      they actually hold, which is exact and beats a paraphrase.
    * names are snapped to the spelling this book already uses.
    """
    rules = parse_spoken_trade(heard).as_dict()
    rules["read_by"] = "rules"

    merged = rules
    if settings.nlp_backend == "llm":
        merged = await _with_model(heard, rules)

    # A name close to one already booked is snapped to it. Done last, so it
    # applies to whichever reader produced the name.
    merged = snap.snap_parsed(merged, known_parties or [], known_goods or [])
    merged["english"] = english.sentence(merged)
    return merged


async def _with_model(heard: str, rules: dict) -> dict:
    """Fold the language model's reading into the rules', where it helps."""
    try:
        got = await runner.run("nlp", extract_llm.extract, heard)
    except extract_llm.ExtractionUnavailable as exc:
        log.info("no language model (%s); read by rules alone", exc)
        return rules
    except Exception:  # noqa: BLE001 - a model failure must not lose the trade
        log.exception("language model failed; read by rules alone")
        return rules

    trade = got.trade.model_dump()
    merged = dict(rules)
    merged.update({"read_by": "rules+model", "model": got.model,
                   "nlp_ms": got.duration_ms})

    # Goods: the rules win when they recognised an actual commodity, because
    # a table hit is exact. Otherwise the model, which can read a word the
    # tables were never given.
    heard_goods = (rules.get("goods") or {}).get("text", "")
    rules_knew = translate.is_known(heard_goods)
    if trade.get("goods") and not rules_knew:
        merged["goods"] = {"value": trade["goods"], "confidence": 0.85,
                           "text": heard_goods, "read_by": "model"}

    # A party the rules missed entirely is worth taking from the model,
    # spelled in Latin like everything else in the book.
    for field in ("seller", "buyer"):
        if merged.get(field) is None and trade.get(field):
            spelled = romanise(str(trade[field]))
            merged[field] = {"value": spelled, "text": str(trade[field]),
                             "confidence": 0.5, "read_by": "model"}

    # Figures never come from the model — see the note above. It is recorded
    # only so the screen can say what it thought.
    for field in ("quantity", "rate", "uom"):
        guess = merged.get(field)
        theirs = trade.get(field)
        if theirs is None:
            continue
        if guess is None:
            merged[field] = {"value": None, "text": "", "confidence": 0.0,
                             "model_said": theirs, "read_by": "unread"}
        elif guess.get("value") != theirs:
            merged[field] = {**guess, "model_said": theirs}
    return merged


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
