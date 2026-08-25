"""Cleaning a recording before the model hears it.

A mandi is loud, and a phone held at arm's length picks up all of it. The
recogniser has no way to tell the trade from the market around it, so the
cheapest accuracy available is spent here rather than on a bigger model:
filtering, denoising and levelling cost milliseconds on a CPU and help more
than tripling the model size did.

Everything is optional. If the audio libraries are missing the clip is passed
through untouched — a market recording is better read imperfectly than not at
all.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Whisper resamples to this anyway; doing it once here keeps the filters
# honest about their cutoffs.
TARGET_RATE = 16_000
# Speech below this is rumble: traffic, handling noise, the phone's own body.
HIGH_PASS_HZ = 80.0
# Leaves headroom so levelling cannot clip.
PEAK = 0.95


def clean(source: Path, target: Path) -> Path:
    """Write a denoised, levelled, mono 16 kHz copy of `source`.

    Returns the cleaned path, or the original if anything is unavailable.
    """
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        return source

    try:
        audio, rate = sf.read(str(source), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001 - webm/opus needs a decoder
        log.debug("could not read %s directly: %s", source.name, exc)
        return source

    audio = audio.mean(axis=1)  # a phone's second channel adds nothing
    audio = _resample(audio, rate, TARGET_RATE)
    audio = _high_pass(audio, TARGET_RATE)
    audio = _denoise(audio, TARGET_RATE)
    audio = _normalise(audio)

    try:
        sf.write(str(target), audio, TARGET_RATE, subtype="PCM_16")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not write cleaned audio: %s", exc)
        return source
    return target


def _resample(audio, rate: int, target: int):
    if rate == target:
        return audio
    try:
        from scipy.signal import resample_poly
        from math import gcd

        step = gcd(int(rate), target)
        return resample_poly(audio, target // step, int(rate) // step)
    except Exception:  # noqa: BLE001
        return audio


def _high_pass(audio, rate: int):
    """Drop the rumble a market floor puts under everything."""
    try:
        from scipy.signal import butter, sosfilt

        sos = butter(4, HIGH_PASS_HZ, btype="highpass", fs=rate, output="sos")
        return sosfilt(sos, audio)
    except Exception:  # noqa: BLE001
        return audio


def _denoise(audio, rate: int):
    """Subtract the steady background, keeping the speech on top of it.

    Non-stationary mode suits a market: the noise floor shifts as people move
    and machinery starts, and a single profile taken at the start would stop
    matching within seconds.
    """
    try:
        import noisereduce as nr

        return nr.reduce_noise(y=audio, sr=rate, stationary=False,
                               prop_decrease=0.75)
    except Exception as exc:  # noqa: BLE001
        log.debug("denoise skipped: %s", exc)
        return audio


def _normalise(audio):
    """Bring a quietly-held phone up to a level the model expects."""
    try:
        import numpy as np

        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak < 1e-6:
            return audio
        return (audio / peak) * PEAK
    except Exception:  # noqa: BLE001
        return audio
