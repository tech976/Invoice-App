"""Positioned words from a PDF page, and the anchors that locate a value.

This is the machinery behind template extraction. A bill's *values* change
every time; its *labels* do not. So a field is recorded not by where it sat on
the page, but by which printed label it sat next to — 'the thing under
"Invoice No."', 'the thing right of "Invoice Dt :"'. That survives a bill
growing from three line items to nine, which fixed coordinates would not.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

log = logging.getLogger(__name__)

# Words on the same line rarely differ in vertical centre by more than this.
LINE_TOL = 3.5
# How far left/above we will look for a label. The left reach is generous
# because a totals row spans the whole table: on these bills 'Total' sits some
# 285pt from the figure it labels, with other cells in between.
MAX_LEFT_GAP = 340.0
MAX_ABOVE_GAP = 22.0

# Tokens that are data rather than a label, skipped when looking leftward for
# the printed word that names a value.
_NUMERIC = re.compile(r"^[\(\)\-+]?[\d,.\s]+%?$")
_UNITS = {
    "kgs", "kg", "qtl", "mts", "gms", "pcs", "nos", "no", "box", "ctn",
    "bag", "bags", "pkt", "tin", "ltr", "mtr", "%", ":", "inr", "rs",
}


def _is_valueish(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return True
    return bool(_NUMERIC.match(t)) or t in _UNITS


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def ymid(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class Line:
    """Words sharing a horizontal band, left to right."""

    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def ymid(self) -> float:
        return sum(w.ymid for w in self.words) / len(self.words)


@dataclass
class PageLayout:
    page_no: int
    width: float
    height: float
    words: list[Word] = field(default_factory=list)

    @property
    def lines(self) -> list[Line]:
        out: list[Line] = []
        for w in sorted(self.words, key=lambda w: (w.ymid, w.x0)):
            if out and abs(out[-1].ymid - w.ymid) <= LINE_TOL:
                out[-1].words.append(w)
            else:
                out.append(Line([w]))
        for line in out:
            line.words.sort(key=lambda w: w.x0)
        return out


def read_layout(path: Path, max_pages: int = 6) -> list[PageLayout]:
    """Positioned words for each page. Empty list if the PDF has no usable text."""
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover
        return []

    pages: list[PageLayout] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for idx, page in enumerate(pdf.pages[:max_pages], start=1):
                words = [
                    Word(w["text"], w["x0"], w["x1"], w["top"], w["bottom"])
                    for w in page.extract_words(keep_blank_chars=False)
                ]
                pages.append(PageLayout(idx, page.width, page.height, words))
    except Exception as exc:  # noqa: BLE001 - a malformed PDF is not fatal
        log.warning("could not read layout of %s: %s", path.name, exc)
        return []
    return pages


# --------------------------------------------------------------------------
# Rendering a stored value back to the forms a bill might print
# --------------------------------------------------------------------------


def indian_group(number: Decimal) -> str:
    """5846893.00 -> '58,46,893.00' — the 2-2-3 grouping used on these bills."""
    neg = number < 0
    whole, _, frac = f"{abs(number):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if neg else "") + f"{whole}.{frac}"


def value_candidates(value) -> list[str]:
    """Every plausible printed form of a stored value."""
    if value is None:
        return []

    if isinstance(value, date):
        return [
            value.strftime("%d-%b-%y"), value.strftime("%d-%b-%Y"),
            value.strftime("%d/%m/%Y"), value.strftime("%d/%m/%y"),
            value.strftime("%d-%m-%Y"), value.strftime("%d.%m.%Y"),
            value.strftime("%d %b %Y"), value.isoformat(),
        ]

    if isinstance(value, (int, float, Decimal)):
        d = Decimal(str(value))
        out = [indian_group(d), f"{d:,.2f}", f"{d:.2f}", f"{d:.3f}"]
        if d == d.to_integral_value():
            whole = d.to_integral_value()
            out += [indian_group(whole).removesuffix(".00"), f"{whole:,}", f"{whole}"]
        return [o for o in dict.fromkeys(out) if o]

    text = str(value).strip()
    return [text] if text else []


# --------------------------------------------------------------------------
# Locating a value, and describing where it was
# --------------------------------------------------------------------------


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


@dataclass
class Located:
    page_no: int
    words: list[Word]

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.bottom for w in self.words)

    @property
    def ymid(self) -> float:
        return (self.top + self.bottom) / 2


def locate_value(pages: list[PageLayout], printed: str) -> Located | None:
    """Find the run of words that spells `printed`, ignoring spacing."""
    needle = _norm(printed)
    if not needle:
        return None

    for page in pages:
        for line in page.lines:
            for start in range(len(line.words)):
                acc = ""
                for end in range(start, min(start + 8, len(line.words))):
                    acc += _norm(line.words[end].text)
                    if acc == needle:
                        return Located(page.page_no, line.words[start : end + 1])
                    if len(acc) > len(needle):
                        break
    return None


# Words further apart than this horizontally belong to different table cells.
CELL_GAP = 18.0


def _cell_run(words: list[Word], start: Word) -> str:
    """Words from `start` rightwards, stopping at the next cell boundary.

    'Invoice No : 14593 / 2026-27   Invoice Dt : 24/07/2026' is one printed
    line but four cells; a run that ignored the gaps would return all of it.
    """
    ordered = sorted([w for w in words if w.x0 >= start.x0 - 0.5], key=lambda w: w.x0)
    run = [ordered[0]] if ordered else []
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt.x0 - prev.x1 > CELL_GAP:
            break
        run.append(nxt)
    return " ".join(w.text for w in run).strip()


@dataclass
class Anchor:
    """Where a value sits relative to a printed label."""

    label: str
    direction: str          # "left" | "above"
    page_no: int
    gap: float              # points between label and value
    x0: float               # value's left edge, for column disambiguation
    x1: float
    # Which occurrence of this label was the right one. A bill often prints
    # 'Total' twice — once under the line items and once for the payable
    # amount — and the two mean different things.
    occurrence: int = 0

    def to_dict(self) -> dict:
        return {
            "label": self.label, "direction": self.direction,
            "page_no": self.page_no, "gap": self.gap,
            "x0": self.x0, "x1": self.x1, "occurrence": self.occurrence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Anchor":
        return cls(d["label"], d["direction"], d["page_no"], d["gap"],
                   d["x0"], d["x1"], d.get("occurrence", 0))


def find_anchor(page: PageLayout, hit: Located) -> Anchor | None:
    """The nearest printed label that explains this value's position."""
    candidates: list[tuple[str, str, float]] = []

    left = [
        w for w in page.words
        if abs(w.ymid - hit.ymid) <= LINE_TOL
        and w.x1 <= hit.x0 + 1
        and hit.x0 - w.x1 < MAX_LEFT_GAP
    ]
    if left:
        left.sort(key=lambda w: -w.x1)
        # Step over neighbouring figures and units — 'Total  8,750.00  KGS
        # 58,46,893.00' should anchor on 'Total', not on 'KGS'.
        anchor_word = next((w for w in left if not _is_valueish(w.text)), None)
        if anchor_word is not None:
            group = [w for w in left
                     if not _is_valueish(w.text) and anchor_word.x1 - w.x1 < 62]
            group.sort(key=lambda w: w.x0)
            candidates.append(
                ("left", " ".join(w.text for w in group), hit.x0 - anchor_word.x1)
            )

    above = [
        w for w in page.words
        if w.bottom <= hit.top + 1
        and hit.top - w.bottom < MAX_ABOVE_GAP
        and not (w.x1 < hit.x0 - 6 or w.x0 > hit.x1 + 6)
    ]
    if above:
        above.sort(key=lambda w: -w.bottom)
        band = [w for w in above if abs(w.bottom - above[0].bottom) < LINE_TOL]
        # In a totals block the row above is another figure. Anchoring to it
        # would bind the template to a number that changes on every bill, so
        # an all-numeric band is not a label and the left anchor wins instead.
        if any(not _is_valueish(w.text) for w in band):
            band = [w for w in band if not _is_valueish(w.text)]
            band.sort(key=lambda w: w.x0)
            candidates.append(
                ("above", " ".join(w.text for w in band), hit.top - above[0].bottom)
            )

    candidates = [c for c in candidates if c[1].strip()]
    if not candidates:
        return None

    direction, label, gap = min(candidates, key=lambda c: c[2])
    anchor = Anchor(label.strip(), direction, page.page_no, round(gap, 1),
                    round(hit.x0, 1), round(hit.x1, 1))

    # Record which occurrence of this label produced our value, so a bill that
    # prints the same word twice still resolves to the right one.
    matches = _matches_on_page(page, anchor)
    aligned = [m for m in matches if abs(m[1] - anchor.x0) <= 40] or matches
    printed = " ".join(w.text for w in hit.words)
    for idx, (text, _x) in enumerate(aligned):
        if _norm(printed) in _norm(text):
            anchor.occurrence = idx
            break
    return anchor


