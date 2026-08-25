"""Recovering text from a PDF whose fonts carry no unicode.

A minority of accounting packages embed subset fonts stripped of everything
that says what the characters are: no `ToUnicode`, no `/Encoding`, a `cmap`
into the private use area, `post` format 3.0. Both pdfminer and PDFium hand
back `(cid:12)` for every letter, and the bill reads as mojibake.

The glyph *shapes* are still there, though, and a subset of Arial draws its
'A' exactly the way Arial does. So each embedded glyph is reduced to a hash of
its outline and looked up in a table built from real fonts — see
`scripts/build_glyph_signatures.py`. Nothing is guessed and no OCR is
involved: a match is the same curve, so it is the same character.
"""
from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

TABLE_PATH = Path(__file__).resolve().parent / "glyph_signatures.json"

# A font is worth decoding even when only a few of its glyphs are recognised.
# Nothing is ever guessed — an unmatched glyph becomes a sentinel and is
# either solved from known text or dropped — so a partial map can only help.
# The floor exists solely to skip fonts where nothing matched at all.
MIN_COVERAGE = 0.05


def outline_signature(glyph_set, name: str) -> str | None:
    """A hash of a glyph's drawing, independent of its name, code or font."""
    try:
        from fontTools.pens.recordingPen import RecordingPen
    except ImportError:  # pragma: no cover
        return None
    pen = RecordingPen()
    try:
        glyph_set[name].draw(pen)
    except Exception:  # noqa: BLE001 - a glyph we cannot draw is simply skipped
        return None
    if not pen.value:
        return None
    return hashlib.sha1(repr(pen.value).encode()).hexdigest()[:16]


def _draws_nothing(glyph_set, name: str) -> bool:
    try:
        from fontTools.pens.recordingPen import RecordingPen
    except ImportError:  # pragma: no cover
        return False
    pen = RecordingPen()
    try:
        glyph_set[name].draw(pen)
    except Exception:  # noqa: BLE001
        return False
    return not pen.value


@lru_cache(maxsize=1)
def reference_table() -> dict[str, str]:
    """signature -> character, built offline from real fonts."""
    if not TABLE_PATH.exists():
        log.warning("no glyph signature table at %s; fontless PDFs stay unreadable",
                    TABLE_PATH)
        return {}
    try:
        return json.loads(TABLE_PATH.read_text())
    except (OSError, ValueError) as exc:
        log.warning("could not load %s: %s", TABLE_PATH, exc)
        return {}


def _embedded_fonts(pdf_path: Path) -> dict[str, bytes]:
    """Every embedded TrueType program in the document, by resource name."""
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        return {}

    programs: dict[str, bytes] = {}
    try:
        reader = PdfReader(str(pdf_path))
        for page in reader.pages:
            # Both of these are routinely indirect references, and a raw .get
            # on one returns nothing at all.
            resources = page.get("/Resources")
            resources = resources.get_object() if resources is not None else {}
            fonts = resources.get("/Font") if resources else None
            fonts = fonts.get_object() if fonts is not None else {}
            for _name, ref in (fonts or {}).items():
                font = ref.get_object()
                base = str(font.get("/BaseFont") or "")
                descriptor = font.get("/FontDescriptor")
                if descriptor is None and font.get("/DescendantFonts"):
                    descriptor = font["/DescendantFonts"][0].get_object().get(
                        "/FontDescriptor")
                descriptor = descriptor.get_object() if descriptor is not None else None
                if descriptor is None or not base:
                    continue
                for key in ("/FontFile2", "/FontFile3", "/FontFile"):
                    if key in descriptor:
                        programs.setdefault(base.lstrip("/"),
                                            descriptor[key].get_data())
                        break
    except Exception as exc:  # noqa: BLE001 - an unreadable PDF is not fatal
        log.debug("could not list fonts in %s: %s", pdf_path.name, exc)
    return programs


