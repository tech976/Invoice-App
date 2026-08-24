#!/usr/bin/env python
"""Load the three sample bills using hand-transcribed extractions.

Lets you see the interface populated with real data before an API key is
configured. Everything except the model call is the real pipeline, so what
appears is exactly what a live extraction would produce.

    python scripts/seed_demo.py            # add the samples
    python scripts/seed_demo.py --reset    # clear the ledger first
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402

from app.db import engine, init_db, session_scope  # noqa: E402
from app.extraction import llm, pipeline  # noqa: E402
from app.extraction.llm import ExtractionResult  # noqa: E402
from app.ingest.storage import store_file  # noqa: E402
from app.models import Base, Document  # noqa: E402
from tests.fixtures import SAMPLES  # noqa: E402

try:
    from tests import local_bills  # noqa: E402
except ImportError:
    local_bills = None

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("seed")

def locate(slug: str) -> Path | None:
    """The PDF for a layout slug.

    The bills themselves are not in the repository — they are third-party
    business documents. `tests/local_bills.py` points at them locally; see
    `tests/local_bills.example.py`.
    """
    if local_bills is None:
        return None
    return local_bills.path_for(slug)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="empty the ledger first")
    args = ap.parse_args()

    init_db()

    if args.reset:
        with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))
        log.info("ledger cleared")

    # Stand in for the API call with the transcribed extraction for each file.
    original = llm.extract_invoice
    loaded = 0

    try:
        if local_bills is None:
            log.error("tests/local_bills.py is not configured — copy "
                      "tests/local_bills.example.py and point it at your PDFs.")
            return 1

        for slug, extracted in SAMPLES.items():
            path = locate(slug)
            if path is None:
                log.warning("skipped '%s' — no PDF configured for it", slug)
                continue
            filename = path.name

            digest, stored, mime = store_file(path)
            with session_scope() as db:
                if db.scalar(select(Document).where(Document.sha256 == digest)):
                    log.info("already loaded: %s", filename)
                    continue
                doc = Document(
                    sha256=digest, original_filename=filename, stored_path=str(stored),
                    mime_type=mime, size_bytes=path.stat().st_size,
                    status="queued", source="demo_seed",
                )
                db.add(doc)
                db.flush()
                doc_id = doc.id

            llm.extract_invoice = lambda _e=extracted, **kw: ExtractionResult(
                invoice=_e, model="demo-fixture", prompt_version="seed",
                input_tokens=0, output_tokens=0, duration_ms=0,
                raw=_e.model_dump(mode="json"),
            )
            with session_scope() as db:
                invoice = pipeline.process_document(db, doc_id)
                flags = [f for f in invoice.flags if not f.resolved]
                log.info(
                    "loaded %-46s invoice #%s  %s  %s",
                    filename[:46], invoice.id,
                    f"Rs {float(invoice.grand_total or 0):>13,.2f}",
                    "clean" if not flags else f"{len(flags)} flag(s)",
                )
            loaded += 1
    finally:
        llm.extract_invoice = original

    log.info("%d sample bill(s) loaded — open http://127.0.0.1:8000", loaded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
