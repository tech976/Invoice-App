"""Numbers as they are actually said in a mandi.

A broker mixes languages inside one sentence — 'Virat ko pachas bori aath sau
tera mein' — and whichever engine transcribes it may return digits, Devanagari,
Gujarati script or romanised words, sometimes several within one utterance.
All of them have to come out as a number.

Two habits matter and are handled explicitly: rates are said digit-wise
('eight thirteen' for 813), and Indian numbering groups in hundreds and lakhs
rather than thousands and millions.
"""
from __future__ import annotations

import re
import unicodedata

# Devanagari and Gujarati digits normalise to ASCII through unicodedata, but
# only one character at a time.
_DIGIT_RE = re.compile(r"\d[\d,]*\.?\d*")

UNITS: dict[str, int] = {
    # English
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    # Hindi / Marathi, romanised
    "ek": 1, "do": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4,
    "panch": 5, "paanch": 5, "che": 6, "chhe": 6, "chah": 6,
    "saat": 7, "sat": 7, "aath": 8, "ath": 8, "nau": 9, "nav": 9,
    "das": 10, "dus": 10, "daha": 10,
    "gyarah": 11, "akara": 11, "barah": 12, "bara": 12,
    "terah": 13, "tera": 13, "chaudah": 14, "chauda": 14,
    "pandrah": 15, "pandra": 15, "solah": 16, "sola": 16,
    "satrah": 17, "satra": 17, "atharah": 18, "athara": 18,
    "unnis": 19, "ekonis": 19,
    # Devanagari
    "शून्य": 0, "एक": 1, "दो": 2, "दोन": 2, "तीन": 3, "चार": 4,
    "पांच": 5, "पाँच": 5, "पाच": 5, "छह": 6, "सहा": 6, "सात": 7,
    "आठ": 8, "नौ": 9, "नऊ": 9, "दस": 10, "दहा": 10,
    "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15,
    "सोलह": 16, "सत्रह": 17, "अठारह": 18, "उन्नीस": 19,
    # Gujarati
    "શૂન્ય": 0, "એક": 1, "બે": 2, "ત્રણ": 3, "ચાર": 4, "પાંચ": 5,
    "છ": 6, "સાત": 7, "આઠ": 8, "નવ": 9, "દસ": 10,
    "અગિયાર": 11, "બાર": 12, "તેર": 13, "ચૌદ": 14, "પંદર": 15,
    "સોળ": 16, "સત્તર": 17, "અઢાર": 18, "ઓગણીસ": 19,
}

TENS: dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    # Hindi / Marathi
    "bees": 20, "bis": 20, "vees": 20, "tees": 30, "tis": 30,
    "chalis": 40, "chalees": 40, "pachas": 50, "pachaas": 50, "pannas": 50,
    "saath": 60, "sath": 60, "saath_": 60, "sattar": 70, "sattar_": 70,
    "assi": 80, "ainshi": 80, "nabbe": 90, "navvad": 90,
    "बीस": 20, "वीस": 20, "तीस": 30, "चालीस": 40, "पचास": 50, "पन्नास": 50,
    "साठ": 60, "सत्तर": 70, "अस्सी": 80, "नब्बे": 90, "नव्वद": 90,
    # Gujarati
    "વીસ": 20, "ત્રીસ": 30, "ચાળીસ": 40, "પચાસ": 50,
    "સાઠ": 60, "સિત્તેર": 70, "એંસી": 80, "નેવું": 90,
}

# Contracted hundreds. Gujarati and Marathi say these as one word rather than
# 'two hundred', and they are common enough in rates to be worth listing.
CONTRACTED: dict[str, int] = {
    "baso": 200, "બસો": 200, "दोसौ": 200, "बेसो": 200,
    "tinso": 300, "ત્રણસો": 300, "तीनसौ": 300,
    "charso": 400, "ચારસો": 400, "चारसौ": 400,
    "pachso": 500, "પાંચસો": 500, "पांचसौ": 500,
    "chheso": 600, "છસો": 600, "saatso": 700, "સાતસો": 700,
    "aathso": 800, "આઠસો": 800, "nauso": 900, "નવસો": 900,
    "teraso": 1300, "તેરસો": 1300,
}



MULTIPLIERS: dict[str, int] = {
    # Romanised 'so' for a hundred is left out on purpose: standing alone it
    # is far more often the English filler, and the contracted forms that do
    # use it ('aathso', 'teraso') are listed above.
    "hundred": 100, "sau": 100, "shambhar": 100,
    "सौ": 100, "शंभर": 100, "સો": 100,
    "thousand": 1000, "hazaar": 1000, "hajar": 1000, "hazar": 1000,
    "हजार": 1000, "હજાર": 1000,
    "lakh": 100000, "lac": 100000, "lakhs": 100000,
    "लाख": 100000, "લાખ": 100000,
}

