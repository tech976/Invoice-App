"""The totals strip and the HSN-wise tax table.

Below the goods table every bill states the same handful of numbers, and
states several of them twice — once as digits, once in words, and once again
in the HSN summary. That redundancy is what makes them safe to read without a
model: a figure picked up from the wrong line will not survive the arithmetic.

Values are found by the label printed beside them, so no layout is assumed
beyond 'the number sits to the right of, or beneath, its name'.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.extraction.layout import Line, PageLayout

# Label -> the words bills print for it.
TOTAL_LABELS: dict[str, tuple[str, ...]] = {
    "taxable_value": ("taxable value", "total taxable", "taxable amt",
                      "tot.taxable amt", "taxable"),
    "cgst_amount": ("cgst",),
    "sgst_amount": ("sgst", "utgst", "sgst/utgst"),
    "igst_amount": ("igst",),
    "cess_amount": ("cess",),
    "tcs_amount": ("tcs",),
    "round_off": ("round off", "r/off", "rounded off", "roundoff"),
    "grand_total": ("grand total", "total inv amt", "total amount", "total"),
}

WORDS_LABELS = (
    "amount chargeable (in words)", "amount chargeable", "amount in words",
    "amount (in words)", "amount(in words)", "rupees in words",
)
# Only the *invoice* total is spelled out for our purposes; the tax figure is
# spelled out too and reads almost identically.
TAX_WORDS_LABELS = ("tax amount (in words)", "tax amt in words", "tax amount in words")

_NUM_TOKEN = re.compile(r"\(?-?\)?\s*₹?\s*-?\d[\d,]*(?:\.\d+)?\)?")
_TRAILING_WORDS = re.compile(r"(only|only\.)\s*$", re.I)
_HSN_ROW_RE = re.compile(r"^(\d{4}(?:\d{2}(?:\d{2})?)?)\b")


def parse_amount(text: str) -> Decimal | None:
    """'(-)0.46' -> -0.46, '₹ 37,41,200.00' -> 3741200.00."""
    if not text:
        return None
    raw = text.strip()
    negative = "(-)" in raw or raw.startswith("-") or (
        raw.startswith("(") and raw.endswith(")") and any(c.isdigit() for c in raw)
    )
    cleaned = re.sub(r"[^\d.]", "", raw)
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def numbers_in(line: Line) -> list[Decimal]:
    out: list[Decimal] = []
    for token in _NUM_TOKEN.findall(line.text):
        value = parse_amount(token)
        if value is not None:
            out.append(value)
    return out


@dataclass
class TaxSummaryRow:
    hsn: str | None = None
    taxable_value: Decimal | None = None
    numbers: list[Decimal] = field(default_factory=list)
    cgst_rate: Decimal | None = None
    cgst_amount: Decimal | None = None
    sgst_rate: Decimal | None = None
    sgst_amount: Decimal | None = None
    igst_rate: Decimal | None = None
    igst_amount: Decimal | None = None
    total_tax: Decimal | None = None


def _norm(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9./ ]+", " ", text.lower())
    return " ".join(cleaned.split())


# Every label that can end another label's value span. Anything naming a
# column or a figure counts, not just the totals we want.
_DELIMITERS: tuple[str, ...] = tuple(sorted(
    {a for aliases in TOTAL_LABELS.values() for a in aliases}
    | {"other amt", "total inv amt", "tot.taxable amt", "rate", "amount",
       "tax amount", "tax rate", "value", "qty", "quantity", "igst amt",
       "cgst amt", "sgst amt", "total tax", "e. & o.e"},
    key=len, reverse=True))

MAX_LABEL_WORDS = 3


def _label_spans(line: Line) -> list[tuple[int, int, str]]:
    """(start word, end word, label) for every recognised label on the line."""
    words = sorted(line.words, key=lambda w: w.x0)
    spans: list[tuple[int, int, str]] = []
    i = 0
    while i < len(words):
        matched = None
        for span in range(min(MAX_LABEL_WORDS, len(words) - i), 0, -1):
            phrase = _norm(" ".join(w.text for w in words[i:i + span]))
            if phrase in _DELIMITERS:
                matched = (span, phrase)
                break
        if matched:
            span, phrase = matched
            spans.append((i, i + span, phrase))
            i += span
        else:
            i += 1
    return spans


def _value_for(line: Line, aliases: tuple[str, ...]) -> Decimal | None:
    """The figure belonging to `aliases` on this line.

    Read between the label and whatever label comes next, so that
    'Tot.Taxable Amt : 58,46,893.00  Other Amt : 0.34  Total Inv Amt :
    61,39,238.00' yields the taxable value rather than the invoice total. The
    last number in that span wins, because a rate is printed before its
    amount: 'IGST 5 % 1,78,152.38'.
    """
    words = sorted(line.words, key=lambda w: w.x0)
    spans = _label_spans(line)
    for idx, (start, end, phrase) in enumerate(spans):
        if phrase not in aliases:
            continue
        stop = spans[idx + 1][0] if idx + 1 < len(spans) else len(words)
        values = [v for v in (parse_amount(w.text) for w in words[end:stop])
                  if v is not None]
        if values:
            return values[-1]
    return None


def read_totals(pages: list[PageLayout], start_y: float | None = None,
                start_page: int = 1) -> dict[str, Decimal]:
    """Every labelled total on the bill, keyed by field name.

    Later occurrences win: a bill prints 'Total' once under the goods table
    and again for the invoice, and the second is the one that matters.
    """
    found: dict[str, Decimal] = {}
    for page in pages:
        for line in page.lines:
            if page.page_no == start_page and start_y is not None and line.ymid < start_y:
                continue
            for field_name, aliases in TOTAL_LABELS.items():
                value = _value_for(line, aliases)
                if value is not None:
                    found[field_name] = value
    return found


def read_amount_in_words(pages: list[PageLayout]) -> str | None:
    """The spelled-out invoice total — the bill's own check on its digits."""
    for page in pages:
        lines = page.lines
        for idx, line in enumerate(lines):
            lowered = " ".join(line.text.lower().split())
            if any(w in lowered for w in TAX_WORDS_LABELS):
                continue
            if not any(w in lowered for w in WORDS_LABELS):
                continue
            # The words may follow the label on the same line or beneath it.
            after = re.split(r"amount[^:]*?(?:\(in words\)|in words|chargeable)\s*:?",
                             line.text, flags=re.I)
            tail = after[-1].strip(" :") if len(after) > 1 else ""
            if _looks_spelled(tail):
                return _clean_words(tail)
            parts: list[str] = []
            for follow in lines[idx + 1: idx + 4]:
                run = _leading_run(follow)
                if not run:
                    continue
                parts.append(run)
                joined = " ".join(parts)
                if _looks_spelled(joined) and re.search(r"\bonly\b", joined, re.I):
                    return _clean_words(joined)
            if parts and _looks_spelled(" ".join(parts)):
                return _clean_words(" ".join(parts))
    return None