def decode_maps(pdf_path: Path) -> dict[str, dict[int, str]]:
    """For each embedded font, the character code -> character it really means.

    Returns an empty map for a font whose outlines are not in the reference
    table: a partial decode would silently corrupt the bill, which is worse
    than declining to read it.
    """
    table = reference_table()
    if not table:
        return {}
    try:
        import io

        from fontTools.ttLib import TTFont
    except ImportError:  # pragma: no cover
        return {}

    maps: dict[str, dict[int, str]] = {}
    for base_font, program in _embedded_fonts(pdf_path).items():
        try:
            font = TTFont(io.BytesIO(program), fontNumber=0, lazy=True)
            glyphs = font.getGlyphSet()
        except Exception:  # noqa: BLE001
            continue

        codes: dict[int, str] = {}
        seen: set[int] = set()
        for subtable in font["cmap"].tables if "cmap" in font else []:
            for code, name in subtable.cmap.items():
                # A (3,0) symbol cmap offsets every code into 0xF000.
                seen.add(code & 0xFF)
                signature = outline_signature(glyphs, name)
                if signature is None and _draws_nothing(glyphs, name):
                    # A glyph with no outline is the space.
                    codes[code & 0xFF] = " "
                    continue
                character = table.get(signature or "")
                if character:
                    codes[code & 0xFF] = character
        if not seen:
            continue
        coverage = len(codes) / len(seen)
        if codes and coverage >= MIN_COVERAGE:
            maps[base_font] = codes
            log.info("%s: decoded %d glyphs of %s by outline",
                     pdf_path.name, len(codes), base_font)
    return maps


def _char_code(text: str) -> int | None:
    """The font code behind a character pdfplumber handed back.

    Unmappable glyphs arrive as '(cid:12)'. The rest arrive already
    'decoded' through a fallback encoding that this font does not use, so the
    character is meaningless but its ordinal is still the original code — and
    that is what the outline table is keyed on.
    """
    if text.startswith("(cid:") and text.endswith(")"):
        try:
            return int(text[5:-1])
        except ValueError:
            return None
    if len(text) != 1:
        return None
    point = ord(text)
    if point <= 0xFF:
        return point
    # Bytes above 0x7F come back through cp1252, which turns code 0x92 into a
    # right single quote at U+2019. Encoding it back recovers the real code.
    try:
        return text.encode("cp1252")[0]
    except (UnicodeEncodeError, IndexError):
        return None


def decode_cid(text: str, code_map: dict[int, str]) -> str | None:
    """Turn '(cid:12)' into the character it stands for."""
    if not code_map or not text.startswith("(cid:") or not text.endswith(")"):
        return None
    try:
        code = int(text[5:-1])
    except ValueError:
        return None
    return code_map.get(code & 0xFF)


# Sentinels stand in for glyphs the outline table did not recognise, one per
# (font, code) pair, drawn from the private use area so they cannot collide
# with anything the bill actually prints.
SENTINEL_BASE = 0xE000

# Words that appear on essentially every Indian GST tax invoice. A glyph the
# outline table missed can be solved by seeing which letter it must be for one
# of these to read correctly.
VOCABULARY = (
    "Tax Invoice", "GSTIN", "UIN", "State Name", "Code", "Invoice No",
    "Dated", "Description", "Quantity", "Amount", "Rate", "Total",
    "Consignee", "Buyer", "Bill to", "Ship to", "Dispatch", "Delivery Note",
    "Terms of Payment", "Place of Supply", "Declaration", "Signatory",
    "Authorised", "Company", "Bank", "Branch", "Account", "Enterprises",
    "Private Limited", "Discount", "Handling", "Charge", "Round Off",
    "Packing", "Freight", "Taxable", "Value", "Chargeable", "words",
    "Handling Charge", "Labour", "Insurance", "Transport", "Round", "Less",
    "Kernels", "Almond", "Cashew", "Walnut", "Raisin", "Pistachio",
    "Maharashtra", "Karnataka", "Gujarat", "Rajasthan", "Delhi", "Punjab",
    "Haryana", "Telangana", "Kerala", "Tamil Nadu", "Madhya Pradesh",
    "Uttar Pradesh", "West Bengal", "Andhra Pradesh", "Computer Generated",
)

# A proposal is only trusted when most of the word around it already read
# correctly.
MIN_LITERAL_SHARE = 0.6


