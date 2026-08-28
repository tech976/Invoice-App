"""Saying it in Marathi or Hindi, filing it in English.

The broker speaks whichever language the deal happened in. The book is kept in
English, so that reports, exports and anybody reading them later see one
vocabulary rather than three.

Whisper's own `translate` task is not used for this. Asked to translate, it
rewrites the sentence as fluent English prose — 'Micron C. Virat weighs 1020
kg and Badam weighs 723 kg' — which loses the structure the parser reads and,
worse, quietly changed a rate from 793 to 723. Transcribing keeps the words
and the figures exactly as spoken; the terms are then mapped here, where the
mapping is a table somebody can read and correct.

Only trade vocabulary is translated. Names are left exactly as said: a party
called अशापुरा is Ashapura, not 'hope'.
"""
from __future__ import annotations

import re
import unicodedata

# What the goods are called, in the languages a mandi trades in.
COMMODITIES: dict[str, str] = {
    # nuts and dried fruit
    "akhrot": "Walnut", "अखरोट": "Walnut", "अख्रोट": "Walnut", "અખરોટ": "Walnut",
    "badam": "Almond", "बादाम": "Almond", "बदाम": "Almond", "બદામ": "Almond",
    "kaju": "Cashew", "काजू": "Cashew", "काजु": "Cashew", "કાજુ": "Cashew",
    "kishmish": "Raisin", "किशमिश": "Raisin", "કિસમિસ": "Raisin",
    "manuka": "Raisin", "मनुका": "Raisin",
    "pista": "Pistachio", "पिस्ता": "Pistachio", "પિસ્તા": "Pistachio",
    "anjeer": "Fig", "अंजीर": "Fig", "અંજીર": "Fig",
    "khajur": "Dates", "खजूर": "Dates", "ખજૂર": "Dates",
    "khopra": "Copra", "खोपरा": "Copra", "kopra": "Copra",
    "makhana": "Foxnut", "मखाना": "Foxnut",
    "chironji": "Chironji", "चिरौंजी": "Chironji",
    # spices
    "elaichi": "Cardamom", "इलायची": "Cardamom", "એલચી": "Cardamom",
    "kali mirch": "Black pepper", "काली मिर्च": "Black pepper",
    "haldi": "Turmeric", "हल्दी": "Turmeric", "હળદર": "Turmeric",
    # Marathi spells several of these differently from Hindi, and a bill is
    # as often dictated in one as the other.
    "हळद": "Turmeric", "halad": "Turmeric",
    "jire": "Cumin", "जिरे": "Cumin", "dhane": "Coriander", "धणे": "Coriander",
    "mirchi": "Chilli", "मिरची": "Chilli", "मिर्ची": "Chilli",
    "साखर": "Sugar", "sakhar": "Sugar",
    "शेंगदाणा": "Groundnut", "shengdana": "Groundnut",
    "हरभरा": "Gram", "harbhara": "Gram",
    "तूरडाळ": "Pigeon pea", "turdal": "Pigeon pea",
    "मूगडाळ": "Green gram", "mugdal": "Green gram",
    "jeera": "Cumin", "जीरा": "Cumin", "જીરું": "Cumin",
    "dhaniya": "Coriander", "धनिया": "Coriander", "ધાણા": "Coriander",
    "methi": "Fenugreek", "मेथी": "Fenugreek",
    "saunf": "Fennel", "सौंफ": "Fennel",
    "lavang": "Clove", "लौंग": "Clove", "laung": "Clove",
    "dalchini": "Cinnamon", "दालचीनी": "Cinnamon",
    "kesar": "Saffron", "केसर": "Saffron",
    "til": "Sesame", "तिल": "Sesame",
    "supari": "Betel nut", "सुपारी": "Betel nut",
    # pulses and grains
    "chana": "Gram", "चना": "Gram", "ચણા": "Gram",
    "rajma": "Kidney bean", "राजमा": "Kidney bean",
    "moong": "Green gram", "मूंग": "Green gram",
    "tur": "Pigeon pea", "तूर": "Pigeon pea", "toor": "Pigeon pea",
    "urad": "Black gram", "उड़द": "Black gram",
    "masoor": "Lentil", "मसूर": "Lentil",
    "gehu": "Wheat", "गेहूं": "Wheat", "gehun": "Wheat",
    "chawal": "Rice", "चावल": "Rice", "tandul": "Rice", "तांदूळ": "Rice",
    "mungfali": "Groundnut", "मूंगफली": "Groundnut", "shengdana": "Groundnut",
    "sabudana": "Sago", "साबूदाना": "Sago",
    "gud": "Jaggery", "गुड़": "Jaggery", "ગોળ": "Jaggery",
    # English spoken forms, so the book settles on one capitalisation
    # whichever language the deal was struck in.
    "walnut": "Walnut", "walnuts": "Walnut", "almond": "Almond",
    "almonds": "Almond", "cashew": "Cashew", "cashews": "Cashew",
    "raisin": "Raisin", "raisins": "Raisin", "pistachio": "Pistachio",
    "fig": "Fig", "figs": "Fig", "dates": "Dates", "turmeric": "Turmeric",
    "cumin": "Cumin", "coriander": "Coriander", "cardamom": "Cardamom",
    "jaggery": "Jaggery", "sesame": "Sesame", "groundnut": "Groundnut",
    "khand": "Sugar", "खांड": "Sugar", "shakkar": "Sugar", "शक्कर": "Sugar",
}

