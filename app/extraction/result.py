"""What a reading of a document produces, whichever reader produced it.

Kept apart from `llm.py` on purpose. Both the local reader and the API reader
return these, and if they lived beside the API client then importing the local
reader would drag the Anthropic SDK in with it — on a deployment that never
calls an API at all.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.schemas import ExtractedInvoice


class ExtractionError(RuntimeError):
    """A failure while reading a document.

    `retryable` tells the worker whether another attempt could succeed. A rate
    limit or a dropped connection is worth retrying; a missing model or a
    document with no readable text will fail identically every time, and
    retrying it just buries the real message under three copies of itself.
    """

    retryable = True


class ConfigurationError(ExtractionError):
    """Something about the setup is wrong. Retrying cannot help."""

    retryable = False


@dataclass
class ExtractionResult:
    invoice: ExtractedInvoice
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    duration_ms: int
    raw: dict
    stop_reason: str | None = None
