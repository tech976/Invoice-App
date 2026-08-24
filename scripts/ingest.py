#!/usr/bin/env python
"""Bulk-ingest bills from a folder, without the browser.

    python scripts/ingest.py ~/Desktop/July-bills
    python scripts/ingest.py ~/bills --recursive --workers 3
    python scripts/ingest.py ~/bills --queue-only     # hand off to the web app

Useful for backfilling a year of archived bills in one go.
"""
from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.extraction.pipeline import process_document  # noqa: E402
from app.ingest.storage import ALLOWED_SUFFIXES, store_file  # noqa: E402
from app.models import Document  # noqa: E402
from app.worker import enqueue  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("ingest")


def find_bills(folder: Path, recursive: bool) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    return sorted(
        p for p in folder.glob(pattern)
        if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIXES and not p.name.startswith(".")
    )


def register(path: Path) -> tuple[int | None, str]:
    """Store the file and create its document row. Returns (id, outcome)."""
    try:
        digest, stored, mime = store_file(path)
    except ValueError as exc:
        return None, f"skipped ({exc})"

    with session_scope() as db:
        existing = db.scalar(select(Document).where(Document.sha256 == digest))
        if existing is not None:
            return existing.id, "already uploaded"
        doc = Document(
            sha256=digest,
            original_filename=path.name,
            stored_path=str(stored),
            mime_type=mime,
            size_bytes=path.stat().st_size,
            status="queued",
            source="cli",
        )
        db.add(doc)
        db.flush()
        return doc.id, "queued"


def extract(document_id: int) -> str:
    with session_scope() as db:
        invoice = process_document(db, document_id)
        if invoice is None:
            return "no invoice"
        flags = [f for f in invoice.flags if f.severity == "error" and not f.resolved]
        return (
            f"invoice #{invoice.id} {invoice.invoice_number or '(no number)'} "
            f"₹{float(invoice.grand_total or 0):,.2f} "
            + ("OK" if not flags else f"{len(flags)} check(s) failed")
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path, help="folder containing the bills")
    ap.add_argument("-r", "--recursive", action="store_true", help="include subfolders")
    ap.add_argument("-w", "--workers", type=int, default=2,
                    help="parallel extractions (default 2)")
    ap.add_argument("--queue-only", action="store_true",
                    help="register the files but leave extraction to the web app")
    args = ap.parse_args()

    if not args.folder.is_dir():
        log.error("%s is not a folder", args.folder)
        return 2

    init_db()
    bills = find_bills(args.folder, args.recursive)
    if not bills:
        log.warning("no PDFs or images found in %s", args.folder)
        return 0
    log.info("found %d bill(s) in %s", len(bills), args.folder)

    pending: list[tuple[int, str]] = []
    for path in bills:
        doc_id, outcome = register(path)
        log.info("  %-52s %s", path.name[:52], outcome)
        if doc_id and outcome == "queued":
            pending.append((doc_id, path.name))

    if args.queue_only:
        with session_scope() as db:
            for doc_id, _ in pending:
                enqueue(db, doc_id)
        log.info("%d document(s) queued — start the web app to process them", len(pending))
        return 0

    if not pending:
        log.info("nothing new to read")
        return 0

    if not settings.anthropic_api_key:
        log.error("ANTHROPIC_API_KEY is not set — cannot read the bills. "
                  "Add it to .env, or re-run with --queue-only.")
        return 1

    log.info("reading %d bill(s) with %d worker(s)...", len(pending), args.workers)
    ok = failed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(extract, doc_id): name for doc_id, name in pending}
        for future in as_completed(futures):
            name = futures[future]
            try:
                log.info("  %-52s %s", name[:52], future.result())
                ok += 1
            except Exception as exc:  # noqa: BLE001
                log.error("  %-52s FAILED: %s", name[:52], exc)
                failed += 1

    log.info("done — %d read, %d failed", ok, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
