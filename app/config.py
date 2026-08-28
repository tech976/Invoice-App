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

    # --- serverless -----------------------------------------------------
    # Vercel and hosts like it set VERCEL=1. There the filesystem is read
    # only apart from /tmp, which is wiped between requests, and no thread
    # outlives the response that started it. So on such a host the bill is
    # kept in the database rather than on disk, it is read inline instead of
    # by a background worker, and page images are not rendered at all — the
    # local reader works off the PDF itself and the screen shows the PDF
    # itself, so nothing needs them.
    #
    # Nothing about the reading changes. The same text layer, the same QR,
    # the same arithmetic rules.
    #
    # VERCEL is set by the host itself and is reserved, so it cannot be set by
    # hand to test this path or to run the same build somewhere else. SERVERLESS
    # can, and either is enough.
    serverless: bool = bool(os.environ.get("VERCEL") or os.environ.get("SERVERLESS"))

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
        """Create the storage tree, where there is one to create.

        On a read-only host this is expected to fail and must not stop the
        app importing: nothing is kept on disk there. Anything that really
        needs a scratch file writes it under /tmp.
        """
        for d in (self.data_dir, self.files_dir, self.pages_dir, self.exports_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                if not self.serverless:
                    raise
                return

    @property
    def scratch_dir(self) -> Path:
        """Somewhere writable, whatever the host."""
        return Path("/tmp/invoice-app") if self.serverless else self.data_dir


# The names a hosted Postgres adds to the environment by itself. Vercel,
# Render, Railway and Heroku each pick a different one, and none of them is
# DATABASE_URL in the form SQLAlchemy wants.
_HOSTED_DB_VARS = (
    "DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL",
    "POSTGRES_URL_NON_POOLING", "POSTGRES_URL_NO_SSL",
)


def _sqlalchemy_url(raw: str) -> str:
    """Rewrite a hosted database URL into the form SQLAlchemy expects.

    A provider hands out `postgres://user:pass@host/db`, which SQLAlchemy
    refuses outright, or `postgresql://...`, which it accepts but then tries
    to open with psycopg2 — a driver this project does not install. Naming
    the driver is what makes either of them work.
    """
    for prefix in ("postgres://", "postgresql://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg://" + raw[len(prefix):]
    return raw


def _discover_database_url() -> str | None:
    """The database this host has already provisioned, if it has.

    Attaching a Postgres in the Vercel dashboard sets these automatically, so
    there is nothing to copy, paste or correct by hand — which is the whole
    difference between a two-click setup and a ten-step one.
    """
    for name in _HOSTED_DB_VARS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return _sqlalchemy_url(value)
    return None


# What the example file ships with, and the shapes people leave behind when
# they mean "not set".
_PLACEHOLDER_MARKS = ("your-key", "your_key", "changeme", "xxx", "...",
                      "here", "placeholder", "<", "example")


def _real_key(value: str | None) -> str | None:
    """A key, or None if it is obviously the placeholder from .env.example.

    An unset key and a fake one behave very differently: unset means no second
    reading is attempted at all, while `sk-ant-your-key-here` is a perfectly
    truthy string that buys a 401 and a couple of wasted seconds on every
    bill. Copying the example file and not editing it is the normal way to
    arrive here, so it is treated as what it means.
    """
    key = (value or "").strip()
    if not key:
        return None
    lowered = key.lower()
    if any(mark in lowered for mark in _PLACEHOLDER_MARKS):
        return None
    return key


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    found = _discover_database_url()
    if found:
        s.database_url = found
    s.anthropic_api_key = _real_key(s.anthropic_api_key)
    s.ensure_dirs()
    return s


settings = get_settings()
