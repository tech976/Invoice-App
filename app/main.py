"""FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.reports import router as reports_router
from app.api.voice import router as voice_router
from app.api.routes import router as api_router
from app.config import BASE_DIR, settings
from app.db import get_db, init_db
from app.worker import requeue_stuck, start_workers, stop_workers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("invoice-app")

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    recovered = requeue_stuck()
    if recovered:
        log.info("requeued %s job(s) orphaned by a previous shutdown", recovered)
    start_workers()
    if not settings.anthropic_api_key:
        log.warning(
            "ANTHROPIC_API_KEY is not set — uploads will queue but extraction "
            "will fail. Add the key to .env and restart."
        )
    log.info("ready on http://127.0.0.1:8000")
    yield
    stop_workers()


app = FastAPI(
    title="Invoice Ledger",
    description="Reads trade bills of any format and keeps them as structured data.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(api_router)
app.include_router(reports_router)
app.include_router(voice_router)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):  # pragma: no cover
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
    )


@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict:
    from sqlalchemy import text

    db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "database": str(db.bind.url.render_as_string(hide_password=True)),
        "model": settings.extraction_model,
        "api_key_configured": bool(settings.anthropic_api_key),
        "workers": settings.worker_threads,
    }


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


def _page(request: Request, name: str, **context):
    return templates.TemplateResponse(
        request=request, name=name, context={"settings": settings, **context}
    )


@app.get("/")
def page_dashboard(request: Request):
    return _page(request, "dashboard.html", title="Dashboard", nav="dashboard")


@app.get("/upload")
def page_upload(request: Request):
    return _page(request, "upload.html", title="Upload Bills", nav="upload")


@app.get("/speak")
def page_speak(request: Request):
    return _page(request, "speak.html", title="Book a Trade", nav="speak")


@app.get("/training")
def page_training(request: Request):
    return _page(request, "training.html", title="Teach the recogniser", nav="training")


@app.get("/trades")
def page_trades(request: Request):
    return _page(request, "trades.html", title="Trades", nav="trades")


@app.get("/invoices")
def page_invoices(request: Request):
    return _page(request, "invoices.html", title="Invoices", nav="invoices")


@app.get("/invoices/{invoice_id}")
def page_invoice(request: Request, invoice_id: int):
    return _page(
        request, "invoice_detail.html", title=f"Invoice #{invoice_id}",
        nav="invoices", invoice_id=invoice_id,
    )


@app.get("/review")
def page_review(request: Request):
    return _page(request, "review.html", title="Review Queue", nav="review")


@app.get("/parties")
def page_parties(request: Request):
    return _page(request, "parties.html", title="Parties", nav="parties")


@app.get("/parties/{party_id}")
def page_party(request: Request, party_id: int):
    return _page(
        request, "party_detail.html", title="Party", nav="parties", party_id=party_id
    )


@app.get("/reports")
def page_reports(request: Request):
    return _page(request, "reports.html", title="Reports", nav="reports")
