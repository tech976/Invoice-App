"""Turn what the model read into what the database stores.

Everything here is deterministic — no model involved. These helpers are also
what the review screen uses when a human retypes a value, so a corrected field
goes through exactly the same cleaning as an extracted one.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dateutil import parser as dateparser

# --------------------------------------------------------------------------
# Amounts
# --------------------------------------------------------------------------

_CURRENCY = re.compile(r"(?i)\b(?:inr|rs\.?|rupees?)\b|[₹$]")
_NUM_JUNK = re.compile(r"[^\d.\-]")


def parse_amount(value) -> Decimal | None:
    """Parse a money/quantity value from anything a bill might print.

    Handles Indian grouping (`58,46,893.00`), accounting negatives
    (`(1,234.00)`), the `(-)` prefix Tally uses, and stray currency words.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None

    text = str(value).strip()
    if not text:
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if "(-)" in text or "(–)" in text:
        negative, text = True, text.replace("(-)", "").replace("(–)", "")

    text = _CURRENCY.sub("", text)
    text = text.replace("–", "-").replace("—", "-")
    # Commas are pure grouping in Indian notation, whatever the spacing.
    text = text.replace(",", "").replace(" ", "")
    text = _NUM_JUNK.sub("", text)

    if text.count("-") and not text.startswith("-"):
        text = text.replace("-", "")
    if not text or text in {"-", ".", "-."}:
        return None

    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return -amount if negative and amount > 0 else amount


def parse_percent(value) -> Decimal | None:
    """`1.50 %` -> 1.5 ; `5%` -> 5 ; `0.05` stays 0.05."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace("%", "").strip()
    return parse_amount(value)


def q2(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(Decimal("0.01"))


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------


def parse_date(value) -> date | None:
    """Parse a date, always day-first — Indian bills never print month-first.

    `03/04/2026` is 3 April 2026. `21-Jul-26` is 21 July 2026.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None
    # Drop a trailing time component such as '21-Jul-26 6:31 PM'.
    text = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?\s*([APap][Mm])?$", "", text).strip()

    try:
        parsed = dateparser.parse(text, dayfirst=True, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None

    # dateutil maps a bare 2-digit year onto a sliding window; bills are
    # always this century.
    if parsed.year < 100:
        parsed = parsed.replace(year=2000 + parsed.year)
    return parsed


def financial_year(d: date | None) -> str | None:
    """Indian FY runs April-March. 2026-07-21 -> '2026-27'."""
    if not d:
        return None
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


# --------------------------------------------------------------------------
# GSTIN / PAN
# --------------------------------------------------------------------------

_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z][Z][0-9A-Z]$")
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman and Diu", "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra", "29": "Karnataka", "30": "Goa", "31": "Lakshadweep",
    "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory",
}


def clean_gstin(value) -> str | None:
    if not value:
        return None
    text = re.sub(r"[^0-9A-Za-z]", "", str(value)).upper()
    return text or None


def gstin_checksum_ok(gstin: str) -> bool:
    """Verify the 15th character.

    Each of the first 14 characters is weighted 1, 2, 1, 2..., the product is
    reduced base-36 (quotient plus remainder), and the checksum is what makes
    the running total a multiple of 36. Catches a mistyped or misread digit.
    """
    if len(gstin) != 15:
        return False
    try:
        total = 0
        for i, ch in enumerate(gstin[:14]):
            code = _GSTIN_CHARS.index(ch)
            product = code * (2 if i % 2 else 1)
            total += product // 36 + product % 36
        expected = _GSTIN_CHARS[(36 - total % 36) % 36]
    except ValueError:
        return False
    return expected == gstin[14]


def validate_gstin(value) -> tuple[str | None, list[str]]:
    """Return (cleaned GSTIN, list of problems). Empty problems means valid."""
    gstin = clean_gstin(value)
    if not gstin:
        return None, []

    problems: list[str] = []
    if len(gstin) != 15:
        problems.append(f"GSTIN is {len(gstin)} characters, expected 15")
        return gstin, problems
    if not _GSTIN_RE.match(gstin):
        problems.append("GSTIN does not match the standard pattern")
    if gstin[:2] not in STATE_CODES:
        problems.append(f"'{gstin[:2]}' is not a valid GST state code")
    if not gstin_checksum_ok(gstin):
        problems.append("GSTIN checksum digit does not match — likely a misread character")
    return gstin, problems


def state_from_gstin(gstin: str | None) -> tuple[str | None, str | None]:
    """(state_code, state_name) implied by a GSTIN."""
    gstin = clean_gstin(gstin)
    if not gstin or len(gstin) < 2:
        return None, None
    code = gstin[:2]
    return code, STATE_CODES.get(code)


def pan_from_gstin(gstin: str | None) -> str | None:
    gstin = clean_gstin(gstin)
    if gstin and len(gstin) == 15:
        candidate = gstin[2:12]
        if _PAN_RE.match(candidate):
            return candidate
    return None