def _matches_on_page(page: PageLayout, anchor: Anchor) -> list[tuple[str, float]]:
    """Every (value text, value x0) this anchor's label resolves to on a page."""
    needle = _norm(anchor.label)
    found: list[tuple[str, float]] = []

    for line in page.lines:
        for start in range(len(line.words)):
            acc = ""
            for end in range(start, min(start + 10, len(line.words))):
                acc += _norm(line.words[end].text)
                if acc != needle:
                    if len(acc) > len(needle):
                        break
                    continue

                label_words = line.words[start : end + 1]
                lx0 = min(w.x0 for w in label_words)
                lx1 = max(w.x1 for w in label_words)
                lbot = max(w.bottom for w in label_words)

                if anchor.direction == "left":
                    after = [w for w in line.words
                             if w.x0 >= lx1 - 1 and w.x0 - lx1 < MAX_LEFT_GAP]
                    if after:
                        after.sort(key=lambda w: w.x0)
                        start_word = min(after, key=lambda w: abs(w.x0 - anchor.x0))
                        if abs(start_word.x0 - anchor.x0) > 90:
                            start_word = after[0]
                        text = _cell_run(after, start_word)
                        if text:
                            found.append((text, start_word.x0))
                else:
                    below = [w for w in page.words
                             if w.top >= lbot - 1 and w.top - lbot < MAX_ABOVE_GAP
                             and not (w.x1 < lx0 - 8 or w.x0 > lx1 + 8)]
                    if below:
                        below.sort(key=lambda w: (w.top, w.x0))
                        band = [w for w in below if abs(w.top - below[0].top) < LINE_TOL]
                        band.sort(key=lambda w: w.x0)
                        text = _cell_run(band, band[0])
                        if text:
                            found.append((text, band[0].x0))
                break
    return found


def read_at_anchor(pages: list[PageLayout], anchor: Anchor) -> str | None:
    """Read the value an anchor points at, in a document we have not seen.

    Disambiguation runs x-position first (table columns keep their alignment
    even as a bill grows), then falls back to the recorded occurrence index.
    """
    page = next((p for p in pages if p.page_no == anchor.page_no), None)
    if page is None:
        return None

    found = _matches_on_page(page, anchor)
    if not found:
        return None
    if len(found) == 1:
        return found[0][0]

    aligned = [f for f in found if abs(f[1] - anchor.x0) <= 40]
    pool = aligned or found
    if len(pool) == 1:
        return pool[0][0]

    idx = min(anchor.occurrence, len(pool) - 1)
    return pool[idx][0]
