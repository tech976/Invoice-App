"""Snapping a misheard name back to one the book already knows.

Codes survive dictation almost perfectly; Indian proper nouns do not. Whisper
turns 'Ashapura' into 'A chapura' and 'Shaan' into 'saun' — close enough that
a person reads straight past it, far enough that the column is wrong.

So a name that nearly matches one this trade book has recorded before is
snapped to it. The comparison is against the book's own history and nothing
else: no imported list, no invoice data, and a name never seen before is left
exactly as it was heard.

Two guards keep it honest. A match must clear a threshold measured against
real mishearings, and it must beat the runner-up by a clear margin — where two
known names are both plausible, neither is chosen and the broker decides.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

# Measured, not guessed. Real mishearings of a known name score from 67 up
# ('saun' against 'Shaan' is the worst of them), while genuinely different
# parties reach at most 62 ('Shaan' against 'Ashapura'). 65 sits in that gap.
SNAP_THRESHOLD = 65
# And the winner must be this far clear of the next candidate, so a name that
# resembles two known parties equally is left alone.
SNAP_MARGIN = 6


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def snap(heard: str | None, known: list[str]) -> tuple[str | None, str | None]:
    """Return (value, snapped_from) for one spoken name.

    `snapped_from` is set only when a correction was applied, so the review
    screen can show what was changed rather than quietly rewriting the words.
    """
    if not heard or not known:
        return heard, None
    target = _norm(heard)
    if not target or len(target) < 3:
        return heard, None

    scored = sorted(
        ((fuzz.ratio(target, _norm(name)), name) for name in known),
        reverse=True,
    )
    best_score, best_name = scored[0]
    if best_name == heard or _norm(best_name) == target:
        return best_name, None
    if best_score < SNAP_THRESHOLD:
        return heard, None
    runner_up = scored[1][0] if len(scored) > 1 else 0
    if best_score - runner_up < SNAP_MARGIN:
        return heard, None
    return best_name, heard


def snap_parsed(parsed: dict, known_parties: list[str],
                known_goods: list[str]) -> dict:
    """Apply snapping to the party and goods columns of a parsed trade."""
    for field, known in (("seller", known_parties), ("buyer", known_parties),
                         ("goods", known_goods)):
        guess = parsed.get(field)
        if not guess or not isinstance(guess, dict):
            continue
        value, was = snap(guess.get("value"), known)
        if was is not None:
            guess["value"] = value
            guess["snapped_from"] = was
            guess["confidence"] = min(0.85, float(guess.get("confidence") or 0) + 0.2)
    return parsed
