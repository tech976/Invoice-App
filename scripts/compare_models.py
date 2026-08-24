#!/usr/bin/env python
"""Score models against hand-transcribed ground truth.

Answers "is the cheap model good enough for my bills" with evidence instead
of opinion. Each model reads the same PDFs; every field is compared to a
transcription typed by hand from the original, and scored in two groups:

  * CHECKED  - fields the validation rules would catch if wrong
  * UNCHECKED - fields nothing verifies, where an error posts silently

The second number is the one that decides whether a model is safe here.

    python scripts/compare_models.py
    python scripts/compare_models.py --models claude-haiku-4-5,claude-sonnet-5
    python scripts/compare_models.py --repeat 3      # check run-to-run stability
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extraction import llm  # noqa: E402
from app.extraction.normalize import (  # noqa: E402
    clean_gstin, clean_text, normalize_hsn, parse_amount, parse_date,
)
from app.extraction.pdf_text import extract_pdf_text, score_text  # noqa: E402
from app.extraction.pricing import cost_usd  # noqa: E402
from app.extraction.ocr import ocr_image  # noqa: E402
from app.extraction.rasterize import render_pdf_pages  # noqa: E402
from app.config import settings  # noqa: E402
from tests.fixtures import SAMPLES  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

SEARCH_DIRS = [Path.home() / "Downloads", Path(__file__).resolve().parent.parent / "samples"]

# ---------------------------------------------------------------------------
# What we compare, and how.
#
# "checked" mirrors what app/validation/rules.py can actually verify. Anything
# marked unchecked would reach the ledger unchallenged if the model got it
# wrong, so it carries the real risk.
# ---------------------------------------------------------------------------

def money(v):
    a = parse_amount(v)
    return None if a is None else a.quantize(Decimal("0.01"))

def date_(v):
    return parse_date(v)

def text(v):
    t = clean_text(v)
    return t.lower() if t else None

def gstin(v):
    return clean_gstin(v)

def hsn(v):
    return normalize_hsn(v)


INVOICE_FIELDS = [
    # (path, label, normaliser, checked_by_rules)
    ("invoice_number",   "invoice no",      text,   False),
    ("invoice_date",     "invoice date",    date_,  False),
    ("seller.name",      "seller name",     text,   False),
    ("seller.gstin",     "seller GSTIN",    gstin,  True),
    ("buyer.name",       "buyer name",      text,   False),
    ("buyer.gstin",      "buyer GSTIN",     gstin,  True),
    ("broker_name",      "broker",          text,   False),
    ("payment_terms",    "payment terms",   text,   False),
    ("taxable_value",    "taxable value",   money,  True),
    ("cgst_amount",      "CGST",            money,  True),
    ("sgst_amount",      "SGST",            money,  True),
    ("igst_amount",      "IGST",            money,  True),
    ("round_off",        "round off",       money,  True),
    ("grand_total",      "grand total",     money,  True),
    ("total_quantity",   "total quantity",  money,  True),
    ("amount_in_words",  "amount in words", text,   True),
    ("eway_bill.eway_bill_no",     "e-way bill no",  text, False),
    ("eway_bill.vehicle_no",       "vehicle no",     text, False),
    ("eway_bill.transporter_name", "transporter",    text, False),
]

LINE_FIELDS = [
    ("description",    "description", text,  False),
    ("item_remarks",   "grade",       text,  False),
    ("hsn",            "HSN",         hsn,   True),
    ("quantity",       "quantity",    money, True),
    ("rate",           "rate",        money, True),
    ("discount_pct",   "discount %",  money, True),
    ("taxable_amount", "line amount", money, True),
    ("bags",           "bags",        money, False),
]


def dig(obj, path):
    for part in path.split("."):
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    return obj


def compare(truth, got):
    """Yield (label, checked, ok, expected, actual) for every field."""
    for path, label, norm, checked in INVOICE_FIELDS:
        e, a = norm(dig(truth, path)), norm(dig(got, path))
        yield label, checked, e == a, e, a

    n = max(len(truth.lines), len(got.lines))
    for i in range(n):
        t_line = truth.lines[i] if i < len(truth.lines) else None
        g_line = got.lines[i] if i < len(got.lines) else None
        for path, label, norm, checked in LINE_FIELDS:
            e = norm(getattr(t_line, path, None)) if t_line else None
            a = norm(getattr(g_line, path, None)) if g_line else None
            yield f"line {i + 1} {label}", checked, e == a, e, a


def locate(filename):
    for folder in SEARCH_DIRS:
        p = folder / filename
        if p.exists():
            return p
    return None


def prepare(path):
    """Same routing the pipeline uses, so the comparison is like-for-like."""
    pdf = extract_pdf_text(path)
    images = render_pdf_pages(path, Path("/tmp") / f"cmp-{path.stem[:20]}")
    route = "text_layer" if pdf.quality >= settings.text_quality_threshold else "ocr_vision"
    ocr = ""
    if route == "ocr_vision":
        ocr = "\n\n".join(f"--- page {i} ---\n{ocr_image(p)}" for i, p in enumerate(images, 1))
    return dict(
        doc_path=path, mime_type="application/pdf", page_images=images,
        text_layer=pdf.full_text, ocr_text=ocr, text_quality=pdf.quality,
        route=route, page_count=pdf.page_count,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="claude-haiku-4-5,claude-sonnet-5,claude-opus-5")
    ap.add_argument("--repeat", type=int, default=1,
                    help="runs per bill, to see whether a model is stable")
    ap.add_argument("--verbose", action="store_true", help="list every mismatch")
    args = ap.parse_args()

    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set — add it to .env first.")
        return 1

    models = [m.strip() for m in args.models.split(",") if m.strip()]

    bills = []
    for filename, truth in SAMPLES.items():
        path = locate(filename)
        if path is None:
            print(f"skipping {filename} — not found")
            continue
        bills.append((filename, path, truth, prepare(path)))

    if not bills:
        print("no sample bills found")
        return 1

    print(f"\n{len(bills)} bill(s) x {len(models)} model(s) x {args.repeat} run(s)\n")

    results = {}
    for model in models:
        tally = defaultdict(lambda: [0, 0])     # group -> [ok, total]
        mismatches = defaultdict(int)
        tokens_in = tokens_out = 0
        failures = 0

        for filename, path, truth, prepped in bills:
            for run in range(args.repeat):
                try:
                    r = llm.extract_invoice(model=model, **prepped)
                except Exception as exc:  # noqa: BLE001
                    print(f"  {model} failed on {filename}: {exc}")
                    failures += 1
                    continue
                tokens_in += r.input_tokens
                tokens_out += r.output_tokens

                for label, checked, ok, exp, act in compare(truth, r.invoice):
                    group = "checked" if checked else "unchecked"
                    tally[group][1] += 1
                    tally[group][0] += ok
                    if not ok:
                        mismatches[f"{filename[:18]} · {label}"] += 1
                        if args.verbose:
                            print(f"    {model} {filename[:18]} {label}: "
                                  f"expected {exp!r} got {act!r}")

        runs = max(1, len(bills) * args.repeat - failures)
        cost = cost_usd(model, tokens_in, tokens_out)
        results[model] = {
            "checked": tally["checked"], "unchecked": tally["unchecked"],
            "mismatches": mismatches, "failures": failures,
            "cost_per_bill": (cost / runs) if cost else None,
            "tokens": (tokens_in // runs, tokens_out // runs),
        }

    # ---- report ----
    print(f"{'model':<22}{'CHECKED':>12}{'UNCHECKED':>12}{'tokens in/out':>18}{'$/bill':>10}")
    print("-" * 74)
    for model, r in results.items():
        c_ok, c_n = r["checked"]
        u_ok, u_n = r["unchecked"]
        t_in, t_out = r["tokens"]

        checked_col = f"{c_ok}/{c_n} {100 * c_ok / max(c_n, 1):.0f}%"
        unchecked_col = f"{u_ok}/{u_n} {100 * u_ok / max(u_n, 1):.0f}%"
        tokens_col = f"{t_in:,}/{t_out:,}"
        cost_col = f"${r['cost_per_bill']:.4f}" if r["cost_per_bill"] else "n/a"

        print(f"{model:<22}{checked_col:>12}{unchecked_col:>12}"
              f"{tokens_col:>18}{cost_col:>10}")

    print("\nUNCHECKED is the number that matters: those fields reach the ledger")
    print("unchallenged. A model at 100% CHECKED but 90% UNCHECKED is not safe here.\n")

    for model, r in results.items():
        if r["mismatches"]:
            print(f"{model} got wrong:")
            for key, n in sorted(r["mismatches"].items(), key=lambda kv: -kv[1]):
                print(f"    {key}" + (f"  (x{n})" if n > 1 else ""))
            print()
        elif not r["failures"]:
            print(f"{model}: every field matched the transcription.\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