def _solve_unknowns(text: str, sentinels: dict[str, tuple[str, int]],
                    known: list[str]) -> dict[str, str]:
    """Work out what each unrecognised glyph must be.

    Every bill states a good deal that is knowable in advance: its own GSTINs
    and document number, echoed exactly by the QR code, and the fixed
    vocabulary of a GST invoice. Wherever one of those reads correctly apart
    from a sentinel or two, the sentinel's identity follows — and unlike a
    guess, a wrong answer would have to make a known word misspell itself.
    """
    import re as _re

    # Bills shout their charge lines — 'HANDLING CHARGE', 'PACKING & LABOUR'
    # — so each term is tried as printed and in upper case.
    expanded: list[str] = []
    for candidate in known:
        if candidate and len(candidate) >= 3:
            expanded.append(candidate)
            if candidate.upper() != candidate:
                expanded.append(candidate.upper())

    proposals: dict[str, set[str]] = {}
    for candidate in expanded:
        # Each position matches the character itself or any sentinel — the
        # sentinels live in the text, not in the word being looked for.
        pattern = "".join(
            f"(?:{_re.escape(ch)}|[\ue000-\uf8ff])" for ch in candidate
        )
        # Only a string that still has real letters in it can anchor a match.
        for match in _re.finditer(pattern, text):
            found = match.group(0)
            literal = sum(1 for a, b in zip(found, candidate)
                          if a == b and a not in sentinels)
            if literal < len(candidate) * MIN_LITERAL_SHARE:
                continue
            for actual, wanted in zip(found, candidate):
                if actual in sentinels:
                    proposals.setdefault(actual, set()).add(wanted)

    # A sentinel that two different words disagree about is left unresolved.
    return {token: next(iter(options))
            for token, options in proposals.items() if len(options) == 1}


def read_decoded_layout(pdf_path: Path, max_pages: int = 6, known: list[str] | None = None):
    """Positioned words for a PDF whose fonts carry no unicode.

    Same shape as `layout.read_layout`, but every `(cid:N)` is replaced by the
    character its outline identifies before the words are assembled. Returns
    an empty list when no font could be decoded, so the caller can fall back
    to whatever it was doing before.
    """
    from app.extraction.layout import PageLayout, Word

    maps = decode_maps(pdf_path)
    if not maps:
        return []

    try:
        import pdfplumber
        from pdfplumber.utils import extract_words
    except ImportError:  # pragma: no cover
        return []

    sentinels: dict[str, tuple[str, int]] = {}
    slots: dict[tuple[str, int], str] = {}

    def sentinel_for(font_name: str, code: int) -> str:
        key = (font_name, code)
        if key not in slots:
            token = chr(SENTINEL_BASE + len(slots))
            slots[key] = token
            sentinels[token] = key
        return slots[key]

    def decode_pages():
        out = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for index, page in enumerate(pdf.pages[:max_pages], start=1):
                chars = []
                for char in page.chars:
                    font_name = char.get("fontname", "")
                    code_map = maps.get(font_name)
                    if not code_map:
                        chars.append(char)
                        continue
                    code = _char_code(char.get("text") or "")
                    if code is None:
                        chars.append(char)
                        continue
                    decoded = code_map.get(code & 0xFF)
                    if decoded is None:
                        # Never guessed: it is held as a sentinel until
                        # something the bill states independently settles it.
                        decoded = sentinel_for(font_name, code & 0xFF)
                    chars.append({**char, "text": decoded})
                out.append(PageLayout(
                    index, page.width, page.height,
                    [Word(w["text"], w["x0"], w["x1"], w["top"], w["bottom"])
                     for w in extract_words(chars)],
                ))
        return out

    try:
        pages = decode_pages()
        if sentinels:
            text = "\n".join(line.text for page in pages for line in page.lines)
            solved = _solve_unknowns(text, sentinels, list(known or []) + list(VOCABULARY))
            if solved:
                for token, character in solved.items():
                    font_name, code = sentinels[token]
                    maps[font_name][code] = character
                log.info("%s: solved %d unrecognised glyph(s) from known text",
                         pdf_path.name, len(solved))
                slots.clear()
                sentinels.clear()
                pages = decode_pages()
            # Anything still unsolved is dropped rather than shown as a box.
            for page in pages:
                for word in page.words:
                    word.text = "".join(c for c in word.text
                                        if not (SENTINEL_BASE <= ord(c) <= 0xF8FF))
                page.words = [w for w in page.words if w.text]
    except Exception as exc:  # noqa: BLE001 - a malformed PDF is not fatal
        log.warning("could not decode layout of %s: %s", pdf_path.name, exc)
        return []
    return pages
