"""Header fields, read off the text layer by pattern and by label.

Two kinds of field live in a bill's header. Some carry their own proof —
a GSTIN has a check digit, an IRN is 64 hex characters, a PAN has a fixed
shape — and those can be found anywhere on the page by pattern alone, with no
idea of the layout. The rest have to be located by the label printed beside
them, which is what `layout.py` provides.

Nothing here is vendor-specific. A GSTIN is a GSTIN on all sixty layouts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from app.extraction.layout import Line, PageLayout
from app.extraction.local.qr import EInvoiceQR
from app.extraction.normalize import gstin_checksum_ok

# --------------------------------------------------------------------------
# Self-proving patterns
# --------------------------------------------------------------------------

GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d][Z][A-Z\d])\b")
PAN_RE = re.compile(r"\b([A-Z]{5}\d{4}[A-Z])\b")
IRN_RE = re.compile(r"\b([0-9a-f]{64})\b")
FSSAI_RE = re.compile(r"\b(\d{14})\b")
EWAY_RE = re.compile(r"\b(\d{12})\b")
ACK_RE = re.compile(r"\b(\d{15})\b")
IFSC_RE = re.compile(r"\b([A-Z]{4}0[A-Z\d]{6})\b")
EMAIL_RE = re.compile(r"\b([\w.+-]+@[\w-]+\.[\w.-]+)\b")
PINCODE_RE = re.compile(r"\b([1-9]\d{5})\b")
STATE_CODE_RE = re.compile(r"Code\s*:?\s*(\d{1,2})\b", re.I)
STATE_NAME_RE = re.compile(r"State\s*Name\s*:?\s*([A-Za-z ]+?)(?:,|\s*Code|$)", re.I)

# An IRN is often printed across two lines; the halves are hex on their own.
HEX_RUN_RE = re.compile(r"\b([0-9a-f]{16,64})\b")


@dataclass
class Found:
    """A value and where on the document it was printed."""

    value: str
    page_no: int
    top: float
    x0: float


def _page_text(page: PageLayout) -> str:
    return "\n".join(line.text for line in page.lines)


def document_text(pages: list[PageLayout]) -> str:
    return "\n".join(_page_text(p) for p in pages)


def find_all(pages: list[PageLayout], pattern: re.Pattern) -> list[Found]:
    """Every match of `pattern`, located to the word that carried it.

    Matching runs per line rather than per word, because a bill may print
    `GSTIN/UIN: 27NGACL2841M1ZO` as two words or as one.
    """
    out: list[Found] = []
    for page in pages:
        for line in page.lines:
            for match in pattern.finditer(line.text):
                word = _word_for(line, match.group(1))
                out.append(Found(
                    match.group(1),
                    page.page_no,
                    word.top if word else line.ymid,
                    word.x0 if word else 0.0,
                ))
    return out


def _word_for(line: Line, value: str):
    for word in line.words:
        if value in word.text:
            return word
    return line.words[0] if line.words else None


def find_gstins(pages: list[PageLayout]) -> list[Found]:
    """GSTINs that pass their own check digit, in printed order."""
    return [f for f in find_all(pages, GSTIN_RE) if gstin_checksum_ok(f.value)]


def find_irn(pages: list[PageLayout]) -> str | None:
    """The 64-character IRN, joining the halves when it is printed on two lines.

    Tally wraps it mid-string with a hyphen; Crystal Reports prints two
    32-character rows with the address interleaved between them.
    """
    direct = find_all(pages, IRN_RE)
    if direct:
        return direct[0].value

    runs = [f.value for f in find_all(pages, HEX_RUN_RE)]
    for i in range(len(runs) - 1):
        joined = runs[i] + runs[i + 1]
        if len(joined) == 64:
            return joined
    return None


# --------------------------------------------------------------------------
# Party blocks
# --------------------------------------------------------------------------

# Labels that open a block describing somebody. `\b(?!'s)` keeps "Buyer's PAN
# NO" and "Buyer's Order No." from opening a buyer block — they are fields in
# the header table, not the party.
BUYER_RE = re.compile(
    r"^(details of receiver|billed to|buyer\s*\(bill to\)|bill to|buyer\b(?!'s|\u2019s))", re.I)
CONSIGNEE_RE = re.compile(
    r"^(details of consignee|shipped to|consignee\s*\(ship to\)|ship to|consignee)\b", re.I)
SELLER_RE = re.compile(r"^(bill from|sold by|supplier|seller)\b", re.I)

# Labels belonging to the header table rather than to a party. They matter
# because they mark where a neighbouring column starts: bills print the buyer
# and the consignee side by side, and a block that ran the full page width
# would splice the two together.
COLUMN_LABEL_RE = re.compile(
    r"^(invoice\s*(no|dt|date)|dated|delivery note|dispatch|dispatched|"
    r"mode/terms|terms of delivery|reference no|other references|"
    r"buyer'?s order|e-?way bill|destination|ack\s*(no|date)|irn|"
    r"details of consignee|details of receiver|consignee|buyer|billed to|"
    r"shipped to|place of supply)\b", re.I)

# Lines that are identifiers rather than anybody's name.
_NAME_NOISE = re.compile(
    r"^(irn|ack\s*no|ack\s*date|gstin|state\s*name|state\s*code|pan|fssai|"
    r"e-?mail|tel|phone|invoice|dated|place of supply|comp\.|buyer'?s)", re.I)

_LABEL_NOISE = re.compile(
    r"^(gstin|gstin/uin|state\s*name|pan|fssai|e-?mail|tel|phone|place of supply|"
    r"state code|comp\.|buyer'?s pan|invoice|dated|delivery|dispatch|terms|"
    r"reference|mode/terms|other references|buyer'?s order)", re.I)

_TITLE = re.compile(
    r"^(tax\s*invoice|e-?invoice|invoice|proforma|bill of supply|credit note|"
    r"debit note|delivery challan|original|duplicate|triplicate|"
    r"\(?e-?invoice qr code\)?)\b", re.I)


@dataclass
class PartyBlock:
    role: str
    name: str | None = None
    lines: list[str] = field(default_factory=list)
    gstin: str | None = None
    page_no: int = 1

    @property
    def text(self) -> str:
        return ", ".join(self.lines)


def _is_title(text: str) -> bool:
    return bool(_TITLE.match(text.strip()))


def _looks_like_name(text: str) -> bool:
    t = text.strip()
    if len(t) < 3 or _LABEL_NOISE.match(t) or _NAME_NOISE.match(t):
        return False
    if GSTIN_RE.search(t) or HEX_RUN_RE.search(t):
        return False
    return bool(re.search(r"[A-Za-z]{3,}", t))


def _label_role(text: str) -> str | None:
    t = text.strip()
    if CONSIGNEE_RE.match(t):
        return "consignee"
    if BUYER_RE.match(t):
        return "buyer"
    if SELLER_RE.match(t):
        return "seller"
    return None


def _column_starts(page: PageLayout) -> list[tuple[float, float, str | None]]:
    """(ymid, x0, role) for every header-table label, used as column boundaries.

    The role matters when the label is itself a party label. 'Details of
    Receiver / Billed To' contains two buyer phrases, and the second must not
    be mistaken for the start of a neighbouring column — otherwise the buyer
    block is clipped a few millimetres after its own heading.
    """
    starts: list[tuple[float, float, str | None]] = []
    for line in page.lines:
        for word in line.words:
            tail = " ".join(w.text for w in line.words if w.x0 >= word.x0)
            if COLUMN_LABEL_RE.match(tail):
                starts.append((line.ymid, word.x0, _label_role(tail)))
    return starts


def _role_labels(page: PageLayout) -> list[tuple[int, float, float, str]]:
    """(line index, ymid, x0, role) for every party label on the page.

    Scanned per word rather than per line, because bills routinely print two
    party labels side by side — 'Details of Receiver / Billed To' and 'Details
    of Consignee / Shipped To' share one line — and a line-level test would
    only ever see the left one.
    """
    out: list[tuple[int, float, float, str]] = []
    for idx, line in enumerate(page.lines):
        seen: set[str] = set()
        for word in line.words:
            tail = " ".join(w.text for w in line.words if w.x0 >= word.x0)
            role = _label_role(tail)
            if role and role not in seen:
                seen.add(role)
                out.append((idx, line.ymid, word.x0, role))
    return out


def _right_bound(page: PageLayout, left: float, y_lo: float, y_hi: float,
                 columns: list[tuple[float, float, str | None]],
                 role: str | None = None) -> float:
    """Where the block's column ends: the next column starting to its right.

    Columns describing the same role are skipped — they are the tail of this
    block's own heading, not the start of the next one.
    """
    return min(
        (x for y, x, r in columns
         if x > left + 30 and y_lo - 4 <= y <= y_hi and (role is None or r != role)),
        default=page.width,
    )


def party_blocks(page: PageLayout, max_lines: int = 9) -> list[PartyBlock]:
    """Blocks opened by a 'Buyer (Bill to)'-style label.

    A block runs from its label down to the next label or `max_lines`, and is
    clipped horizontally at the next column that starts to its right. Without
    that clip a bill printing receiver and consignee side by side yields one
    block containing both parties' names.
    """
    lines = page.lines
    columns = _column_starts(page)
    labels = _role_labels(page)
    blocks: list[PartyBlock] = []

    for idx, _y, left, role in labels:
        window = lines[idx + 1: idx + 1 + max_lines]
        if not window:
            continue
        y_lo, y_hi = lines[idx].ymid, window[-1].ymid
        right = _right_bound(page, left, y_lo, y_hi, columns, role)

        block = PartyBlock(role=role, page_no=page.page_no)
        for follow in window:
            words = [w for w in follow.words if left - 4 <= w.x0 < right - 2]
            if not words:
                continue
            text = " ".join(w.text for w in words).strip()
            if not text:
                continue
            if any(r for _i, _yy, xx, r in labels
                   if abs(xx - left) < 30 and _label_role(text)):
                break
            hit = GSTIN_RE.search(text)
            if hit and gstin_checksum_ok(hit.group(1)) and block.gstin is None:
                block.gstin = hit.group(1)
            if block.name is None and _looks_like_name(text):
                block.name = text
            else:
                block.lines.append(text)
        if block.name or block.gstin:
            blocks.append(block)
    return blocks


def letterhead_block(page: PageLayout, max_lines: int = 14) -> PartyBlock:
    """The seller, read off the top of page 1.

    Most layouts give the supplier no label at all — it is simply the name at
    the top of the page. So the letterhead is whatever sits above the first
    labelled block, clipped to its own column so the invoice-number table
    printed alongside does not run into the company name, and minus the
    document title and e-invoice identifiers Tally prints in the same area.
    """
    lines = page.lines
    columns = _column_starts(page)
    labels = _role_labels(page)
    stop = min((idx for idx, *_ in labels), default=len(lines))
    window = lines[:min(stop, max_lines)]

    block = PartyBlock(role="seller", page_no=page.page_no)
    if not window:
        return block

    left = min((w.x0 for line in window for w in line.words), default=0.0)
    right = _right_bound(page, left, window[0].ymid, window[-1].ymid, columns)

    for line in window:
        words = [w for w in line.words if w.x0 < right - 2]
        text = " ".join(w.text for w in words).strip()
        if not text:
            continue
        hit = GSTIN_RE.search(line.text)
        if hit and gstin_checksum_ok(hit.group(1)) and block.gstin is None:
            block.gstin = hit.group(1)
        if block.name is None and _looks_like_name(text) and not _is_title(text):
            block.name = text
        else:
            block.lines.append(text)
    return block


def seller_gstin(pages: list[PageLayout], blocks: list[PartyBlock],
                 letterhead: PartyBlock) -> str | None:
    """The supplier's GSTIN.

    Usually it is on the letterhead. Crystal Reports puts it at the foot of
    the page instead, labelled 'Comp. GSTTIN NO', far from the company name —
    so the fallback is elimination: the one checksummed GSTIN on the bill that
    belongs to neither the buyer nor the consignee.
    """
    if letterhead.gstin:
        return letterhead.gstin

    others = {b.gstin for b in blocks if b.gstin}
    for found in find_gstins(pages):
        if found.value not in others:
            return found.value
    return None


def qr_is_consistent(qr: EInvoiceQR | None, text: str) -> bool:
    """Does the QR describe the document it is printed on?

    A vendor template can carry a QR image left over from an older invoice —
    one sample bill does exactly that, showing a 2021 document worth a
    thousandth of the printed total. The cheap test is whether what the QR
    claims actually appears on the page. An unreadable text layer cannot
    contradict anything, so that case is decided by the caller, not here.
    """
    if qr is None:
        return False
    haystack = text.upper().replace(" ", "")
    checks = [qr.doc_no, qr.seller_gstin, qr.buyer_gstin]
    present = [c for c in checks if c and c.upper().replace(" ", "") in haystack]
    return len(present) >= 2


# --------------------------------------------------------------------------
# Label-anchored values
# --------------------------------------------------------------------------

VALUE_GAP = 25.0

# Every label the header table can print. A run of text that is itself one of
# these is a neighbouring heading, not a value: Tally rules 'Invoice No.',
# 'e-Way Bill No.' and 'Dated' across one row and prints all three values on
# the row beneath.
KNOWN_LABELS: frozenset[str] = frozenset({
    "invoice no", "invoice no.", "invoice number", "invoice dt", "invoice date",
    "bill no", "dated", "date", "delivery note", "delivery note date",
    "mode/terms of payment", "terms of payment", "payment terms",
    "reference no. & date.", "reference no", "other references",
    "buyer's order no", "buyers order no", "dispatch doc no", "dispatched through",
    "destination", "terms of delivery", "e-way bill no", "e-way bill no.",
    "eway bill no", "e way bill no", "broker name", "broker", "representative",
    "agent", "ack no", "ack no.", "ack date", "ack dt", "irn", "po no. & dt",
    "place of supply", "state name", "state code", "gstin", "gstin/uin",
    "consignee", "buyer", "billed to", "shipped to", "remarks",
    # The e-way annexure. Its transporter fields are routinely left empty,
    # and without these the reader walks on and files the next heading as a
    # transporter's name.
    "transporter id", "transporter name", "transporter gstin", "transin",
    "doc no", "doc no.", "vehicle no", "vehicle no.", "cewb no", "cewb no.",
    "generated date", "generated by", "valid upto", "approx distance",
    "supply type", "transaction type", "mode", "name", "date", "from", "to",
    "dispatch from", "ship to", "address details", "goods details",
    "transportation details", "vehicle details", "e-way bill details",
    "tot.taxable amt", "other amt", "total inv amt",
})

# '4. Transportation Details' is a section heading, whatever follows the
# number. Nothing printed on a bill is named like that.
_SECTION_HEADING = re.compile(r"^\d+\s*[.)]\s*\S")


def _norm_label(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9/&. ]+", " ", text.lower()).split())


def label_value(pages: list[PageLayout], aliases: tuple[str, ...],
                max_words: int = 4, below: bool = True) -> str | None:
    """The text printed for a label, whether it sits beside or beneath it.

    Crystal Reports writes 'Invoice No : 14593 / 2026-27' on one line; Tally
    puts 'Invoice No.' in a box with the number underneath. Both are common
    across the sixty layouts, so both are tried — to the right first, then
    directly below within the label's own column.
    """
    wanted = tuple(_norm_label(a).rstrip(" :.") for a in aliases)
    for page in pages:
        lines = page.lines
        for idx, line in enumerate(lines):
            words = sorted(line.words, key=lambda w: w.x0)
            for i, word in enumerate(words):
                for span in range(min(max_words, len(words) - i), 0, -1):
                    phrase = _norm_label(" ".join(w.text for w in words[i:i + span]))
                    if phrase.rstrip(" :.") not in wanted:
                        continue
                    right = _run_after(words, i + span)
                    if right and not _is_label(right):
                        return right
                    if below and idx + 1 < len(lines):
                        bound = _next_label_x(words, i + span)
                        for follow in lines[idx + 1: idx + 3]:
                            under = _run_below(follow, word.x0, bound)
                            if under and not _is_label(under):
                                return under
    return None


def _run_after(words: list, start: int) -> str:
    """Words following a label, stopping at the next column or the next label."""
    kept: list[str] = []
    for idx in range(start, len(words)):
        if kept and words[idx].x0 - words[idx - 1].x1 > VALUE_GAP:
            break
        text = words[idx].text.strip(" :")
        if not text:
            continue
        if _norm_label(" ".join(kept + [text])).rstrip(" :.") in _known_labels():
            break
        kept.append(text)
    return " ".join(kept).strip(" :")


@lru_cache(maxsize=1)
def _known_labels() -> frozenset[str]:
    """KNOWN_LABELS put through the same normalisation used to compare them.

    Without this, 'Reference No. & Date.' fails to recognise itself: the
    comparison strips the trailing dot from the candidate but not from the
    entry it is checked against, and a heading is returned as if it were a
    value.
    """
    return frozenset(_norm_label(label).rstrip(" :.") for label in KNOWN_LABELS)


def _is_label(text: str) -> bool:
    """Is this text a heading rather than a value?

    Prefixes count. Stopping a run at the next label leaves its opening words
    behind — 'Invoice No.' followed by 'e-Way Bill No.' yields the fragment
    'e-Way Bill', which is no more a value than the whole label is.
    """
    stripped = text.strip()
    if _SECTION_HEADING.match(stripped) and not any(ch.isdigit() for ch in stripped[2:]):
        return True
    norm = _norm_label(stripped).rstrip(" :.")
    if not norm:
        return True
    return any(label == norm or label.startswith(norm + " ")
               for label in _known_labels())


def _next_label_x(words: list, start: int, max_words: int = 4) -> float:
    """Where the label to the right begins, bounding this label's column."""
    for j in range(start, len(words)):
        for span in range(min(max_words, len(words) - j), 0, -1):
            phrase = _norm_label(" ".join(w.text for w in words[j:j + span]))
            if phrase.rstrip(" :.") in _known_labels():
                return words[j].x0
    return float("inf")


def _run_below(line: Line, x0: float, x_max: float = float("inf"),
               tolerance: float = 8.0) -> str:
    """Words on the next line lying inside the label's own column.

    The column bound matters: Tally rules 'Invoice No.', 'e-Way Bill No.' and
    'Dated' across one row, so an unbounded read beneath the first of them
    returns all three values run together.
    """
    kept = []
    for word in sorted(line.words, key=lambda w: w.x0):
        if word.x0 < x0 - tolerance or word.x0 >= x_max - 2:
            continue
        if kept and word.x0 - kept[-1].x1 > VALUE_GAP:
            break
        kept.append(word)
    return " ".join(w.text for w in kept).strip(" :")
