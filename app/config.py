"""Application settings, loaded from environment / .env file."""
from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- database -------------------------------------------------------
    # Postgres is the default. Set DATABASE_URL=sqlite:///./data/invoices.db
    # to run without a Postgres server.
    database_url: str = "postgresql+psycopg://localhost/invoice_app"

    # --- storage --------------------------------------------------------
    data_dir: Path = BASE_DIR / "data"

    # --- llm extraction -------------------------------------------------
    anthropic_api_key: str | None = None
    # The workhorse. Sonnet 5 is strong enough for transcription and a
    # quarter the price of Opus; the escalation model below covers the bills
    # where that is not good enough. Measure before dropping to Haiku:
    #   python scripts/compare_models.py
    extraction_model: str = "claude-sonnet-5"
    extraction_effort: str = "high"
    extraction_max_tokens: int = 16000
    # A second, independent reading by a stronger model, used where the
    # arithmetic rules cannot help: they verify money, not identity. Set
    # ENABLE_CROSSCHECK=false to halve API cost and rely on one reading.
    enable_crosscheck: bool = True
    escalation_model: str = "claude-opus-5"
    # Bills at or above this rupee value always get a second reading.
    crosscheck_min_value: float = 1_000_000.0
    # So does anything the first reading was less than this sure about.
    crosscheck_min_confidence: float = 0.90

    # --- pipeline -------------------------------------------------------
    worker_threads: int = 2
    max_upload_mb: int = 32
    # Rendered page DPI for OCR / vision fallback.
    render_dpi: int = 200
    # Below this score the PDF text layer is considered unusable (garbled
    # fonts, scanned image) and the page images drive extraction.
    text_quality_threshold: float = 0.60
    # Rupee tolerance when reconciling invoice arithmetic.
    amount_tolerance: float = 1.0

    # --- business defaults ---------------------------------------------
    # Fallback brokerage rate (%) when no specific rule matches.
    default_brokerage_pct: float = 1.0
    home_state_code: str = "27"  # Maharashtra

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def pages_dir(self) -> Path:
        return self.data_dir / "pages"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.files_dir, self.pages_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


settings = get_settings()
