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
    try:
        init_db()
    except Exception as exc:  # noqa: BLE001 - a missing database is a message
        # Raising here takes the whole function down, and every page then
        # reports that the server crashed — which says nothing about the
        # database not being attached yet. Far better to start, and let the
        # first request say what is actually wrong.
        log.error("could not reach the database at startup: %s", exc)
    if settings.serverless:
        # No thread outlives the response that started it, so there is no
        # queue to drain and nothing to recover. Bills are read inline by the
        # upload handler instead.
        log.info("serverless: reading bills inline, no background workers")
    else:
        recovered = requeue_stuck()
        if recovered:
            log.info("requeued %s job(s) orphaned by a previous shutdown", recovered)
        start_workers()
    if not settings.anthropic_api_key and settings.extraction_backend != "local":
        log.warning(
            "ANTHROPIC_API_KEY is not set and EXTRACTION_BACKEND is '%s' — "
            "uploads will queue but extraction will fail. Add the key, or set "
            "EXTRACTION_BACKEND=local to read bills on this machine.",
            settings.extraction_backend,
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

# Voice entry needs a speech model and a language model on the machine, which
# a serverless host has neither the disk nor the lifetime for. Imported only
# where it can actually run, so its dependencies stay out of that build.
if not settings.serverless:
    try:
        from app.api.voice import router as voice_router

        app.include_router(voice_router)
    except ImportError as exc:  # noqa: BLE001 - the ledger stands on its own
        # A build without the speech stack should serve the ledger rather
        # than fail to start over a feature it was never meant to carry.
        log.warning("voice entry unavailable (%s); ledger only", exc)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):  # pragma: no cover
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
    )


@app.get("/api/health")
def health() -> JSONResponse:
    """Whether this deployment can actually work, and if not, what is missing.

    Deliberately does not depend on a database session: the one moment this
    endpoint matters most is when the database is the thing that is wrong,
    and an endpoint that cannot answer then is no use.
    """
    from sqlalchemy import text

    from app.db import engine

    info = {
        "status": "ok",
        "serverless": settings.serverless,
        "extraction_backend": settings.extraction_backend,
        "database": engine.url.render_as_string(hide_password=True),
    }
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - the report is the point
        info["status"] = "no_database"
        info["error"] = f"{type(exc).__name__}: {exc}"[:300]
        info["fix"] = (
            "No database is attached. In the Vercel dashboard open Storage, "
            "create a Postgres database, connect it to this project, then "
            "redeploy."
        )
        return JSONResponse(status_code=503, content=info)
    return JSONResponse(info)


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


# The voice screens exist only where voice entry does. Serving them on a host
# with no recogniser would give a microphone button that cannot work.
if not settings.serverless:

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
