#!/usr/bin/env python
"""Train Whisper on the broker's own voice.

This is the largest accuracy gain available, and the only one that addresses
the actual weakness: a general model has never heard his accent, his market,
or names like 'Ashapura'. Shown a few hundred examples of exactly those, it
stops guessing at them.

Nothing here calls a service. Weights are downloaded once, training runs on a
machine you control, and the result is converted to the same CPU runtime the
app already uses — after which the server needs no network at all.

    # 1. collect and correct recordings on /training, then
    python scripts/export_voice_dataset.py
    # 2. train (a GPU makes this hours instead of days; rent one, or Colab)
    python scripts/finetune_speech.py --base small --epochs 3
    # 3. convert for the app and point .env at it
    python scripts/finetune_speech.py --convert-only

Training on CPU is possible but slow enough to be impractical beyond a toy
run. The trained model, however, runs on CPU perfectly well — which is the
point.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

DATASET = Path(settings.data_dir) / "voice_dataset"
OUTPUT = Path(settings.data_dir) / "voice_model"
CT2_OUTPUT = Path(settings.data_dir) / "voice_model_ct2"


def load_rows(split: str) -> list[dict]:
    path = DATASET / f"{split}.jsonl"
    if not path.exists():
        raise SystemExit(f"{path} is missing. Run export_voice_dataset.py first.")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def convert(source: Path, target: Path) -> None:
    """Put the trained model into the runtime the app uses.

    The app runs CTranslate2 on CPU; training produces a PyTorch model. This
    is the bridge, and it also quantises to int8 so the result stays inside a
    small VPS's memory.
    """
    from ctranslate2.converters import TransformersConverter

    print(f"converting {source} -> {target} (int8)")
    TransformersConverter(str(source)).convert(
        str(target), quantization="int8", force=True)
    print(f"done. Point SPEECH_MODEL at {target} in .env")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="openai/whisper-small",
                    help="model to start from; an Indic-tuned one starts closer")
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--language", default="hi",
                    help="dominant language of the recordings")
    ap.add_argument("--convert-only", action="store_true")
    args = ap.parse_args()

    if args.convert_only:
        convert(OUTPUT, CT2_OUTPUT)
        return 0

    import evaluate
    import torch
    from datasets import Audio, Dataset
    from transformers import (
        Seq2SeqTrainer, Seq2SeqTrainingArguments, WhisperForConditionalGeneration,
        WhisperProcessor,
    )

    processor = WhisperProcessor.from_pretrained(
        args.base, language=args.language, task="transcribe")
    model = WhisperForConditionalGeneration.from_pretrained(args.base)
    # Let the fine-tune decide the language rather than inheriting a forced one.
    model.generation_config.forced_decoder_ids = None
    model.config.suppress_tokens = []

    def build(split: str) -> Dataset:
        rows = load_rows(split)
        data = Dataset.from_list([
            {"audio": str(DATASET / r["audio"]), "text": r["text"]} for r in rows
        ]).cast_column("audio", Audio(sampling_rate=16_000))

        def prepare(batch):
            audio = batch["audio"]
            batch["input_features"] = processor.feature_extractor(
                audio["array"], sampling_rate=audio["sampling_rate"]
            ).input_features[0]
            batch["labels"] = processor.tokenizer(batch["text"]).input_ids
            return batch

        return data.map(prepare, remove_columns=data.column_names)

    train, test = build("train"), build("test")
    print(f"training on {len(train)} clip(s), holding back {len(test)}")

    def collate(features):
        inputs = [{"input_features": f["input_features"]} for f in features]
        batch = processor.feature_extractor.pad(inputs, return_tensors="pt")
        labels = processor.tokenizer.pad(
            [{"input_ids": f["labels"]} for f in features], return_tensors="pt")
        masked = labels["input_ids"].masked_fill(
            labels.attention_mask.ne(1), -100)
        batch["labels"] = masked
        return batch

    metric = evaluate.load("wer")

    def compute_metrics(pred):
        ids = pred.label_ids
        ids[ids == -100] = processor.tokenizer.pad_token_id
        return {"wer": 100 * metric.compute(
            predictions=processor.batch_decode(pred.predictions, skip_special_tokens=True),
            references=processor.batch_decode(ids, skip_special_tokens=True))}

    trainer = Seq2SeqTrainer(
        args=Seq2SeqTrainingArguments(
            output_dir=str(OUTPUT), per_device_train_batch_size=args.batch,
            learning_rate=args.lr, num_train_epochs=args.epochs,
            gradient_checkpointing=True, fp16=torch.cuda.is_available(),
            eval_strategy="epoch", predict_with_generate=True,
            save_strategy="epoch", logging_steps=10, report_to=[],
        ),
        model=model, train_dataset=train, eval_dataset=test,
        data_collator=collate, compute_metrics=compute_metrics,
        processing_class=processor.feature_extractor,
    )
    trainer.train()
    trainer.save_model(str(OUTPUT))
    processor.save_pretrained(str(OUTPUT))
    print(f"\ntrained model saved to {OUTPUT}")
    convert(OUTPUT, CT2_OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
