"""The goods table, read by column geometry.

Sixty layouts disagree about where the table sits, how many columns it has and
what they are called — but they agree on the vocabulary. Every one of them
labels its columns some variant of Quantity, Rate, HSN, Amount. So the table
is found by looking for the line carrying the most of those words, and its
column positions become the bands that every row beneath is read against.

No vendor templates. The header row on the page *is* the template.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.extraction.layout import Line, PageLayout

log = logging.getLogger(__name__)

# Canonical column -> the words bills print for it. Matched longest-first, so
# 'bag qty' wins over the bare 'qty'.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "serial": ("sl no", "sr no", "s n", "s no", "sl", "sr", "sn", "no"),
    "description": ("code & description of goods", "name of product/service",
                    "description of goods", "description of good",
                    "name of product", "particulars", "description",
                    "product", "goods", "item"),
    "item_remarks": ("item remarks", "remarks"),
    "hsn": ("hsn/sac code", "hsn/sac", "hsn code", "hsn", "sac"),
    "bags": ("bag qty", "alt quantity", "bags", "packages"),
    "quantity": ("quantity", "qty"),
    "rate": ("rate", "price"),
    "rate_uom": ("per", "unit"),
    "discount": ("disc %", "discount", "disc"),
    "tax_rate": ("tax rate",),
    "tax_amount": ("tax amt", "tax amount"),
    # 'total' is deliberately absent: it opens the totals strip, not a column.
    "amount": ("amount", "value"),
}

# Longest alias first, so 'bag qty' is tried before 'qty'.
_ALIAS_LOOKUP: list[tuple[str, str]] = sorted(
    ((alias, name) for name, aliases in COLUMN_ALIASES.items() for alias in aliases),
    key=lambda pair: -len(pair[0].split()),
)
MAX_LABEL_WORDS = 4

# A header line must carry at least this many recognised columns. Three keeps
# a stray 'Amount' in the totals block from being mistaken for a table.
MIN_HEADER_HITS = 3

# Words separated by more than this belong to different column labels.
LABEL_GAP = 16.0

# Rows stop here: the totals strip under the table.
TERMINATOR_RE = re.compile(
    r"^(total|grand total|sub\s*total|amount chargeable|amount in words|"
    r"amount \(in words\)|tax amt in words|e\. ?& ?o\.e|continued|"
    r"subject to|this is a computer|company'?s? bank|bank details)\b", re.I)

# Labels in the totals strip that restate a figure the invoice already has a
# field for. They are not charges, and adding them to `other_charges` counts
# the same printed rupee twice — enough on its own to fail the grand-total
# check. Not a terminator: a real charge can be printed below these, so the
# row is skipped rather than the scan stopped.
TOTALS_LABEL_RE = re.compile(
    r"\b(taxable\s+(value|amount)|sub\s*total|gross\s+(value|amount|total)|"
    r"total\s+(value|amount|before\s+tax)|net\s+(value|amount)|"
    r"grand\s+total|invoice\s+(value|total))\b", re.I)

# Rows that are not goods.
TAX_ROW_RE = re.compile(r"\b(output\s+)?(c ?gst|s ?gst|i ?gst|ugst|cess|tcs|tds)\b", re.I)
CHARGE_WORDS = {
    "round_off": ("round off", "r/off", "rounded off", "roundoff"),
    "tcs": ("tcs",),
    "tds": ("tds",),
    "packing": ("packing", "packaging"),
    "labour": ("labour", "labor", "hamali"),
    "freight": ("freight", "transport", "carriage", "lorry"),
    "handling": ("handling",),
    "insurance": ("insurance",),
    "discount": ("discount", "rebate", "cash disc"),
}

# Every column a label can land in, left to right. The totals strip is
# right-aligned against the amount, so its label sits under a column that
# carries figures on a goods row — which is why this is the full set and not
# just the description ones.
_LABEL_ORDER: tuple[str, ...] = (
    "serial", "description", "item_remarks", "hsn", "bags", "quantity",
    "rate", "rate_uom", "discount", "tax_rate", "tax_amount",
)

# A cell holding only a figure — an amount, a count, a percentage, a unit
# symbol. Never part of a label.
_FIGURE_ONLY_RE = re.compile(r"^[₹Rs\s.,()%+-]*[\d,.]+\s*[%]?\s*(cr|dr)?$", re.I)


def _figure_only(text: str) -> bool:
    """Is this cell a number rather than a word?

    Kept deliberately narrow: it must contain a digit and nothing that could
    be part of a label. '2.5%' and '(-)0.46' are figures; 'CGST @ 2.5%' and
    'QTL' are not, and both need to survive into the label.
    """
    t = text.strip()
    return bool(t) and any(c.isdigit() for c in t) and bool(_FIGURE_ONLY_RE.match(t))


NUMBER_RE = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")
# An amount as bills print it: '35,59,297.50', '(-)0.46', '₹ 37,41,200.00'.
AMOUNT_RE = re.compile(r"^[₹Rs.\s]*\(?-?\)?\s*-?[\d,]+(?:\.\d+)?\)?\s*(cr|dr)?$", re.I)


def amount_like(text: str) -> bool:
    """Does this cell hold a money figure rather than page furniture?

    Without this, 'continued ...' and 'SUBJECT TO MUMBAI JURISDICTION' land in
    the amount column and open rows of their own.
    """
    t = text.strip()
    return bool(t) and bool(AMOUNT_RE.match(t)) and any(c.isdigit() for c in t)
HSN_RE = re.compile(r"^\d{4}(?:\.?\d{2}){0,2}\.?$")
PERCENT_RE = re.compile(r"^-?[\d.]+\s*%$")


@dataclass
class Column:
    name: str
    x0: float
    x1: float


@dataclass
class TableHeader:
    page_no: int
    line_index: int
    ymid: float
    columns: list[Column]
    end_index: int = 0

    def __post_init__(self) -> None:
        self.end_index = self.end_index or self.line_index

    def band_for(self, x_center: float) -> str | None:
        """Which column a word at `x_center` belongs to."""
        best, best_gap = None, None
        for idx, col in enumerate(self.columns):
            lo = col.x0 if idx == 0 else (self.columns[idx - 1].x1 + col.x0) / 2
            hi = col.x1 if idx == len(self.columns) - 1 else (col.x1 + self.columns[idx + 1].x0) / 2
            if lo <= x_center <= hi:
                return col.name
            gap = min(abs(x_center - lo), abs(x_center - hi))
            if best_gap is None or gap < best_gap:
                best, best_gap = col.name, gap
        return best


@dataclass
class RawRow:
    """One row of the table, before it is interpreted."""

    cells: dict[str, str] = field(default_factory=dict)
    extra_lines: list[str] = field(default_factory=list)
    ymid: float = 0.0

    def get(self, name: str) -> str:
        return self.cells.get(name, "").strip()

    @property
    def text(self) -> str:
        parts = [self.get("description"), self.get("item_remarks"), *self.extra_lines]
        return " ".join(p for p in parts if p).strip()

    @property
    def full_text(self) -> str:
        """Every cell joined — a label can straddle two columns.

        'Less : ROUND OFF' lands with 'ROUND' under the description and 'OFF'
        under the HSN, and neither half names the charge on its own.

        The totals strip is why this cannot stop at the description columns.
        Its labels are right-aligned against the amount, so 'Taxable Value'
        and 'Packing & Forwarding' land under whichever column happens to sit
        there — 'rate' on one layout, 'rate_uom' on the next. Reading only the
        left-hand columns left those rows with no label at all, which made a
        printed charge anonymous and hid the end of the table from
        `_is_terminator`.

        Figures are left out. A label is words, and joining the amount in
        would give every bare number in the totals strip a 'label' made of its
        own digits.
        """
        ordered = " ".join(
            value for key in _LABEL_ORDER
            if (value := self.cells.get(key, "").strip()) and not _figure_only(value)
        )
        return " ".join((ordered + " " + " ".join(self.extra_lines)).split())


def _normalise(text: str) -> str:
    """'HSN/SAC', 'Alt. Quantity' and 'Disc. %' reduce to a comparable form.

    Whitespace is collapsed, because stripping the dot out of 'Alt. Quantity'
    otherwise leaves two spaces and the phrase matches nothing.
    """
    cleaned = re.sub(r"[^a-z0-9/&% ]+", " ", text.lower()).replace("%", " ")
    return " ".join(cleaned.split())


def _score_line(line: Line) -> tuple[int, list[Column]]:
    """Read a line as column headings, longest label first.

    Gap-based grouping fails here: on a Tally bill 'HSN/SAC' and 'Quantity'
    sit eleven points apart, closer than the words inside 'Description of
    Goods'. So labels are matched greedily left to right against the known
    vocabulary instead of guessed from spacing.
    """
    words = sorted(line.words, key=lambda w: w.x0)
    columns: list[Column] = []
    seen: set[str] = set()
    i = 0
    while i < len(words):
        matched = None
        for span in range(min(MAX_LABEL_WORDS, len(words) - i), 0, -1):
            phrase = _normalise(" ".join(w.text for w in words[i:i + span]))
            if not phrase:
                continue
            for alias, name in _ALIAS_LOOKUP:
                if phrase == alias:
                    matched = (name, span)
                    break
            if matched:
                break
        if matched:
            name, span = matched
            if name not in seen:
                seen.add(name)
                columns.append(Column(name, words[i].x0, words[i + span - 1].x1))
            i += span
        else:
            i += 1
    return len(columns), columns


EWAY_PAGE_RE = re.compile(r"^e-?way\s*bill\b", re.I)


def is_annexure(page: PageLayout, scan_lines: int = 4) -> bool:
    """Is this the e-way bill page rather than the invoice?

    The annexure repeats the goods in its own table with different columns and
    its own totals. Reading it would double every quantity on the bill.
    """
    for line in page.lines[:scan_lines]:
        if EWAY_PAGE_RE.match(line.text.strip()):
            return True
    return False


def find_header(page: PageLayout) -> TableHeader | None:
    """The line that looks most like the goods-table heading.

    Some bills split the heading over two lines ('Sl' above 'No.'); the second
    line is folded in when it adds columns without looking like data.
    """
    lines = page.lines
    best: TableHeader | None = None
    best_hits = MIN_HEADER_HITS - 1

    for idx, line in enumerate(lines):
        hits, columns = _score_line(line)
        if hits <= best_hits:
            continue
        end = idx
        if idx + 1 < len(lines):
            extra_hits, extra_columns = _score_line(lines[idx + 1])
            if 0 < extra_hits <= hits and abs(lines[idx + 1].ymid - line.ymid) < 20:
                known = {c.name for c in columns}
                columns = columns + [c for c in extra_columns if c.name not in known]
                # Folded whether or not it contributed: 'No.' sitting under
                # 'Sl' is part of the heading, not the first row.
                end = idx + 1
        best_hits = hits
        best = TableHeader(page.page_no, idx, line.ymid,
                           sorted(columns, key=lambda c: c.x0), end_index=end)
    if best:
        log.debug("table header on p%s: %s", page.page_no,
                  [c.name for c in best.columns])
    return best


def _is_terminator(line: Line, header: TableHeader) -> bool:
    """Has the table ended?

    Tested against the line's own leading words rather than a column band:
    the totals strip spans the table, so 'Total' can land under any column
    depending on the layout.
    """
    words = sorted(line.words, key=lambda w: w.x0)
    for span in (1, 2):
        lead = " ".join(w.text for w in words[:span]).strip()
        if TERMINATOR_RE.match(lead):
            return True
    return False


def read_rows(page: PageLayout, header: TableHeader,
              max_rows: int = 200) -> list[RawRow]:
    """Every row under the header, continuation lines folded into their row.

    A row opens where the serial column has a value, or — on layouts with no
    serial column — where the amount column does. Anything else is a
    continuation: the bags count, the brand, the grade printed beneath the
    item name.
    """
    lines = page.lines
    has_serial = any(c.name == "serial" for c in header.columns)
    rows: list[RawRow] = []

    for line in lines[header.end_index + 1:]:
        if _is_terminator(line, header):
            break
        if len(rows) >= max_rows:
            break

        cells: dict[str, list[str]] = {}
        for word in sorted(line.words, key=lambda w: w.x0):
            band = header.band_for((word.x0 + word.x1) / 2)
            if band:
                cells.setdefault(band, []).append(word.text)
        if not cells:
            continue
        flat = {k: " ".join(v) for k, v in cells.items()}
        _unglue_serial(flat)
        _pull_serial_from_description(flat)

        has_amount = amount_like(flat.get("amount", ""))
        opens = has_amount or (
            has_serial and bool(NUMBER_RE.match(flat.get("serial", "").strip()))
        )
        if opens:
            rows.append(RawRow(cells=flat, ymid=line.ymid))
        elif not rows:
            continue
        else:
            merged = " ".join(
                flat[k] for k in ("description", "item_remarks") if flat.get(k)
            ).strip()
            if merged:
                rows[-1].extra_lines.append(merged)
            for key, value in flat.items():
                if key not in ("description", "item_remarks") and not rows[-1].cells.get(key):
                    rows[-1].cells[key] = value
    return rows


_BAG_UNIT_RE = re.compile(r"\bbags?\b|\bpkts?\b|\bcartons?\b|\bboxes\b", re.I)
_WEIGHT_UNIT_RE = re.compile(r"\b(kgs?|mt|qtl|ltr?|gms?|pcs|nos)\b", re.I)


def repair_quantity_columns(row: RawRow) -> None:
    """Swap quantity and bags when the printed units say they are the wrong way round.

    Tally prints 'Alt. Quantity' beside 'Quantity' and right-aligns both
    headings, so a purely geometric read can land '1,020.0 KGS' under the bag
    count and '34 bags' under the quantity. The units settle it: the cell
    naming bags is the bag count, whatever column it fell into.
    """
    qty, bags = row.cells.get("quantity", ""), row.cells.get("bags", "")
    if not qty or not bags:
        return
    qty_is_bags = bool(_BAG_UNIT_RE.search(qty)) and not _WEIGHT_UNIT_RE.search(qty)
    bags_is_qty = bool(_WEIGHT_UNIT_RE.search(bags))
    if qty_is_bags and bags_is_qty:
        row.cells["quantity"], row.cells["bags"] = bags, qty


_GLUED_SERIAL_RE = re.compile(r"^(\d{1,3})([A-Za-z].*)$", re.S)


def _unglue_serial(cells: dict[str, str]) -> None:
    """Separate a serial number printed hard against the description.

    Tally emits '1ALMONDS KERNEL' as a single word, and a continuation line's
    text can drift left into the serial column. Either way, letters in the
    serial cell belong to the description.
    """
    serial = cells.get("serial", "").strip()
    if not serial:
        return
    match = _GLUED_SERIAL_RE.match(serial)
    if match:
        cells["serial"] = match.group(1)
        spill = match.group(2).strip()
    elif NUMBER_RE.match(serial):
        return
    else:
        cells["serial"], spill = "", serial
    if spill:
        cells["description"] = (spill + " " + cells.get("description", "")).strip()


_LEADING_SERIAL_RE = re.compile(r"^(\d{1,3})\s+(?=[A-Za-z])")


def _pull_serial_from_description(cells: dict[str, str]) -> None:
    """Recover a serial number that landed in the description column.

    Narrow serial columns let the number drift a point or two into its
    neighbour, and 'Almond Kernels' should not be filed as '1 Almond Kernels'.

    Only rows carrying an amount qualify. A continuation line such as
    '200 Bags' opens with a number too, and promoting that to a serial would
    split one line item into several.
    """
    if cells.get("serial", "").strip() or not amount_like(cells.get("amount", "")):
        return
    description = cells.get("description", "").strip()
    match = _LEADING_SERIAL_RE.match(description)
    if match:
        cells["serial"] = match.group(1)
        cells["description"] = description[match.end():].strip()


_BAG_LINE_RE = re.compile(
    r"^([\d,]+)\s*(bags?|pkts?|packets?|cartons?|boxes|tins?|drums?)$", re.I)


def split_continuations(row: RawRow) -> tuple[str | None, float | None, str | None]:
    """Separate the lines printed beneath an item into their own fields.

    Bills stack the detail under the product name rather than beside it — the
    bag count on one line, the brand or origin mark on the next. Folded into
    the description they make 'Walnuts Inshell 200 Bags Andesfood', which is
    neither a product name nor searchable.
    """
    description = row.get("description")
    bags: float | None = None
    brand: str | None = None
    leftover: list[str] = []

    for line in row.extra_lines:
        text = " ".join(line.split())
        if not text or text == row.get("item_remarks"):
            continue
        match = _BAG_LINE_RE.match(text)
        if match and bags is None:
            bags = float(match.group(1).replace(",", ""))
            continue
        # A short line of plain words under an item is its brand or origin.
        if brand is None and len(text) <= 40 and not any(c.isdigit() for c in text):
            brand = text
            continue
        leftover.append(text)

    if leftover:
        description = " ".join([description, *leftover]).strip()
    return (" ".join(description.split())[:200] or None) if description else None, bags, brand


def classify(row: RawRow) -> tuple[str, str | None]:
    """('goods' | 'charge' | 'tax' | 'total', charge kind).

    A charge row carries an amount but no quantity and no rate — packing,
    handling, freight, round-off. A tax row names a GST head. A total row
    restates a figure the invoice already holds elsewhere. Everything else
    that has an amount is goods.
    """
    text = row.full_text.lower()
    has_qty = bool(row.get("quantity").strip())
    if TAX_ROW_RE.search(text) and not has_qty:
        return "tax", None
    # A charge never carries a quantity — that is what separates 'PACKING &
    # LABOUR 5%' priced at 825.00 from a goods row priced per kilo.
    if not has_qty:
        if TOTALS_LABEL_RE.search(text):
            return "total", None
        for kind, words in CHARGE_WORDS.items():
            if any(w in text for w in words):
                return "charge", kind
        if row.get("amount"):
            # Every real charge names itself on the bill — that is how the
            # buyer knows what is being billed — so a nameless amount under
            # the table is the reader having found a figure it cannot account
            # for, which in practice means the totals strip.
            #
            # Booking it as 'other' put the taxable value and the tax into
            # `other_charges` beside their own fields, and the doubled rupees
            # then broke the grand-total check on a bill whose arithmetic was
            # sound. Letting it fall through to goods is no better: a line
            # with no description and no quantity still adds its amount to the
            # subtotal. So it is booked nowhere, and if that leaves a real gap
            # the grand-total check is what says so.
            return ("charge", "other") if text.strip() else ("total", None)
    return "goods", None
