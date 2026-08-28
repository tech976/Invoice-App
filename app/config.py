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

    # --- extraction -----------------------------------------------------
    # 'local' reads the PDF's own text layer and its e-invoice QR, with no
    # model and no network. 'claude' sends the document to the API. See
    # EXTRACTION_PLAN.md.
    extraction_backend: str = "local"

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

    # --- voice entry ----------------------------------------------------
    # Which recogniser model to load. 'small' is the smallest that copes with
    # Hindi/Marathi/Gujarati mixed with English; 'base' is faster and worse.
    speech_model: str = "small"
    # Left empty on purpose for stock Whisper: a sentence switching language
    # mid-way is transcribed better when the model decides for itself. Set it
    # to 'hi' or 'mr' when using a model fine-tuned on one language — those
    # have no detector and must be told. Note that such a model returns
    # Devanagari, so names are stored in that script.
    speech_language: str = ""
    max_audio_mb: int = 10
    # Measured on this pipeline, a wider beam bought no accuracy on a
    # five-second utterance and cost 0.6s of it. Greedy decoding it is.
    speech_beam_size: int = 1
    # Filter, denoise and level the recording before the model hears it. A
    # market floor is noisy and this costs milliseconds; measure it off and on
    # with scripts/bench_speech.py on your own recordings before trusting it.
    # Off by default: measured on and off it changed no field on any clip,
    # and it costs time on every one. Turn it on if a real market recording
    # shows it earning its keep — scripts/bench_speech.py --clean both.
    speech_clean_audio: bool = False
    # How many cores the models may use. Left below the core count on
    # purpose: the web server still has to answer while one is running.
    cpu_threads: int = 4

    # --- local language model -------------------------------------------
    # Ollama on this machine. It binds to localhost and the weights are a
    # file on disk, so nothing leaves the server.
    ollama_url: str = "http://127.0.0.1:11434"
    nlp_model: str = "qwen2.5:3b-instruct-q4_K_M"
    # A trade is a small object. Capping the reply is what keeps a confused
    # model from spending half a minute on it.
    nlp_max_tokens: int = 160
    nlp_timeout: float = 60.0
    # How long Ollama holds the model in memory after a request.
    nlp_keep_alive: str = "30m"
    # 'llm' reads the sentence with the model; 'rules' uses the parser beside
    # it, which is instant but only knows the words it was given.
    nlp_backend: str = "llm"

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
