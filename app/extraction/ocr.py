"""Local OCR via tesseract.

The model reading the page image is the primary path for scanned bills; OCR
text is supplied alongside as a hint. Two independent readings of the same
pixels disagree in different places, and the disagreements are exactly what
the review queue should surface.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# psm 6 = "assume a uniform block of text", which handles invoice tables far
# better than the default page-segmentation mode.
_CONFIG = "--oem 3 --psm 6"


def ocr_image(path: Path, lang: str = "eng") -> str:
    try:
        import pytesseract
        from PIL import Image

        with Image.open(path) as img:
            return pytesseract.image_to_string(img, lang=lang, config=_CONFIG)
    except Exception as exc:  # noqa: BLE001 - tesseract may be missing entirely
        log.warning("OCR failed on %s: %s", path.name, exc)
        return ""


def ocr_pages(paths: list[Path], lang: str = "eng") -> dict[int, str]:
    """OCR a list of page images, keyed by 1-based page number."""
    return {idx: ocr_image(p, lang) for idx, p in enumerate(paths, start=1)}
