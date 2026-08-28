"""Database engine and session management."""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

log = logging.getLogger(__name__)

_connect_args = {}
if settings.database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
    connect_args=_connect_args,
)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _rec):  # pragma: no cover - env specific
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


_schema_lock = threading.Lock()
_schema_ready = False


def ensure_schema() -> None:
    """Create the tables once per process, on first use.

    Startup is not a reliable place to do this on a serverless host. The
    runtime there may never run ASGI lifespan events at all, in which case
    nothing has created the tables and every query fails on a table that is
    not there — which looks like the function crashing rather than like a
    database that was never set up.

    So it is done on the first request instead, guarded so that concurrent
    requests in the same process do it once between them.
    """
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        from app import models  # noqa: F401  (registers mappers)

        models.Base.metadata.create_all(engine)
        _schema_ready = True
        log.info("schema ensured on %s", engine.url.render_as_string(hide_password=True))


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    ensure_schema()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background workers and scripts."""
    ensure_schema()
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create the schema eagerly. Safe to call more than once."""
    ensure_schema()
