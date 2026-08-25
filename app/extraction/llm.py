"""Claude-backed structured extraction.

The PDF itself goes to the model as a document block, so Claude sees the
rendered pages *and* whatever text it can recover — that combination is what
lets one code path handle a clean TallyPrime export, a Crystal Reports dump
with scrambled columns, and a bill whose font map is broken, without a
per-vendor template anywhere.
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import anthropic

from app.config import settings
from app.extraction.prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
from app.extraction.result import (
    ConfigurationError, ExtractionError, ExtractionResult,
)
from app.schemas import ExtractedInvoice

log = logging.getLogger(__name__)

# API limits for a base64 document block.
MAX_PDF_BYTES = 32 * 1024 * 1024
MAX_PDF_PAGES = 100
# Enough headroom that a long text layer never crowds out the pages.
MAX_TEXT_CHARS = 120_000

# Defined in `result` so the local reader can return them without
# importing this module, and with it the API client.
ExtractionError = ExtractionError
ConfigurationError = ConfigurationError
ExtractionResult = ExtractionResult


def _client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise ConfigurationError(
            "ANTHROPIC_API_KEY is not set. Add it to the .env file at the "
            "project root, then restart the app."
        )
    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=600.0,
        max_retries=3,
    )


def _b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("utf-8")


def build_content_blocks(
    *,
    doc_path: Path,
    mime_type: str,
    page_images: list[Path],
    text_layer: str,
    ocr_text: str,
    text_quality: float,
    route: str,
    page_count: int,
) -> list[dict]:
    """Assemble the user turn: the document, then whatever text supports it."""
    blocks: list[dict] = [
        {
            "type": "text",
            "text": build_user_prompt(
                filename=doc_path.name,
                page_count=page_count,
                text_quality=text_quality,
                route=route,
            ),
        }
    ]

    size = doc_path.stat().st_size
    sent_native_pdf = False

    if mime_type == "application/pdf" and size <= MAX_PDF_BYTES and page_count <= MAX_PDF_PAGES:
        blocks.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": _b64(doc_path),
                },
                "title": doc_path.name,
            }
        )
        sent_native_pdf = True
    else:
        # Oversized, image upload, or too many pages: send rendered pages.
        if not page_images:
            raise ExtractionError(
                f"{doc_path.name} cannot be sent as a PDF "
                f"({size / 1e6:.1f} MB, {page_count} pages) and no page images "
                "were rendered."
            )
        for idx, img in enumerate(page_images, start=1):
            blocks.append({"type": "text", "text": f"--- page {idx} ---"})
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": _b64(img),
                    },
                }
            )

    if route == "text_layer" and text_layer.strip():
        blocks.append(
            {
                "type": "text",
                "text": (
                    "TEXT LAYER (verbatim from the PDF, column alignment "
                    "approximate):\n\n" + text_layer[:MAX_TEXT_CHARS]
                ),
            }
        )
    elif ocr_text.strip():
        blocks.append(
            {
                "type": "text",
                "text": (
                    "OCR TEXT (local tesseract pass — a hint, not authoritative; "
                    "verify every digit against the page):\n\n"
                    + ocr_text[:MAX_TEXT_CHARS]
                ),
            }
        )

    if sent_native_pdf and not page_images:
        log.debug("%s sent as native PDF with no rendered fallback", doc_path.name)

    return blocks


def _call(client: anthropic.Anthropic, *, model: str, blocks: list[dict]) -> tuple:
    """Call the API, shedding parameters the model does not accept.

    Thinking and effort configuration differ across model generations. Rather
    than pin the app to one generation, try the richest request first and drop
    whatever the API rejects, so switching EXTRACTION_MODEL keeps working.
    """
    attempts: list[dict] = [
        {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": settings.extraction_effort},
        },
        {"output_config": {"effort": settings.extraction_effort}},
        {"thinking": {"type": "adaptive"}},
        {},
    ]

    last_exc: Exception | None = None
    for extra in attempts:
        try:
            return (
                client.messages.parse(
                    model=model,
                    max_tokens=settings.extraction_max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": blocks}],
                    output_format=ExtractedInvoice,
                    **extra,
                ),
                extra,
            )
        except anthropic.BadRequestError as exc:
            msg = str(exc)
            # Only param-shape complaints are worth retrying differently.
            if not any(k in msg.lower() for k in ("thinking", "effort", "output_config")):
                raise
            log.warning("retrying without %s: %s", list(extra), msg[:200])
            last_exc = exc

    raise ConfigurationError(
        f"Model '{model}' rejected every parameter combination. Check "
        f"EXTRACTION_MODEL in .env. Last error: {last_exc}"
    )


def extract_invoice(
    *,
    doc_path: Path,
    mime_type: str,
    page_images: list[Path],
    text_layer: str = "",
    ocr_text: str = "",
    text_quality: float = 0.0,
    route: str = "text_layer",
    page_count: int = 1,
    model: str | None = None,
) -> ExtractionResult:
    """Read one document into an `ExtractedInvoice`."""
    client = _client()
    model = model or settings.extraction_model

    blocks = build_content_blocks(
        doc_path=doc_path,
        mime_type=mime_type,
        page_images=page_images,
        text_layer=text_layer,
        ocr_text=ocr_text,
        text_quality=text_quality,
        route=route,
        page_count=page_count,
    )

    started = time.time()
    try:
        response, used_params = _call(client, model=model, blocks=blocks)
    except anthropic.APIStatusError as exc:
        # 4xx means the request itself is wrong; 5xx and 429 are worth another go.
        error = ExtractionError(f"Claude API error {exc.status_code}: {exc}")
        if 400 <= exc.status_code < 500 and exc.status_code not in (408, 409, 429):
            error = ConfigurationError(str(error))
        raise error from exc
    except anthropic.APIConnectionError as exc:
        raise ExtractionError(f"Could not reach the Claude API: {exc}") from exc
    duration_ms = int((time.time() - started) * 1000)

    # A safety refusal returns HTTP 200 with an empty content list, so check
    # the stop reason before touching parsed_output.
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        raise ConfigurationError("The model declined to process this document.")
    if stop_reason == "max_tokens":
        raise ExtractionError(
            "Extraction hit the output token limit — the bill has more rows "
            "than EXTRACTION_MAX_TOKENS allows. Raise it and retry."
        )

    parsed = getattr(response, "parsed_output", None)
    if parsed is None:
        raise ExtractionError(f"Model returned no structured output (stop={stop_reason}).")

    usage = getattr(response, "usage", None)
    log.info(
        "extracted %s via %s in %dms (params=%s)",
        doc_path.name, model, duration_ms, list(used_params) or "base",
    )

    return ExtractionResult(
        invoice=parsed,
        model=model,
        prompt_version=PROMPT_VERSION,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        duration_ms=duration_ms,
        raw=parsed.model_dump(mode="json"),
        stop_reason=stop_reason,
    )
