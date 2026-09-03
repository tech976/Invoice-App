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
        _add_missing_columns(models.Base)
        _schema_ready = True
        log.info("schema ensured on %s", engine.url.render_as_string(hide_password=True))


def _add_missing_columns(base) -> None:
    """Add columns the models have gained since the tables were made.

    `create_all` creates missing *tables* and leaves existing ones exactly as
    they are, so a column added to a model after a database already exists is
    simply never created — and every query naming it fails with `no such
    column`, which reads like a bug in the query rather than a database a
    version behind.

    Only additive, and only nullable columns without defaults: this fills in
    what is missing and never rewrites, drops or retypes anything, so it
    cannot lose data. Anything more than that wants a real migration.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table in base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have or column.primary_key:
                continue
            if not column.nullable:
                log.warning(
                    "%s.%s is missing and not nullable; leaving it to a migration",
                    table.name, column.name,
                )
                continue
            ddl = column.type.compile(engine.dialect)
            with engine.begin() as conn:
                conn.execute(text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl}'
                ))
            log.info("added missing column %s.%s (%s)", table.name, column.name, ddl)


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
