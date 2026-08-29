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

try:
    subprocess.run(["createdb", TEST_DB], capture_output=True)
except FileNotFoundError:
    # No Postgres on this machine. The tests that need one skip themselves
    # below; the ones that are pure logic — numerals, dates, the spoken-trade
    # parser — still run, which is what makes them useful on a laptop.
    pass

import pytest  # noqa: E402

from app.db import SessionLocal, clear_ledger, engine  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(scope="session")
def schema():
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    except Exception as exc:  # noqa: BLE001 - absence of Postgres is not a failure
        pytest.skip(f"no Postgres available: {exc}", allow_module_level=True)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def db(schema):
    """A clean database for each test."""
    with engine.begin() as conn:
        clear_ledger(conn)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
