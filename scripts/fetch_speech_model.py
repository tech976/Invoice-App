#!/usr/bin/env python
"""Download the speech model once, during setup.

The application itself never reaches the network — `speech.py` loads the model
with `local_files_only`, so a missing model is a deployment error rather than
a download that happens while a broker is standing in the market.

This script is the one moment a network is needed, and it is deliberately
separate from anything the app runs.

    python scripts/fetch_speech_model.py           # the configured model
    python scripts/fetch_speech_model.py base      # a smaller, faster one

To keep the server itself offline entirely, run this on any machine and copy
~/.cache/huggingface across.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else settings.speech_model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("faster-whisper is not installed. pip install -r requirements.txt")
        return 1

    print(f"fetching '{name}' (this is the only step that needs a network)...")
    WhisperModel(name, device="cpu", compute_type="int8")
    print(f"'{name}' is now cached locally. The app will not download anything again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
