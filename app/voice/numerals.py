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
    # Marathi spells the teens differently from Hindi.
    "अकरा": 11, "बारा": 12, "तेरा": 13, "चौदा": 14, "पंधरा": 15,
    "सोळा": 16, "सतरा": 17, "अठरा": 18, "एकोणीस": 19,
    "akara": 11, "bara": 12, "tera": 13, "chauda": 14, "pandhra": 15,
    "sola": 16, "satra": 17, "athra": 18, "ekonis": 19,
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
    # Marathi contracts them onto 'शे' rather than 'सौ', and writes them as
    # one word: 'दोनशे क्विंटल' is two hundred quintal. Left out, the number
    # is not recognised at all and the quantity silently goes missing —
    # which is how 'नऊ हजार चारशे' was read as 9000.
    "दोनशे": 200, "donshe": 200, "donshen": 200,
    "तीनशे": 300, "tinshe": 300, "teenshe": 300, "tinshen": 300,
    "चारशे": 400, "charshe": 400, "charshen": 400,
    "पाचशे": 500, "पांचशे": 500, "pachshe": 500, "panchshe": 500,
    "सहाशे": 600, "sahashe": 600,
    "सातशे": 700, "satshe": 700, "saatshe": 700,
    "आठशे": 800, "aathshe": 800, "athshe": 800,
    "नऊशे": 900, "नउशे": 900, "naushe": 900,
    "अकराशे": 1100, "akarashe": 1100,
    "बाराशे": 1200, "barashe": 1200,
    "तेराशे": 1300, "terashe": 1300,
}




# Hindi and Marathi name every number to a hundred separately —
# 'तिरानवे' is ninety-three and resembles neither ninety nor three. This
# table is passive: it costs nothing unless one of these words is actually
# spoken, so it cannot affect a sentence said in English.
COMPOUND: dict[str, int] = {
    "इक्कीस": 21,
    "ikkis": 21,
    "ikis": 21,
    "एकवीस": 21,
    "ekvis": 21,
    "बाईस": 22,
    "bais": 22,
    "bayis": 22,
    "बावीस": 22,
    "bavis": 22,
    "तेईस": 23,
    "teis": 23,
    "तेवीस": 23,
    "tevis": 23,
    "चौबीस": 24,
    "chaubis": 24,
    "chobis": 24,
    "चोवीस": 24,
    "chovis": 24,
    "पच्चीस": 25,
    "pachchis": 25,
    "pachis": 25,
    "पंचवीस": 25,
    "panchvis": 25,
    "छब्बीस": 26,
    "chhabbis": 26,
    "सव्वीस": 26,
    "savvis": 26,
    "सत्ताईस": 27,
    "sattais": 27,
    "satais": 27,
    "सत्तावीस": 27,
    "sattavis": 27,
    "अट्ठाईस": 28,
    "atthais": 28,
    "athais": 28,
    "अठ्ठावीस": 28,
    "atthavis": 28,
    "उनतीस": 29,
    "untis": 29,
    "एकोणतीस": 29,
    "ekontis": 29,
    "इकतीस": 31,
    "ikattis": 31,
    "ikatis": 31,
    "एकतीस": 31,
    "ektis": 31,
    "बत्तीस": 32,
    "battis": 32,
    "batis": 32,
    "तैंतीस": 33,
    "taintis": 33,
    "tentis": 33,
    "तेहतीस": 33,
    "tehtis": 33,
    "चौंतीस": 34,
    "chauntis": 34,
    "चौतीस": 34,
    "chautis": 34,
    "पैंतीस": 35,
    "paintis": 35,
    "pentis": 35,
    "पस्तीस": 35,
    "pastis": 35,
    "छत्तीस": 36,
    "chhattis": 36,
    "chattis": 36,
    "सैंतीस": 37,
    "saintis": 37,
    "सदतीस": 37,
    "sadtis": 37,
    "अड़तीस": 38,
    "adtis": 38,
    "उनतालीस": 39,
    "untalis": 39,
    "एकोणचाळीस": 39,
    "ekonchalis": 39,
    "इकतालीस": 41,
    "ikatalis": 41,
    "बयालीस": 42,
    "bayalis": 42,
    "बेचाळीस": 42,
    "bechalis": 42,
    "तैंतालीस": 43,
    "taintalis": 43,
    "त्रेचाळीस": 43,
    "trechalis": 43,
    "चवालीस": 44,
    "chavalis": 44,
    "पैंतालीस": 45,
    "paintalis": 45,
    "पंचेचाळीस": 45,
    "panchechalis": 45,
    "छियालीस": 46,
    "chhiyalis": 46,
    "सैंतालीस": 47,
    "saintalis": 47,
    "अड़तालीस": 48,
    "adtalis": 48,
    "उनचास": 49,
    "unchas": 49,
    "एकोणपन्नास": 49,
    "ekonpannas": 49,
    "पन्नास": 50,
    "pannas": 50,
    "इक्यावन": 51,
    "ikyavan": 51,
    "बावन": 52,
    "bavan": 52,
    "बावन्न": 52,
    "bavanna": 52,
    "तिरपन": 53,
    "tirpan": 53,
    "त्रेपन्न": 53,
    "trepanna": 53,
    "चौवन": 54,
    "chauvan": 54,
    "पचपन": 55,
    "pachpan": 55,
    "पंचावन्न": 55,
    "panchavanna": 55,
    "छप्पन": 56,
    "chhappan": 56,
    "सत्तावन": 57,
    "sattavan": 57,
    "अट्ठावन": 58,
    "atthavan": 58,
    "उनसठ": 59,
    "unsath": 59,
    "एकोणसाठ": 59,
    "ekonsath": 59,
    "इकसठ": 61,
    "iksath": 61,
    "बासठ": 62,
    "basath": 62,
    "बासष्ठ": 62,
    "basashth": 62,
    "तिरसठ": 63,
    "tirsath": 63,
    "त्रेसष्ठ": 63,
    "tresashth": 63,
    "चौंसठ": 64,
    "chausath": 64,
    "पैंसठ": 65,
    "painsath": 65,
    "छियासठ": 66,
    "chhiyasath": 66,
    "सड़सठ": 67,
    "sarsath": 67,
    "अड़सठ": 68,
    "arsath": 68,
    "उनहत्तर": 69,
    "unhattar": 69,
    "एकोणसत्तर": 69,
    "ekonsattar": 69,
    "इकहत्तर": 71,
    "ikhattar": 71,
    "बहत्तर": 72,
    "bahattar": 72,
    "बहात्तर": 72,
    "तिहत्तर": 73,
    "tihattar": 73,
    "चौहत्तर": 74,
    "chauhattar": 74,
    "पचहत्तर": 75,
    "pachhattar": 75,
    "पंच्याहत्तर": 75,
    "panchyahattar": 75,
    "छिहत्तर": 76,
    "chhihattar": 76,
    "सतहत्तर": 77,
    "satattar": 77,
    "अठहत्तर": 78,
    "athhattar": 78,
    "उनासी": 79,
    "unasi": 79,
    "एकोणऐंशी": 79,
    "ekonainshi": 79,
    "इक्यासी": 81,
    "ikyasi": 81,
    "बयासी": 82,
    "bayasi": 82,
    "ब्याऐंशी": 82,
    "byaainshi": 82,
    "तिरासी": 83,
    "tirasi": 83,
    "चौरासी": 84,
    "chaurasi": 84,
    "पचासी": 85,
    "pachasi": 85,
    "पंच्याऐंशी": 85,
    "panchyaainshi": 85,
    "छियासी": 86,
    "chhiyasi": 86,
    "सत्तासी": 87,
    "sattasi": 87,
    "अट्ठासी": 88,
    "atthasi": 88,
    "नवासी": 89,
    "navasi": 89,
    "एकोणनव्वद": 89,
    "ekonnavvad": 89,
    "इक्यानवे": 91,
    "ikyanave": 91,
    "ikyanve": 91,
    "बानवे": 92,
    "banave": 92,
    "banve": 92,
    "ब्याण्णव": 92,
    "byannav": 92,
    "तिरानवे": 93,
    "tiranave": 93,
    "tiranve": 93,
    "त्र्याण्णव": 93,
    "tryannav": 93,
    "चौरानवे": 94,
    "chauranave": 94,
    "पचानवे": 95,
    "pachanave": 95,
    "pachanve": 95,
    "पंच्याण्णव": 95,
    "panchyannav": 95,
    "छियानवे": 96,
    "chhiyanave": 96,
    "सत्तानवे": 97,
    "sattanave": 97,
    "अट्ठानवे": 98,
    "atthanave": 98,
    "निन्यानवे": 99,
    "ninyanave": 99,
    "नव्व्याण्णव": 99,
    "navvyannav": 99,
}