# Words further apart than this belong to different columns.
COLUMN_GAP = 25.0


def _leading_run(line: Line) -> str:
    """The line's leftmost column only.

    The spelled-out total often shares a line with a running-balance column;
    cutting at the first wide gap keeps 'Previous Balance : 6,33,737.00 Dr'
    out of the sentence.
    """
    words = sorted(line.words, key=lambda w: w.x0)
    kept = []
    for idx, word in enumerate(words):
        if idx and word.x0 - words[idx - 1].x1 > COLUMN_GAP:
            break
        kept.append(word.text)
    return " ".join(kept).strip()


def _looks_spelled(text: str) -> bool:
    lowered = text.lower()
    if not any(k in lowered for k in ("lakh", "thousand", "crore", "hundred", "only")):
        return False
    return len(re.findall(r"[A-Za-z]{3,}", text)) >= 3


def _clean_words(text: str) -> str:
    cleaned = " ".join(text.split()).strip(" :")
    match = _TRAILING_WORDS.search(cleaned)
    if match:
        cleaned = cleaned[: match.end()]
    return cleaned


def _summary_heads(lines: list[Line], before: int) -> list[str]:
    """Which tax heads the summary table charges, in printed order.

    Taken from the table's own heading rather than from the invoice totals —
    the point of the HSN table is to be an independent statement of the tax,
    and matching it against the figure it is meant to check would make the
    comparison circular.
    """
    for line in reversed(lines[max(0, before - 6): before]):
        lowered = line.text.lower()
        if "taxable" not in lowered:
            continue
        heads: list[tuple[float, str]] = []
        for word in line.words:
            token = re.sub(r"[^a-z]", "", word.text.lower())
            for head in ("cgst", "sgst", "utgst", "igst", "cess"):
                if token.startswith(head):
                    heads.append((word.x0, "sgst" if head == "utgst" else head))
                    break
        ordered, seen = [], set()
        for _x, head in sorted(heads):
            if head not in seen:
                seen.add(head)
                ordered.append(head)
        if ordered:
            return ordered
    return []


def read_tax_summary(pages: list[PageLayout]) -> list[TaxSummaryRow]:
    """Rows of the HSN-wise tax table, if the bill prints one.

    Recognised by shape: a line opening with an HSN code and carrying the
    taxable value, a (rate, amount) pair per tax head, and the row total. The
    heads come from the table's heading, so a bill charging CGST+SGST is not
    read as though it charged IGST.
    """
    rows: list[TaxSummaryRow] = []
    for page in pages:
        lines = page.lines
        heads: list[str] | None = None
        for idx, line in enumerate(lines):
            text = line.text.strip()
            match = _HSN_ROW_RE.match(text)
            if not match:
                continue
            values = numbers_in(line)[1:]
            if len(values) < 2:
                continue
            if heads is None:
                heads = _summary_heads(lines, idx)
            if not heads:
                continue

            row = TaxSummaryRow(hsn=match.group(1), taxable_value=values[0],
                                numbers=values)
            # taxable, then a rate and an amount per head, then the row total.
            expected = 1 + 2 * len(heads) + 1
            if len(values) >= expected - 1:
                cursor = 1
                for head in heads:
                    setattr(row, f"{head}_rate", values[cursor])
                    setattr(row, f"{head}_amount", values[cursor + 1])
                    cursor += 2
                if cursor < len(values):
                    row.total_tax = values[cursor]
                rows.append(row)
    return rows
