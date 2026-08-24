"""Test setup.

Runs against a real throwaway Postgres database rather than SQLite, so the
tests exercise the same dialect, constraints and Numeric behaviour as
production.
"""
from __future__ import annotations

import os
import subprocess

TEST_DB = "invoice_app_test"

# Must be set before anything imports app.config, which caches settings.
os.environ["DATABASE_URL"] = f"postgresql+psycopg://localhost/{TEST_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "")

subprocess.run(["createdb", TEST_DB], capture_output=True)

import pytest  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def db(schema):
    """A clean database for each test."""
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.exec_driver_sql(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE')
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