MULTIPLIERS: dict[str, int] = {
    # Romanised 'so' for a hundred is left out on purpose: standing alone it
    # is far more often the English filler, and the contracted forms that do
    # use it ('aathso', 'teraso') are listed above.
    "hundred": 100, "sau": 100, "shambhar": 100,
    # Marathi says 'bara she' for twelve hundred.
    "she": 100, "शे": 100, "shen": 100,
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


_NUKTA = "़"


def _fold(text: str) -> str:
    """A lookup key that ignores marks the transcript may or may not carry.

    Two of them matter here. A romanised transcript arrives with academic
    diacritics whenever the recogniser feels like it — 'ṭiranave' for
    'tiranave'. Devanagari arrives with or without the nukta, and 'हज़ार' and
    'हजार' are the same word said the same way: a table holding only one of
    them misses the other, which is how 'एक हज़ार बीस' was read as 20.
    """
    key = strip_marks(text)
    if not key.isascii():
        key = unicodedata.normalize("NFD", key).replace(_NUKTA, "")
        key = unicodedata.normalize("NFC", key)
    return key.lower()


# Every number word, folded once at import. Built in table order, so a word
# appearing in two tables keeps the meaning the first one gives it.
_INDEX: dict[str, tuple[str, int]] = {}
for _table, _kind in ((UNITS, "unit"), (COMPOUND, "unit"), (TENS, "ten"),
                      (CONTRACTED, "whole"), (MULTIPLIERS, "mult")):
    for _spelling, _value in _table.items():
        _INDEX.setdefault(_fold(_spelling), (_kind, _value))


def _word_value(token: str) -> tuple[str, int] | None:
    return _INDEX.get(_fold(token.strip(".,:;!?")))


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
