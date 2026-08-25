#!/usr/bin/env python
"""Turn the corrected recordings into a dataset a model can be trained on.

Everything the broker has checked on the Teach screen is written out as audio
plus its true transcript, in the layout the fine-tuning script expects. Clips
nobody has looked at are left out: an uncorrected machine transcript would
teach the model its own mistakes.

    python scripts/export_voice_dataset.py
    python scripts/export_voice_dataset.py --out /tmp/sauda --min-seconds 1
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.models import VoiceClip  # noqa: E402

# Held back so there is an honest measure of whether training helped.
HOLDOUT = 0.15


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path,
                    default=Path(settings.data_dir) / "voice_dataset")
    ap.add_argument("--min-seconds", type=float, default=0.5)
    args = ap.parse_args()

    source = Path(settings.data_dir) / "voice"
    rows = []
    with session_scope() as db:
        clips = db.scalars(
            select(VoiceClip)
            .where(VoiceClip.said.is_not(None), VoiceClip.status != "new")
            .order_by(VoiceClip.id)
        ).all()
        for clip in clips:
            path = source / clip.filename
            if not path.exists():
                continue
            if clip.duration_ms and clip.duration_ms < args.min_seconds * 1000:
                continue
            rows.append({"filename": clip.filename, "text": clip.said.strip(),
                         "status": clip.status})

    if not rows:
        print("Nothing to export yet. Correct some recordings on /training first.")
        return 1

    audio_dir = args.out / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        shutil.copy2(source / row["filename"], audio_dir / row["filename"])

    # A fixed split, so a rerun compares like with like rather than reshuffling.
    cut = max(1, int(len(rows) * (1 - HOLDOUT)))
    for name, subset in (("train", rows[:cut]), ("test", rows[cut:])):
        target = args.out / f"{name}.jsonl"
        with target.open("w") as handle:
            for row in subset:
                handle.write(json.dumps({
                    "audio": f"audio/{row['filename']}", "text": row["text"],
                }, ensure_ascii=False) + "\n")
        print(f"  {name:5} {len(subset):>5} clip(s) -> {target}")

    corrected = sum(1 for r in rows if r["status"] == "corrected")
    print(f"\n{len(rows)} clip(s) exported, {corrected} of them corrections.")
    if len(rows) < 300:
        print("Under 300 clips a fine-tune usually loses to the stock model.")
        print("Keep booking by voice; the set grows on its own.")
    else:
        print("Enough to train on: python scripts/finetune_speech.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
