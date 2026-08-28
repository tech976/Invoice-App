"""Turning recorded audio into words, on your own machine.

Nothing here calls out to a service. The browser's own speech API was not an
option: both Chrome and Safari send the audio to Google or Apple, which is
neither local nor private.

The engine is deliberately swappable. Which one is right depends on how the
broker actually speaks — mixed Hindi, Marathi, Gujarati and English is the
hardest case there is for offline recognition — and that can only be settled
by measuring on real recordings, not chosen in advance.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.voice import vocabulary

log = logging.getLogger(__name__)


class SpeechUnavailable(RuntimeError):
    """No recogniser is installed, or its model has not been downloaded."""


@dataclass
class Transcript:
    text: str
    language: str | None = None
    duration_ms: int = 0
    engine: str = ""


@lru_cache(maxsize=1)
def _whisper():
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise SpeechUnavailable(
            "faster-whisper is not installed. Add it to requirements and "
            "download a model before voice entry will work."
        ) from exc
    # int8 keeps a small model inside a CPU VPS's memory and speed budget.
    # local_files_only is the point: the weights are a file on this machine,
    # fetched once at setup like any other dependency. Nothing here may reach
    # the network at run time, so a missing model is an error to fix during
    # deployment rather than a silent download on a broker's first recording.
    try:
        return WhisperModel(
            settings.speech_model, device="cpu", compute_type="int8",
            # Left below the core count so the web server still has a core to
            # answer on while a recording is being read.
            cpu_threads=settings.cpu_threads,
            num_workers=1,
            local_files_only=True,
        )
    except Exception as exc:  # noqa: BLE001 - almost always a missing model
        name = settings.speech_model
        how = (f"    python scripts/add_speech_model.py <repo>   # for a local path\n"
               if "/" in name or Path(name).exists()
               else f"    python scripts/fetch_speech_model.py {name}\n")
        raise SpeechUnavailable(
            f"The speech model '{name}' is not on this machine. Fetch it once "
            f"during setup with:\n{how}After that it runs entirely offline."
        ) from exc


def transcribe(audio_path: Path, learned: tuple[str, ...] = (),
               clean_audio: bool | None = None) -> Transcript:
    """Recognise one short utterance.

    `language=None` lets the model decide, which is what a sentence mixing
    Hindi and English needs — pinning it to one language makes the other half
    worse.

    `learned` are names this trade book has recorded before. Indian proper
    nouns are the weakest thing an offline recogniser does, and a name it has
    been shown comes back far more often intact.
    """
    import time

    started = time.time()
    model = _whisper()

    if settings.speech_clean_audio if clean_audio is None else clean_audio:
        from tempfile import NamedTemporaryFile

        from app.voice.audio import clean

        with NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            cleaned = Path(tmp.name)
        audio_path = clean(audio_path, cleaned)
    # Which language this is decides which vocabulary helps. Detected once,
    # from audio decoded once, and then reused for the decode itself.
    from faster_whisper import decode_audio

    try:
        samples = decode_audio(str(audio_path), sampling_rate=16_000)
        detected, probability, _ = model.detect_language(audio=samples)
    except Exception as exc:  # noqa: BLE001 - fall back to letting it decide
        log.debug("language detection failed (%s); letting the decoder choose", exc)
        samples, detected, probability = str(audio_path), None, 0.0

    # The detected language chooses the vocabulary, but is not passed to the
    # decoder. Told it is listening to Hindi, Whisper writes Devanagari;
    # left to itself with romanised hints it writes Latin, which is both what
    # the book keeps and what survives transcription intact.
    language = settings.speech_language or None

    def run(language: str | None):
        return model.transcribe(
            samples,
            language=language,
        task="transcribe",
        # A trade is one short sentence, so there is no previous text worth
        # conditioning on — and carrying it over is what makes Whisper repeat
        # itself or drift into a hallucinated sentence on a quiet clip.
        condition_on_previous_text=False,
        # Told what a sauda sounds like, the decoder is far better at the
        # words that matter: the units, the commodities and the codes.
        initial_prompt=vocabulary.initial_prompt(learned, language),
        hotwords=" ".join((vocabulary.hotwords(language), *learned)),
        # A wider beam costs time and buys accuracy. Worth it here: the clip
        # is five seconds long and the number in it is money.
        beam_size=settings.speech_beam_size,
        # One pass at temperature zero. The fallback ladder re-decodes a clip
        # up to four times when the first pass looks unsure, which on a CPU is
        # four times the wait for a sentence the broker is standing there
        # waiting on. He can see the result and correct it faster than the
        # model can second-guess itself.
        temperature=0.0,
        # A market is noisy; trimming silence before decoding helps more than
        # it costs.
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        )

    try:
        segments, info = run(language)
    except IndexError:
        # A model fine-tuned on one language carries no detector, so asking it
        # to work out which language this is fails outright. Those models are
        # exactly the ones worth using for Hindi and Marathi, so the fallback
        # tells it what it already knows.
        fallback = settings.speech_language or "hi"
        log.info("%s cannot detect language; forcing '%s'",
                 settings.speech_model, fallback)
        segments, info = run(fallback)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    log.info("%s heard as '%s' (%.2f)", audio_path.name, detected or "?", probability)
    return Transcript(
        text=text,
        language=getattr(info, "language", None) or detected,
        duration_ms=int((time.time() - started) * 1000),
        engine=f"faster-whisper:{settings.speech_model}",
    )


def available() -> bool:
    try:
        _whisper()
        return True
    except Exception:  # noqa: BLE001 - absence is a normal state here
        return False
