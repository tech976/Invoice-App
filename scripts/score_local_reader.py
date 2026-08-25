#!/usr/bin/env python
"""Score the local reader against the hand-transcribed bills.

Same question `compare_models.py` asks of a model, asked of the reader that
needs no model: how much of what it produced matches a transcription typed by
hand from the original, split into

  * CHECKED   - fields the validation rules would catch if wrong
  * UNCHECKED - fields nothing verifies, where an error posts silently

The second number is the one that decides whether the reader is safe to use.

    python scripts/score_local_reader.py
    python scripts/score_local_reader.py --verbose
"""
from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extraction.local.reader import extract_invoice  # noqa: E402
from app.extraction.normalize import clean_gstin, normalize_hsn, parse_date  # noqa: E402

logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")

BILLS_DIR = Path(__file__).resolve().parent.parent / "Invoices"

# (label, dotted path, comparator, checked-by-the-rules?)
FIELDS = [
    ("invoice number",  "invoice_number",        "text",  True),
    ("invoice date",    "invoice_date",          "date",  False),
    ("seller gstin",    "seller.gstin",          "gstin", True),
    ("seller name",     "seller.name",           "text",  False),
    ("buyer gstin",     "buyer.gstin",           "gstin", True),
    ("buyer name",      "buyer.name",            "text",  False),
    ("broker",          "broker_name",           "text",  False),
    ("irn",             "irn",                   "text",  True),
    ("taxable value",   "taxable_value",         "money", True),
    ("cgst",            "cgst_amount",           "money", True),
    ("sgst",            "sgst_amount",           "money", True),
    ("igst",            "igst_amount",           "money", True),
    ("round off",       "round_off",             "money", True),
    ("grand total",     "grand_total",           "money", True),
    ("amount in words", "amount_in_words",       "words", True),
    ("line count",      "#lines",                "count", True),
    ("line 1 hsn",      "lines.0.hsn",           "hsn",   True),
    ("line 1 qty",      "lines.0.quantity",      "money", True),
    ("line 1 rate",     "lines.0.rate",          "money", True),
    ("line 1 amount",   "lines.0.taxable_amount", "money", True),
    ("charge count",    "#charges",              "count", True),
]


def dig(obj, path: str):
    if path == "#lines":
        return len(obj.lines)
    if path == "#charges":
        return len(obj.charges)
    current = obj
    for part in path.split("."):
        if current is None:
            return None
        if part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            current = getattr(current, part, None)
    return current


def same(kind: str, want, got) -> bool:
    if want in (None, "", []) and got in (None, "", []):
        return True
    if want is None or got is None:
        return False
    if kind == "money":
        return abs(Decimal(str(want)) - Decimal(str(got))) <= Decimal("1.0")
    if kind == "count":
        return int(want) == int(got)
    if kind == "gstin":
        return clean_gstin(want) == clean_gstin(got)
    if kind == "hsn":
        return normalize_hsn(str(want)) == normalize_hsn(str(got))
    if kind == "date":
        return parse_date(want) == parse_date(got)
    if kind == "words":
        return _squash(want) == _squash(got)
    return _squash(want) == _squash(got)


def _squash(text) -> str:
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show every field, not just the misses")
    args = ap.parse_args()

    try:
        from tests.local_bills import LEDGER
    except ImportError as exc:
        print(f"no ground truth available: {exc}")
        return 1

    totals = {"checked": [0, 0], "unchecked": [0, 0]}
    for filename, truth in sorted(LEDGER.items()):
        path = BILLS_DIR / filename
        if not path.exists():
            continue
        print(f"\n{filename}")
        try:
            got = extract_invoice(doc_path=path).invoice
        except Exception as exc:  # noqa: BLE001
            print(f"   FAILED: {exc}")
            continue

        for label, path_expr, kind, checked in FIELDS:
            want, mine = dig(truth, path_expr), dig(got, path_expr)
            ok = same(kind, want, mine)
            bucket = "checked" if checked else "unchecked"
            totals[bucket][1] += 1
            totals[bucket][0] += int(ok)
            if not ok:
                print(f"   MISS  {label:16} want={want!r:38} got={mine!r}")
            elif args.verbose:
                print(f"   ok    {label:16} {mine!r}")

    print("\n" + "=" * 62)
    for bucket in ("checked", "unchecked"):
        hit, total = totals[bucket]
        pct = 100.0 * hit / total if total else 0.0
        print(f"  {bucket.upper():9} {hit:>3}/{total:<3} {pct:5.1f}%")
    print("\n  UNCHECKED is the number that matters: those fields reach the")
    print("  ledger with nothing to catch them if they are wrong.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
