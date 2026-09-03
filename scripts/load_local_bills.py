#!/usr/bin/env python
"""Fill the ledger from `Invoices/` using hand-transcribed extractions.

Same idea as `seed_demo.py`, but it walks a folder of real bills and looks
each one up by filename in `tests/local_bills.LEDGER`. Everything except the
model call is the real pipeline — rendering, persistence, the arithmetic
rules, party matching, brokerage — so what lands in the ledger is what a live
extraction would produce, minus the reading itself.

    python scripts/load_local_bills.py                 # load Invoices/
    python scripts/load_local_bills.py --reset         # clear the ledger first
    python scripts/load_local_bills.py ~/other-bills   # a different folder
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import clear_ledger, engine, init_db, session_scope  # noqa: E402
from app.extraction import llm, pipeline  # noqa: E402
from app.extraction.llm import ExtractionResult  # noqa: E402
from app.ingest.storage import store_file  # noqa: E402
from app.business.brokerage import compute_brokerage  # noqa: E402
from app.models import BrokerageRule, Document, Invoice, Party  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("load")

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "Invoices"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", type=Path, default=DEFAULT_DIR,
                    help=f"folder of bills (default {DEFAULT_DIR.name}/)")
    ap.add_argument("--reset", action="store_true", help="empty the ledger first")
    args = ap.parse_args()

    try:
        from tests.local_bills import LEDGER
    except ImportError as exc:
        log.error("tests/local_bills.py has no LEDGER to load: %s", exc)
        return 1

    if not args.folder.is_dir():
        log.error("%s is not a folder", args.folder)
        return 2

    init_db()

    if args.reset:
        with engine.begin() as conn:
            clear_ledger(conn)
        log.info("ledger cleared")

    # One reading only: a second pass would just replay the same transcription.
    settings.enable_crosscheck = False

    original = llm.extract_invoice
    loaded = failed = 0
    try:
        for path in sorted(args.folder.iterdir()):
            extracted = LEDGER.get(path.name)
            if extracted is None:
                if path.is_file() and not path.name.startswith("."):
                    log.warning("no transcription for %s — skipped", path.name)
                continue

            digest, stored, mime = store_file(path)
            with session_scope() as db:
                doc = db.scalar(select(Document).where(Document.sha256 == digest))
                if doc is None:
                    doc = Document(
                        sha256=digest, original_filename=path.name,
                        stored_path=str(stored), mime_type=mime,
                        size_bytes=path.stat().st_size, status="queued",
                        source="local_transcription",
                    )
                    db.add(doc)
                    db.flush()
                doc_id = doc.id

            llm.extract_invoice = lambda _e=extracted, **kw: ExtractionResult(
                invoice=_e, model="hand-transcribed", prompt_version="local",
                input_tokens=0, output_tokens=0, duration_ms=0,
                raw=_e.model_dump(mode="json"),
            )
            try:
                with session_scope() as db:
                    invoice = pipeline.process_document(db, doc_id)
                    flags = [f for f in invoice.flags if not f.resolved]
                    errors = [f for f in flags if f.severity == "error"]
                    log.info(
                        "%-46s invoice #%-3s %s  %s",
                        path.name[:46], invoice.id,
                        f"Rs {float(invoice.grand_total or 0):>13,.2f}",
                        "clean" if not flags else
                        f"{len(errors)} error(s), {len(flags) - len(errors)} warning(s)",
                    )
                loaded += 1
            except Exception as exc:  # noqa: BLE001
                log.error("%-46s FAILED: %s", path.name[:46], exc)
                failed += 1
    finally:
        llm.extract_invoice = original

    if loaded:
        apply_brokerage_rules()

    log.info("%d bill(s) loaded, %d failed", loaded, failed)
    return 1 if failed else 0


def apply_brokerage_rules() -> None:
    """Seed the rates the bills print, then re-accrue against them.

    `compute_brokerage` runs during the pipeline, before any rule exists, so
    every invoice first accrues at the default rate. Re-running it once the
    rules are in place is what puts the printed rate on the invoice that
    states one.
    """
    from tests.local_bills import BROKERAGE_RULES

    with session_scope() as db:
        for spec in BROKERAGE_RULES:
            if db.scalar(select(BrokerageRule).where(BrokerageRule.name == spec["name"])):
                continue
            seller = db.scalar(select(Party).where(Party.gstin == spec["seller_gstin"]))
            if seller is None:
                log.warning("no party for %s — rule '%s' skipped",
                            spec["seller_gstin"], spec["name"])
                continue
            db.add(BrokerageRule(
                name=spec["name"], seller_id=seller.id, rate_pct=spec["rate_pct"],
                basis=spec["basis"], payable_by=spec["payable_by"],
                notes=spec["notes"], is_active=True,
            ))
            log.info("brokerage rule: %s", spec["name"])

    with session_scope() as db:
        for invoice in db.scalars(select(Invoice)):
            compute_brokerage(db, invoice)


if __name__ == "__main__":
    raise SystemExit(main())