# Words that carry no numeric value but sit inside a spoken number.
FILLERS = {"and", "aur", "ane", "ni", "che", "और"}


def normalise_digits(text: str) -> str:
    """'५०' and '૮૧૩' become '50' and '813'."""
    out = []
    for ch in text:
        if ch.isdigit() and not ch.isascii():
            try:
                out.append(str(unicodedata.decimal(ch)))
                continue
            except (TypeError, ValueError):
                pass
        out.append(ch)
    return "".join(out)


def strip_marks(text: str) -> str:
    """'ṭiranave' and 'tiranave' are the same word.

    Whisper transliterates Devanagari with academic diacritics when it feels
    like it — ṭ, ā, ṇ — and a table keyed on plain letters then misses the
    number entirely, which turns 893 into 800 without complaint.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    # Devanagari must survive: decomposing it and dropping the marks would
    # destroy the vowel signs that make the word.
    return stripped if stripped.isascii() else text


def _word_value(token: str) -> tuple[str, int] | None:
    key = strip_marks(token.strip(".,:;!?")).lower()
    for table, kind in ((UNITS, "unit"), (TENS, "ten"),
                        (CONTRACTED, "whole"), (MULTIPLIERS, "mult")):
        if key in table:
            return kind, table[key]
    return None


def parse_number_words(tokens: list[str]) -> int | None:
    """Fold a run of number words into one value.

    Three habits break a naive left-to-right sum:

    * 'aath sau tera' is 8x100 + 13, not 8 + 100 + 13;
    * a bare 'sau' or 'hazaar' means one hundred, one thousand;
    * a rate is dictated digit-wise — 'eight thirteen' is 813, never 21.

    The last one is decided by shape. 'twenty five' is a tens word followed by
    a unit, which is how 25 is said. Two plain units side by side is not how
    any number is said, so they are being read out digit by digit.
    """
    atoms: list[tuple[str, int]] = []
    for token in tokens:
        hit = _word_value(token)
        if hit is None:
            if token.strip(".,").lower() in FILLERS:
                continue
            return None
        atoms.append(hit)
    if not atoms:
        return None

    if not any(kind == "mult" for kind, _ in atoms):
        if any(kind == "whole" for kind, _ in atoms):
            return sum(value for _, value in atoms)
        # Group first, then read the groups out one after another. A tens word
        # with a unit after it is one number — 'ninety three' is 93 — and only
        # then are the groups strung together: 'seven ninety three' is 7 and
        # 93 spoken in turn, which is 793 and never 7903.
        groups: list[int] = []
        index = 0
        while index < len(atoms):
            kind, value = atoms[index]
            if (kind == "ten" and index + 1 < len(atoms)
                    and atoms[index + 1][0] == "unit"):
                groups.append(value + atoms[index + 1][1])
                index += 2
            else:
                groups.append(value)
                index += 1
        if len(groups) == 1:
            return groups[0]
        return int("".join(str(g) for g in groups))

    total = 0
    current = 0
    for kind, value in atoms:
        if kind == "whole":
            total += current + value
            current = 0
        elif kind in ("unit", "ten"):
            current += value
        else:
            current = (current or 1) * value
            if value >= 1000:
                total += current
                current = 0
    return total + current


def split_hyphens(text: str) -> str:
    """'thirty-three' is two words. Whisper writes it as one.

    Left joined it matches no entry in any table and the number vanishes —
    a quantity silently becoming nothing.
    """
    return re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", " ", text)


def numbers_in(text: str) -> list[tuple[int, int, float]]:
    """Every number in the sentence as (start word, end word, value).

    Positions are word indices, because what a number *means* is decided by
    the words beside it — a unit after it makes it a quantity, a rate cue
    before it makes it a price.
    """
    words = split_hyphens(normalise_digits(text)).split()
    found: list[tuple[int, int, float]] = []
    i = 0
    while i < len(words):
        cleaned = words[i].strip(".,:;!?₹$£€").replace(",", "")
        if _DIGIT_RE.fullmatch(cleaned):
            # Rates are dictated digit-wise: 'eight thirteen' or '8 13'.
            j = i + 1
            run = [cleaned]
            while j < len(words):
                nxt = words[j].strip(".,:;!?₹$£€").replace(",", "")
                if _DIGIT_RE.fullmatch(nxt) and len(run) < 3 and len(nxt) <= 2:
                    run.append(nxt)
                    j += 1
                else:
                    break
            value = float("".join(run)) if len(run) > 1 else float(cleaned)
            found.append((i, j - 1, value))
            i = j
            continue

        if _word_value(words[i]) is not None:
            j = i
            while j < len(words) and (
                _word_value(words[j]) is not None
                or words[j].strip(".,").lower() in FILLERS
            ):
                j += 1
            value = parse_number_words(words[i:j])
            if value is not None:
                found.append((i, j - 1, float(value)))
            i = j
            continue
        i += 1
    return found
