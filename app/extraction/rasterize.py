"""Render PDF pages to PNGs.

Page images serve three purposes:
  1. they are what the model actually looks at when the text layer is junk;
  2. they feed tesseract for a local OCR cross-check;
  3. the review screen shows them beside the extracted fields.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

# Long edge in pixels. Claude downsamples above ~1568px, so rendering larger
# costs tokens and upload time without adding any detail the model can use.
MAX_EDGE = 1568


def render_pdf_pages(pdf_path: Path, out_dir: Path, dpi: int | None = None) -> list[Path]:
    """Render every page of `pdf_path` into `out_dir` as page-0001.png etc."""
    dpi = dpi or settings.render_dpi
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / "page"

    try:
        proc = subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), "-scale-to", str(MAX_EDGE),
             str(pdf_path), str(prefix)],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            log.error("pdftoppm failed for %s: %s", pdf_path.name, proc.stderr[:400])
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("pdftoppm unavailable for %s: %s", pdf_path.name, exc)
        return _render_with_pdfium(pdf_path, out_dir, dpi)

    pages = sorted(out_dir.glob("page-*.png"))
    if not pages:
        return _render_with_pdfium(pdf_path, out_dir, dpi)
    return pages


def _render_with_pdfium(pdf_path: Path, out_dir: Path, dpi: int) -> list[Path]:
    """Render with pypdfium2, which ships its own PDFium and needs no poppler.

    pdftoppm is faster on long documents, but it is a system package; this
    path keeps rendering working on a machine that only has the wheels.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        log.error("pypdfium2 unavailable for %s: %s", pdf_path.name, exc)
        return _render_with_pdf2image(pdf_path, out_dir, dpi)

    written: list[Path] = []
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            for idx, page in enumerate(doc, start=1):
                img = page.render(scale=dpi / 72).to_pil()
                img.thumbnail((MAX_EDGE, MAX_EDGE))
                target = out_dir / f"page-{idx:04d}.png"
                img.save(target, "PNG")
                written.append(target)
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001
        log.error("pypdfium2 fallback failed for %s: %s", pdf_path.name, exc)
        return written or _render_with_pdf2image(pdf_path, out_dir, dpi)
    return written


def _render_with_pdf2image(pdf_path: Path, out_dir: Path, dpi: int) -> list[Path]:
    try:
        from pdf2image import convert_from_path

        images = convert_from_path(str(pdf_path), dpi=dpi)
    except Exception as exc:  # noqa: BLE001
        log.error("pdf2image fallback failed for %s: %s", pdf_path.name, exc)
        return []

    written: list[Path] = []
    for idx, img in enumerate(images, start=1):
        img.thumbnail((MAX_EDGE, MAX_EDGE))
        target = out_dir / f"page-{idx:04d}.png"
        img.save(target, "PNG")
        written.append(target)
    return written


def normalize_image(src: Path, out_dir: Path) -> Path:
    """Copy a directly-uploaded photo/scan into the page store, downscaled."""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "page-0001.png"
    with Image.open(src) as img:
        img = img.convert("RGB")
        img.thumbnail((MAX_EDGE, MAX_EDGE))
        img.save(target, "PNG")
    return target
