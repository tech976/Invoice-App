#!/usr/bin/env python
"""Freeze the interface into a folder of files, with no server behind it.

A demo of the screens does not need the reader, the queue or a database. It
needs the screens. So this runs the real application against synthetic data,
asks it for every page and every API reply those pages fetch, and writes the
answers to `public/` as ordinary files.

What comes out is the genuine interface — the same templates, the same CSS,
the same JavaScript, the same Indian digit grouping — served as static files.
It cannot crash, needs nothing attached to it, and costs nothing to host.

The data is the synthetic set from `tests/fixtures.py`: correct arithmetic and
valid GSTIN check digits, but invented companies. Real bills belong to
third-party suppliers and have no business on a public URL.

    python scripts/build_static.py

Then deploy the `public/` folder anywhere at all.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# A throwaway database, built fresh every time and thrown away after.
_TMP = Path(tempfile.mkdtemp(prefix="invoice-static-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/demo.db"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["SERVERLESS"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app.business.brokerage import compute_brokerage  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.extraction.persist import persist_invoice  # noqa: E402
from app.extraction.pipeline import revalidate  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Document, ExtractionRun  # noqa: E402
from tests.fixtures import (  # noqa: E402
    BROKEN_FONT_INVOICE, CRYSTAL_INVOICE, TALLY_INVOICE,
)

OUT = ROOT / "public"

# The smallest thing that is still a valid PDF. The invoice screen shows the
# original bill in an iframe, and a demo should show something there rather
# than a browser error — but not one of the real documents.
PLACEHOLDER_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 120]/Contents 4 0 R\
/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 74>>stream
BT /F1 11 Tf 22 66 Td (Original bill - not shown in this demo) Tj ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
trailer<</Root 1 0 R>>
"""


def seed() -> None:
    """Three invoices, posted through the real persistence and rules."""
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        for idx, extracted in enumerate(
            (TALLY_INVOICE, CRYSTAL_INVOICE, BROKEN_FONT_INVOICE), start=1
        ):
            doc = Document(
                sha256=f"{idx:064d}",
                original_filename=f"demo-bill-{idx}.pdf",
                stored_path=f"demo-{idx}.pdf",
                content=PLACEHOLDER_PDF,
                mime_type="application/pdf",
                size_bytes=len(PLACEHOLDER_PDF),
                page_count=1,
                status="extracted",
                text_quality=0.98,
                extraction_route="text_layer",
            )
            db.add(doc)
            db.flush()
            run = ExtractionRun(
                document_id=doc.id, engine="local", model="local-text-layer",
                pass_type="primary", status="done", duration_ms=480,
            )
            db.add(run)
            db.flush()
            invoice = persist_invoice(
                db, document=doc, extracted=extracted, extraction_run_id=run.id
            )
            revalidate(db, invoice)
            compute_brokerage(db, invoice)
        db.commit()
    finally:
        db.close()


def capture(client: TestClient) -> None:
    """Ask the running application for everything the screens need."""
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    invoice_ids = [i["id"] for i in client.get("/api/invoices").json()["invoices"]]
    party_ids = [p["id"] for p in client.get("/api/parties").json()["parties"]]

    pages = {
        "index.html": "/",
        "upload.html": "/upload",
        "invoices.html": "/invoices",
        "review.html": "/review",
        "parties.html": "/parties",
        "reports.html": "/reports",
    }
    for name, url in pages.items():
        (OUT / name).write_bytes(client.get(url).content)
    for i in invoice_ids:
        (OUT / f"invoices/{i}.html").parent.mkdir(parents=True, exist_ok=True)
        (OUT / f"invoices/{i}.html").write_bytes(client.get(f"/invoices/{i}").content)
    for p in party_ids:
        (OUT / f"parties/{p}.html").parent.mkdir(parents=True, exist_ok=True)
        (OUT / f"parties/{p}.html").write_bytes(client.get(f"/parties/{p}").content)

    # Every API reply the pages fetch, written where the JavaScript looks for
    # it. Query strings are dropped: a frozen demo answers the same either
    # way, and a file cannot vary by parameter.
    api = {
        "health": "/api/health",
        "invoices": "/api/invoices?limit=100",
        "parties": "/api/parties?limit=100",
        "documents": "/api/documents?limit=25",
        "reports/summary": "/api/reports/summary",
        "reports/review-queue": "/api/reports/review-queue",
        "reports/corrections": "/api/reports/corrections?limit=20",
    }
    for dim in ("seller", "buyer", "transporter", "broker"):
        api[f"reports/by-{dim}"] = f"/api/reports/by-{dim}?limit=20"
    for i in invoice_ids:
        api[f"invoices/{i}"] = f"/api/invoices/{i}"
    for p in party_ids:
        api[f"parties/{p}"] = f"/api/parties/{p}"

    for name, url in api.items():
        # Suffixed, because a file and a directory cannot share a name and
        # both /api/invoices and /api/invoices/1 are needed. vercel.json maps
        # the fetched path onto the suffixed file.
        target = OUT / "api" / f"{name}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        reply = client.get(url)
        target.write_text(json.dumps(reply.json(), indent=1))

    # The original bill, as shown in the iframe on the invoice screen.
    for i in invoice_ids:
        doc_id = client.get(f"/api/invoices/{i}").json()["document"]["id"]
        target = OUT / "api" / "documents" / str(doc_id) / "file.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PLACEHOLDER_PDF)

    shutil.copytree(ROOT / "app" / "static", OUT / "static")


def main() -> int:
    seed()
    with TestClient(app) as client:
        capture(client)
    files = sum(1 for _ in OUT.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"public/  {files} files, {size / 1024:.0f} KB")
    shutil.rmtree(_TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
