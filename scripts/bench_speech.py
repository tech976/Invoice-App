#!/usr/bin/env python
"""Measure speech accuracy on real recordings, and tune for it.

Guessing which model or beam width is right is pointless: it depends entirely
on how the broker speaks, how noisy the market is and what his phone's
microphone does. So record him saying twenty real trades, write down what each
one should have produced, and let this tell you.

    mkdir -p samples/voice
    # put clips in there: 001.wav, 002.wav, ...
    # and a samples/voice/expected.json:
    #   {"001.wav": {"seller":"C31","buyer":"V07","goods":"walnut",
    #                "quantity":50,"uom":"BAGS","rate":813}}

    python scripts/bench_speech.py
    python scripts/bench_speech.py --models base,small,medium
    python scripts/bench_speech.py --beams 5,10

Scores each field separately, because they do not matter equally: a wrong rate
is money, a wrong spelling of a name is a nuisance.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.ERROR)

from app.voice import vocabulary  # noqa: E402
from app.voice.parse import parse_trade  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "voice"
FIELDS = ("seller", "buyer", "goods", "quantity", "uom", "rate")
# The two that are money. Everything else is a nuisance to fix; these are not.
CRITICAL = ("quantity", "rate")


def same(field: str, want, got) -> bool:
    if want is None and got is None:
        return True
    if want is None or got is None:
        return False
    if field in ("quantity", "rate"):
        return abs(float(want) - float(got)) < 0.01
    return str(want).strip().lower() == str(got).strip().lower()


def run(model_name: str, beam: int, clips: dict, learned: tuple = (),
        clean_audio: bool = False) -> dict:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8",
                         local_files_only=True)
    hits = {f: 0 for f in FIELDS}
    total = {f: 0 for f in FIELDS}
    perfect = critical_ok = 0
    elapsed = 0.0

    for filename, expected in sorted(clips.items()):
        path = SAMPLES / filename
        if not path.exists():
            continue
        started = time.time()
        source = path
        if clean_audio:
            from tempfile import NamedTemporaryFile

            from app.voice.audio import clean
            with NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                source = clean(path, Path(tmp.name))
        segments, _ = model.transcribe(
            str(source), condition_on_previous_text=False,
            initial_prompt=vocabulary.initial_prompt(learned),
            hotwords=" ".join((vocabulary.hotwords(), *learned)),
            beam_size=beam, patience=1.2,
            temperature=[0.0, 0.2, 0.4, 0.6], vad_filter=True,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        elapsed += time.time() - started

        parsed = parse_trade(text).as_dict()
        ok_all, ok_critical = True, True
        for field in FIELDS:
            if field not in expected:
                continue
            total[field] += 1
            got = (parsed.get(field) or {}).get("value")
            if same(field, expected[field], got):
                hits[field] += 1
            else:
                ok_all = False
                if field in CRITICAL:
                    ok_critical = False
        perfect += ok_all
        critical_ok += ok_critical

    return {"model": model_name, "beam": beam, "hits": hits, "total": total,
            "perfect": perfect, "critical": critical_ok,
            "clips": len(clips), "seconds": elapsed}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="small")
    ap.add_argument("--beams", default="10")
    ap.add_argument("--clean", default="off", choices=("off", "on", "both"),
                    help="denoise and level the audio before decoding")
    ap.add_argument("--learned", default="",
                    help="comma-separated names, to measure what the book "
                         "learns from its own history")
    args = ap.parse_args()

    expected_file = SAMPLES / "expected.json"
    if not expected_file.exists():
        print(f"No recordings to measure. Create {expected_file} — see this "
              f"script's docstring for the shape.")
        return 1
    clips = json.loads(expected_file.read_text())

    cleans = {"off": [False], "on": [True], "both": [False, True]}[args.clean]
    rows = []
    for model_name in args.models.split(","):
        for beam in args.beams.split(","):
            for clean_audio in cleans:
                try:
                    learned = tuple(x.strip() for x in args.learned.split(",") if x.strip())
                    row = run(model_name.strip(), int(beam), clips, learned, clean_audio)
                    row["clean"] = clean_audio
                    rows.append(row)
                except Exception as exc:  # noqa: BLE001
                    print(f"  {model_name}/beam{beam}: unavailable ({exc})")

    print(f"\n{len(clips)} clip(s)\n")
    head = f"{'model':10}{'beam':>5}{'clean':>7}  " + "".join(f"{f:>10}" for f in FIELDS)
    print(head + f"{'money ok':>10}{'all ok':>8}{'s/clip':>8}")
    print("-" * len(head + " " * 26))
    for r in rows:
        cells = "".join(
            f"{(100*r['hits'][f]/r['total'][f]):>9.0f}%" if r["total"][f] else f"{'—':>10}"
            for f in FIELDS)
        per = r["seconds"] / max(1, r["clips"])
        print(f"{r['model']:10}{r['beam']:>5}{('yes' if r.get('clean') else 'no'):>7}  {cells}"
              f"{(100*r['critical']/r['clips']):>9.0f}%"
              f"{(100*r['perfect']/r['clips']):>7.0f}%{per:>8.1f}")
    print("\n'money ok' is quantity and rate both correct — the number that decides")
    print("whether this is safe to use. Names can be fixed on screen; a rate cannot")
    print("be noticed once it is wrong.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
