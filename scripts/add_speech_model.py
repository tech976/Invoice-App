#!/usr/bin/env python
"""Bring another recogniser into the app, converted for CPU.

Stock Whisper is trained mostly on English and is weakest at exactly what a
mandi needs: Indian accents, Devanagari, and names like 'Ashapura'. Several
groups publish Whisper models fine-tuned on Indian languages, free and openly
licensed. This fetches one and converts it to the CTranslate2 runtime the app
already uses, so it becomes a drop-in swap.

    python scripts/add_speech_model.py vasista22/whisper-hindi-small
    python scripts/add_speech_model.py ai4bharat/indicwhisper --name indic

Then point SPEECH_MODEL at the path it prints, and measure the difference:

    python scripts/bench_speech.py --models small,data/speech/hindi-small

Downloading is the only step needing a network, exactly like the stock model.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

MODELS = Path(settings.data_dir) / "speech"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", help="a Whisper model on Hugging Face")
    ap.add_argument("--name", help="folder to write it to (default: from the repo)")
    ap.add_argument("--quantization", default="int8",
                    help="int8 keeps it inside a small VPS; float16 needs a GPU")
    args = ap.parse_args()

    try:
        from ctranslate2.converters import TransformersConverter
    except ImportError:
        print("ctranslate2 is missing. pip install -r requirements.txt")
        return 1

    name = args.name or args.repo.split("/")[-1].replace("whisper-", "")
    target = MODELS / name
    target.parent.mkdir(parents=True, exist_ok=True)

    print(f"fetching and converting {args.repo}")
    print("(this downloads once; afterwards the app runs offline)")
    try:
        TransformersConverter(args.repo).convert(
            str(target), quantization=args.quantization, force=True)
    except Exception as exc:  # noqa: BLE001 - a bad repo name is the usual cause
        print(f"\nCould not convert {args.repo}: {exc}")
        print("It must be a Whisper model in transformers format.")
        return 1

    print(f"\nready at {target}")
    print(f"  measure it:  python scripts/bench_speech.py --models small,{target}")
    print(f"  adopt it:    SPEECH_MODEL={target}  in .env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
