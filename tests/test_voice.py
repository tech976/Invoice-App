"""Reading a spoken trade in Hindi, Marathi or English.

These are the three languages a deal is actually struck in on this desk, and
the test set is one trade said many ways: Devanagari and romanised, with the
markers and without them, with a greeting round it and without.

Nothing here needs a database or a model. The figures are read by the numeral
tables, which is deliberate — the language model was measured at nought out of
twenty on Indian numerals ('तैंतीस' came back as 115, 'दो सौ' as 2909), so a
rate is never taken from it. What these tests protect is the path that is
exact.
"""
from __future__ import annotations

import pytest

from app.voice import english, snap, translate
from app.voice.numerals import numbers_in
from app.voice.parse import parse_trade


def read(sentence: str) -> dict:
    """The fields a sentence yields, as plain values."""
    parsed = parse_trade(sentence).as_dict()
    return {
        name: (parsed[name] or {}).get("value")
        for name in ("seller", "buyer", "goods", "quantity", "uom", "rate")
    }


# --------------------------------------------------------------------------
# Numerals
# --------------------------------------------------------------------------


@pytest.mark.parametrize("said, expected", [
    # English, including the digit-wise way a rate is dictated.
    ("thirty three", 33),
    ("one thousand twenty", 1020),
    ("eight thirteen", 813),
    # Hindi, romanised and in Devanagari.
    ("taintis", 33),
    ("aath sau tera", 813),
    ("तैंतीस", 33),
    ("बारह सौ पचास", 1250),
    ("सात सौ तिरानवे", 793),
    # The nukta is optional in practice and must not change the reading.
    ("एक हज़ार बीस", 1020),
    ("एक हजार बीस", 1020),
    # Marathi contracts its hundreds onto 'शे' and writes them as one word.
    ("तीनशे", 300),
    ("दोनशे", 200),
    ("नऊ हजार चारशे", 9400),
    ("बारा शे पन्नास", 1250),
])
def test_numbers_are_read_exactly(said, expected):
    found = numbers_in(said)
    assert found, f"no number found in {said!r}"
    assert found[0][2] == expected


# --------------------------------------------------------------------------
# Whole sentences
# --------------------------------------------------------------------------

# One trade — Ashapura sells Shaan 33 bags of cashew at 1250 — said six ways.
SAME_TRADE = [
    "Ashapura to Shaan, thirty three bags cashew at twelve fifty",
    "Ashapura se Shaan ko taintis bori kaju bara sau pachas mein",
    "अशापुरा से शान को तैंतीस बोरी काजू बारह सौ पचास में",
    "अशापुरा कडून शान ला तेहतीस पोती काजू बारा शे पन्नास दराने",
    "Ashapura kadun Shaan la tehtis poti kaju bara she pannas darane",
    # A broker does not dictate cleanly; he talks around the trade.
    "okay so listen, write this down — Ashapura to Shaan, thirty three bags "
    "of cashew, rate twelve fifty, theek hai",
]


@pytest.mark.parametrize("sentence", SAME_TRADE)
def test_one_trade_said_many_ways(sentence):
    """However it is said, the figures and the goods come out the same."""
    got = read(sentence)
    assert got["goods"] == "Cashew"
    assert got["quantity"] == 33
    assert got["uom"] == "BAGS"
    assert got["rate"] == 1250


@pytest.mark.parametrize("sentence, quantity, uom, rate, goods", [
    ("सनमार्ग कडून श्रीनाथ ला दोनशे क्विंटल हळद नऊ हजार चारशे दराने",
     200, "QTL", 9400, "Turmeric"),
    ("माइक्रोन कडून विराट ला तीनशे किलो बादाम सात शे तिरानवे दराने",
     300, "KGS", 793, "Almond"),
    ("माइक्रोन से विराट को एक हज़ार बीस किलो बादाम सात सौ तिरानवे में",
     1020, "KGS", 793, "Almond"),
    ("C31 se V07 ko pachas bori akhrot aath sau tera mein",
     50, "BAGS", 813, "Walnut"),
])
def test_marathi_and_hindi_trades(sentence, quantity, uom, rate, goods):
    got = read(sentence)
    assert (got["quantity"], got["uom"], got["rate"]) == (quantity, uom, rate)
    assert got["goods"] == goods


def test_a_code_survives_dictation():
    """Codes are the one thing a broker can say that transcribes reliably."""
    got = read("C31 to V07, fifty bags walnut at eight thirteen")
    assert got["seller"] == "C31"
    assert got["buyer"] == "V07"


# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------

BOOK = ["Ashapura", "Shaan", "Micron", "Virat", "Sanmargg", "Shrinath"]


@pytest.mark.parametrize("heard, expected", [
    # Real mishearings, taken from the sample recordings.
    ("A chapure", "Ashapura"),
    ("Sean", "Shaan"),
    ("V-Rot", "Virat"),
    # And the spellings transliteration produces, which are phonetically right
    # but are not how the firm writes itself.
    ("Shan", "Shaan"),
    ("Maikron", "Micron"),
    ("Sanamarg", "Sanmargg"),
])
def test_a_misheard_name_snaps_to_the_one_already_booked(heard, expected):
    value, snapped_from = snap.snap(heard, BOOK)
    assert value == expected
    assert snapped_from == heard


def test_a_name_never_booked_is_left_alone():
    """Snapping must never invent a client the broker did not name."""
    assert snap.snap("Zephyrix", BOOK) == ("Zephyrix", None)


# --------------------------------------------------------------------------
# Saying it back in English
# --------------------------------------------------------------------------


def parsed_of(sentence: str) -> dict:
    return parse_trade(sentence).as_dict()


def test_the_trade_reads_back_in_english():
    said = "अशापुरा से शान को तैंतीस बोरी काजू बारह सौ पचास में"
    line = english.sentence(parsed_of(said))
    # Devanagari in, English out, with the money worked out.
    assert "33 bags" in line
    assert "Cashew" in line
    assert "1,250" in line
    assert "41,250" in line


def test_english_uses_indian_grouping():
    """8,08,860 — not 808,860. It is compared against bills written that way."""
    said = "Micron to Virat, one thousand twenty kilograms almond at seven ninety three"
    assert "8,08,860" in english.sentence(parsed_of(said))


def test_a_half_heard_sentence_looks_half_heard():
    """Nothing is invented to round the sentence off."""
    line = english.sentence(parsed_of("thirty three bags cashew"))
    assert "33 bags" in line and "Cashew" in line
    # No rate was said, so no rate and no total may appear.
    assert "at" not in line and "—" not in line


def test_nothing_read_says_nothing():
    assert english.sentence(parsed_of("")) is None


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------


@pytest.mark.parametrize("said, expected", [
    ("kaju", "Cashew"), ("काजू", "Cashew"),
    ("akhrot", "Walnut"), ("अखरोट", "Walnut"),
    ("हळद", "Turmeric"), ("haldi", "Turmeric"),
    ("badam", "Almond"), ("बादाम", "Almond"),
])
def test_goods_are_filed_in_english(said, expected):
    assert translate.term(said) == expected


def test_an_unknown_word_is_left_as_it_was_said():
    """More likely a name or a commodity not listed than a mistake to hide."""
    assert translate.term("Zephyrix") == "Zephyrix"