# Grade and quality words that sit beside a commodity.
QUALIFIERS: dict[str, str] = {
    "sabut": "whole", "साबुत": "whole",
    "tukda": "split", "टुकड़ा": "split", "dal": "split",
    "gola": "ball", "गोला": "ball",
    "chota": "small", "छोटा": "small", "lahan": "small",
    "bada": "large", "बड़ा": "large", "mota": "large", "मोटा": "large",
    "naya": "new", "नया": "new", "juna": "old", "पुराना": "old",
}


# Devanagari and Gujarati letters. A word containing any of these was said in
# a script the book does not keep.
_INDIC = re.compile(r"[\u0900-\u097F\u0A80-\u0AFF]")


def romanise(text: str | None) -> str | None:
    """Write an Indic name in Latin letters.

    Names are not translated — a party called अशापुरा is Ashapura, not 'hope'.
    They are spelled out, so the book holds one spelling of each client rather
    than one per script.

    Hindi and Marathi drop the inherent vowel at the end of a word, so
    'virATa' is read Virat and 'nilesha' Nilesh. Left in, every name would
    carry a syllable nobody says.
    """
    if not text or not _INDIC.search(text):
        return text
    try:
        from indic_transliteration import sanscript
        from indic_transliteration.sanscript import transliterate
    except ImportError:  # pragma: no cover - optional
        return text

    out: list[str] = []
    for word in text.split():
        if not _INDIC.search(word):
            out.append(word)
            continue
        roman = transliterate(word, sanscript.DEVANAGARI, sanscript.ITRANS)
        # ITRANS writes the long vowel as 'A' and the inherent short one as
        # 'a'. Only the short one is dropped: 'virATa' is Virat, but
        # 'ashApurA' keeps its ending and is Ashapura, not Ashapur.
        if len(roman) > 3 and roman.endswith("a"):
            roman = roman[:-1]
        roman = re.sub(r"[^a-zA-Z]", "", roman).lower()
        out.append(roman.capitalize() if roman else word)
    return " ".join(out)


def _key(text: str) -> str:
    """Fold a word to the form the tables are keyed on."""
    folded = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    cleaned = ascii_only if ascii_only.isascii() else text
    return re.sub(r"[^\wऀ-ॿ઀-૿ ]+", "", cleaned.lower()).strip()


def term(text: str | None) -> str | None:
    """Translate one spoken term into English, or leave it alone.

    A word with no entry is returned unchanged. That is deliberate: an
    unknown word is far more likely to be somebody's name or a commodity we
    have not listed than a mistake worth hiding.
    """
    if not text:
        return text
    words = text.split()
    out: list[str] = []
    index = 0
    while index < len(words):
        # Two-word terms first, so 'kali mirch' does not become 'black' alone.
        pair = _key(" ".join(words[index:index + 2])) if index + 1 < len(words) else ""
        if pair in COMMODITIES:
            out.append(COMMODITIES[pair])
            index += 2
            continue
        single = _key(words[index])
        if single in COMMODITIES:
            out.append(COMMODITIES[single])
        elif single in QUALIFIERS:
            out.append(QUALIFIERS[single])
        else:
            out.append(words[index])
        index += 1
    return " ".join(out).strip() or None


def is_known(text: str | None) -> bool:
    """Whether anything in this phrase is trade vocabulary we can translate."""
    if not text:
        return False
    return any(_key(w) in COMMODITIES or _key(w) in QUALIFIERS for w in text.split())
