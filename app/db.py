"""Database engine and session management."""
from __future__ import annotations

import logging
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


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background workers and scripts."""
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
    from app import models  # noqa: F401  (registers mappers)

    models.Base.metadata.create_all(engine)
    log.info("schema ensured on %s", engine.url.render_as_string(hide_password=True))


def clear_ledger(connection) -> None:
    """Empty every table, on either database.

    Postgres takes one TRUNCATE across the lot: CASCADE settles the foreign
    keys whatever order the tables come in, and RESTART IDENTITY puts the
    sequences back to 1 so a reseeded ledger numbers from the top.

    SQLite has no TRUNCATE and no CASCADE. DELETE in reverse dependency order
    is the equivalent — children before parents, so no foreign key is ever
    left dangling — and clearing `sqlite_sequence` is what restarts AUTOINCREMENT.
    That table only exists once something has AUTOINCREMENT, hence the guard.
    """
    from app.models import Base

    tables = list(reversed(Base.metadata.sorted_tables))

    if connection.dialect.name == "postgresql":
        for table in tables:
            connection.exec_driver_sql(
                f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE')
        return

    for table in tables:
        connection.exec_driver_sql(f'DELETE FROM "{table.name}"')
    if connection.dialect.name == "sqlite":
        exists = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).first()
        if exists:
            connection.exec_driver_sql("DELETE FROM sqlite_sequence")