def clean_pan(value) -> str | None:
    if not value:
        return None
    text = re.sub(r"[^0-9A-Za-z]", "", str(value)).upper()
    return text if _PAN_RE.match(text) else (text or None)


# --------------------------------------------------------------------------
# Codes and text
# --------------------------------------------------------------------------

_UOM_MAP = {
    "kg": "KGS", "kgs": "KGS", "kilogram": "KGS", "kilograms": "KGS",
    "kilo": "KGS", "kgm": "KGS",
    "qtl": "QTL", "quintal": "QTL", "ton": "MTS", "tons": "MTS",
    "mt": "MTS", "mts": "MTS", "tonne": "MTS", "tonnes": "MTS",
    "gm": "GMS", "gms": "GMS", "gram": "GMS", "grams": "GMS",
    "pcs": "PCS", "pc": "PCS", "piece": "PCS", "pieces": "PCS", "nos": "PCS",
    "no": "PCS", "unit": "PCS", "units": "PCS", "each": "PCS",
    "box": "BOX", "boxes": "BOX", "ctn": "CTN", "carton": "CTN",
    "cartons": "CTN", "bag": "BAG", "bags": "BAG", "pkt": "PKT",
    "packet": "PKT", "packets": "PKT", "tin": "TIN", "tins": "TIN",
    "ltr": "LTR", "litre": "LTR", "litres": "LTR", "liter": "LTR", "l": "LTR",
    "mtr": "MTR", "meter": "MTR", "metre": "MTR", "sqft": "SQF", "sqm": "SQM",
}


def normalize_uom(value) -> str | None:
    if not value:
        return None
    text = re.sub(r"[^A-Za-z]", "", str(value)).lower()
    if not text:
        return None
    return _UOM_MAP.get(text, text.upper()[:12])


def normalize_hsn(value) -> str | None:
    """HSN/SAC as digits only. `0802.1200` -> `08021200`."""
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        return None
    # A leading zero is routinely dropped by spreadsheets; GST codes are
    # 4, 6 or 8 digits, so a 7-digit code is a stripped 8-digit one.
    if len(digits) in (3, 5, 7):
        digits = "0" + digits
    return digits[:12]


def normalize_vehicle_no(value) -> str | None:
    if not value:
        return None
    text = re.sub(r"[^0-9A-Za-z]", "", str(value)).upper()
    return text or None


_NAME_NOISE = re.compile(
    r"(?i)\b(private|pvt|limited|ltd|llp|inc|corporation|corp|company|co|"
    r"enterprises?|enterprise|traders?|trading|industries|industry|agro|"
    r"exports?|imports?|impex|and|&|the|m/s|messrs)\b"
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_name(value) -> str:
    """A comparison key for party names.

    'Riverstone Impex Private Limited B-12' and 'RIVERSTONE IMPEX PVT LTD B12' both reduce
    to 'lcdf e46', so the same firm matches across bills that spell it
    differently. Kept deliberately aggressive — it is only ever a *candidate*
    key, and GSTIN wins whenever one is present.
    """
    if not value:
        return ""
    text = str(value).lower()
    text = _NAME_NOISE.sub(" ", text)
    text = _NON_ALNUM.sub(" ", text)
    return " ".join(text.split())


def normalize_product_name(value) -> str:
    if not value:
        return ""
    text = str(value).lower()
    text = _NON_ALNUM.sub(" ", text)
    return " ".join(text.split())


def clean_text(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


# Legal-form abbreviations, expanded so that 'Pvt Ltd' and 'Private Limited'
# compare as identical text.
_ABBREV = {
    "pvt": "private", "pv": "private", "ltd": "limited", "lmtd": "limited",
    "co": "company", "corp": "corporation", "inc": "incorporated",
    "ent": "enterprises", "enterprise": "enterprises",
    "inds": "industries", "ind": "industries", "industry": "industries",
    "exp": "exports", "export": "exports", "imp": "imports", "import": "imports",
    "trader": "traders", "trdrs": "traders", "agencies": "agency",
    "bros": "brothers", "mfg": "manufacturing", "intl": "international",
    "&": "and", "ms": "", "messrs": "",
}


def normalize_name_light(value) -> str:
    """A gentler key than `normalize_name`: case, punctuation, abbreviations.

    `normalize_name` deliberately throws away 'Enterprises', 'Trading', 'Pvt
    Ltd' so that spelling variants collapse — but that also makes 'Sunrise
    Enterprises' and 'Shan Trading Co' look alike. Keeping those words here,
    with abbreviations expanded, gives the matcher a second and genuinely
    independent opinion.
    """
    if not value:
        return ""
    text = str(value).lower().replace("&", " and ")
    text = _NON_ALNUM.sub(" ", text)
    words = [_ABBREV.get(w, w) for w in text.split()]
    return " ".join(w for w in words if w)
