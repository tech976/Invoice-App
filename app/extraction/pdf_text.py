"""Read the embedded text layer of a PDF and judge whether it is trustworthy.

Not every PDF that *has* text has *usable* text. Bills exported through some
print drivers embed a font with no ToUnicode map, so the extracted characters
are mojibake even though the page looks perfect on screen. One of the sample
bills (K.R.FOODS, produced by PDFium) does exactly this — pdftotext returns
`!"#$%&'$` where the page reads `K.R.FOODS`.

`score_text` separates the two cases so the pipeline can send junk-text pages
down the OCR / vision route instead of feeding garbage to the model.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Words that appear on essentially every Indian tax invoice. Hitting several
# of these is strong evidence the text layer decoded correctly.
INVOICE_KEYWORDS = (
    "invoice", "total", "gst", "gstin", "tax", "hsn", "amount", "rate",
    "quantity", "qty", "date", "bill", "party", "state", "sac", "cgst",
    "sgst", "igst", "buyer", "seller", "consignee", "address", "pan",
    "eway", "e-way", "supply", "goods", "description", "value", "signatory",
)

_ALPHA_TOKEN = re.compile(r"[A-Za-z]{2,}")
_VOWELS = set("aeiouAEIOU")


@dataclass
class PageText:
    page_no: int
    text: str
    quality: float


@dataclass
class PdfText:
    pages: list[PageText] = field(default_factory=list)
    producer: str | None = None
    page_count: int = 0

    @property
    def full_text(self) -> str:
        return "\n\n".join(
            f"--- page {p.page_no} ---\n{p.text}" for p in self.pages if p.text.strip()
        )

    @property
    def quality(self) -> float:
        """Quality of the document as a whole.

        Uses the best page rather than the mean: an e-way bill annexure that
        is a pure image should not condemn a perfectly readable invoice page.
        """
        if not self.pages:
            return 0.0
        return max(p.quality for p in self.pages)


def score_text(text: str) -> float:
    """Rate how likely `text` is real decoded prose. 0.0 = junk, 1.0 = clean.

    Three independent signals, because any one alone is foolable:

    * keyword hits  - garbled text never spells 'invoice' by accident
    * alpha ratio   - broken encodings skew heavily to punctuation/symbols
    * vowel ratio   - real words have vowels; `!"#$%&` and `9/=*<` do not
    """
    if not text or not text.strip():
        return 0.0

    lowered = text.lower()
    hits = sum(1 for kw in INVOICE_KEYWORDS if kw in lowered)
    keyword_score = min(hits / 8.0, 1.0)

    stripped = re.sub(r"\s", "", text)
    if not stripped:
        return 0.0
    alpha_ratio = sum(c.isalpha() for c in stripped) / len(stripped)
    # Bills are number-dense, so even good text sits near 0.5 alpha. Scale so
    # 0.45+ counts as full marks rather than penalising legitimate invoices.
    alpha_score = min(alpha_ratio / 0.45, 1.0)

    tokens = _ALPHA_TOKEN.findall(text)
    if tokens:
        with_vowel = sum(1 for t in tokens if any(ch in _VOWELS for ch in t))
        vowel_score = with_vowel / len(tokens)
    else:
        vowel_score = 0.0

    return round(0.5 * keyword_score + 0.2 * alpha_score + 0.3 * vowel_score, 4)


def _pdfinfo(path: Path) -> dict[str, str]:
    try:
        out = subprocess.run(
            ["pdfinfo", str(path)], capture_output=True, text=True, timeout=30
        )
        info: dict[str, str] = {}
        for line in out.stdout.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()
        return info
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("pdfinfo failed for %s: %s", path.name, exc)
        return {}


def extract_pdf_text(path: Path) -> PdfText:
    """Pull the text layer out page by page, with a quality score for each."""
    info = _pdfinfo(path)
    try:
        page_count = int(info.get("Pages", "0"))
    except ValueError:
        page_count = 0

    result = PdfText(
        producer=info.get("Producer") or info.get("Creator"),
        page_count=page_count,
    )

    if page_count <= 0:
        page_count = _fallback_page_count(path)
        result.page_count = page_count

    for page_no in range(1, page_count + 1):
        text = _page_text(path, page_no)
        result.pages.append(PageText(page_no=page_no, text=text, quality=score_text(text)))

    return result


def _fallback_page_count(path: Path) -> int:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception as exc:  # noqa: BLE001 - any malformed PDF lands here
        log.warning("could not count pages in %s: %s", path.name, exc)
        return 0


def _page_text(path: Path, page_no: int) -> str:
    """`pdftotext -layout` keeps columns aligned, which matters a lot for the
    line-item table — a scrambled table is far harder for the model to read."""
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", "-f", str(page_no), "-l", str(page_no), str(path), "-"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if out.returncode == 0:
            return out.stdout
        log.warning("pdftotext rc=%s on %s p%s", out.returncode, path.name, page_no)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("pdftotext failed on %s p%s: %s", path.name, page_no, exc)

    # Fall back to pdfplumber, which sometimes recovers text pdftotext drops.
    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            if page_no <= len(pdf.pages):
                return pdf.pages[page_no - 1].extract_text() or ""
    except Exception as exc:  # noqa: BLE001
        log.warning("pdfplumber failed on %s p%s: %s", path.name, page_no, exc)
    return ""
