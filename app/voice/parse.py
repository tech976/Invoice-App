"""Turning one spoken sentence into the columns of a trade.

Nothing is looked up. The broker says 'C31 se V07 ko pachas bori walnut aath
sau tera mein' and that is recorded as it was said — the codes as text, the
goods as text, the numbers as numbers. There is no client list to maintain and
no matching against anything: he knows who C31 is, and the book is his.

What the parser does is decide which part of the sentence is which, and that
is carried by tiny words. 'se' and 'from' mark the seller, 'ko' and 'to' mark
the buyer, a unit after a number makes it a quantity, and what is left over is
the goods.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.voice.numerals import normalise_digits, numbers_in

# Units of trade, mapped to what the book stores.
UNITS: dict[str, str] = {
    "bori": "BAGS", "bora": "BAGS", "boris": "BAGS", "बोरी": "BAGS", "બોરી": "BAGS",
    "bag": "BAGS", "bags": "BAGS", "थैला": "BAGS", "katta": "BAGS", "कट्टा": "BAGS",
    "kg": "KGS", "kgs": "KGS", "kilo": "KGS", "kilos": "KGS", "kilogram": "KGS",
    "किलो": "KGS", "કિલો": "KGS",
    "quintal": "QTL", "quintals": "QTL", "qtl": "QTL", "क्विंटल": "QTL",
    "ton": "MT", "tons": "MT", "tonne": "MT", "mt": "MT", "टन": "MT",
    "peti": "BOX", "petti": "BOX", "box": "BOX", "boxes": "BOX", "carton": "BOX",
    "पेटी": "BOX", "પેટી": "BOX",
    "pcs": "PCS", "piece": "PCS", "pieces": "PCS", "nag": "PCS", "नग": "PCS",
}

# Words that mark the number beside them as a price.
RATE_CUES = {
    "mein", "me", "rate", "bhav", "bhaav", "rupees", "rupee", "rs", "rs.",
    "per", "@", "ke", "ka", "ki", "price", "at", "भाव", "में", "रुपये",
    "ભાવ", "માં", "રૂપિયા", "prati", "प्रति", "each",
}

# Which side of the deal a name is on. Indian languages mark it after the
# name, English before it, and the two are never read from the same side —
# in 'from X to Y' the word following X is 'to'.
TRAILING_SELLER = {"se", "kadun", "kadoon", "pasethi", "से", "कडून", "પાસેથી"}
TRAILING_BUYER = {"ko", "को"}
LEADING_SELLER = {"from", "by"}
LEADING_BUYER = {"to"}

# 'ne' means 'to' in Gujarati but marks the seller in Hindi and Marathi. A
# 'ko' elsewhere in the sentence settles which language is being spoken.
AMBIGUOUS_NE = {"ne", "ने", "ને"}

ALL_CUES = (TRAILING_SELLER | TRAILING_BUYER | LEADING_SELLER | LEADING_BUYER
            | AMBIGUOUS_NE | RATE_CUES | {"sold", "bought", "becha", "liya", "diya"})

# Speech is not dictation. A broker booking a sauda says a good deal that is
# not part of the trade — he greets, he tells you to write it down, he
# confirms at the end — and none of it belongs in a column. These are the
# words that carry no trade meaning in any of the languages he mixes.
#
# Nothing here can be a party, a commodity or a number, so removing them is
# safe. A name that happens to contain one — 'Nileshbhai', 'Rameshji' — is a
# single word and is never touched.
FILLERS = {
    # Hindi / Marathi / Gujarati conversation
    "haan", "han", "ha", "haa", "ji", "achha", "accha", "acha", "theek",
    "thik", "hai", "he", "tha", "thi", "bas", "matlab", "yaar", "arre",
    "are", "suno", "sun", "sunno", "dekho", "dekh", "bol", "bolo", "boliye",
    "likh", "likho", "lo", "karo", "kar", "kro", "note", "entry", "kya",
    "na", "nahi", "abhi", "chalo", "sahi", "badhiya", "chalega", "daal",
    "daalo", "bhai", "sir", "madam", "aur", "phir", "fir", "toh", "wo",
    "woh", "ye", "yeh", "ekdum", "zara", "jara",
    "हाँ", "हां", "जी", "ठीक", "है", "बस", "अरे", "सुनो", "देखो", "लिखो",
    "करो", "नोट", "अभी", "चलो", "भाई", "और", "फिर",
    "હા", "જી", "બરાબર", "છે", "સાંભળો", "લખો", "કરો", "ભાઈ",
    # English
    "okay", "ok", "so", "um", "uh", "er", "erm", "hmm", "well", "right",
    "yeah", "yes", "no", "please", "thanks", "thank", "done", "write",
    "put", "record", "book", "add", "make", "just", "actually", "basically",
    "like", "now", "then", "and", "the", "a", "an", "of", "is", "it",
    "listen", "this", "that", "here", "there", "down", "up",
    "let", "lets", "also", "one", "second", "minute", "wait",
}

# Two-word habits that only make sense together.
FILLER_PHRASES = (
    ("theek", "hai"), ("thik", "hai"), ("note", "karo"), ("likh", "lo"),
    ("likh", "do"), ("kar", "do"), ("daal", "do"), ("note", "kar"),
    ("ek", "minute"), ("hold", "on"), ("write", "it"), ("put", "it"),
    ("ठीक", "है"), ("नोट", "करो"), ("लिख", "लो"),
)

# A client code as it is spoken: a letter or two then a number, however it
# arrives — 'C31', 'C 31', 'C-31'.
CODE_RE = re.compile(r"^[A-Za-z]{1,3}[-\s]?\d{1,4}$")


@dataclass
class Guess:
    """A value and the words it came from."""

    value: Any
    text: str = ""
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return {"value": self.value, "text": self.text,
                "confidence": round(self.confidence, 2)}


@dataclass
class WordUse:
    """What became of one spoken word."""

    word: str
    role: str          # seller | buyer | goods | quantity | uom | rate | filler | marker | ignored

    def as_dict(self) -> dict:
        return {"word": self.word, "role": self.role}


@dataclass
class ParsedTrade:
    transcript: str = ""
    #: Word positions the quantity and rate were read from. They mark where
    #: the figures sit in the sentence, which is what separates the parties
    #: before them from the goods after.
    figure_span: tuple[int, int] | None = None
    seller: Guess | None = None
    buyer: Guess | None = None
    goods: Guess | None = None
    quantity: Guess | None = None
    uom: Guess | None = None
    rate: Guess | None = None
    leftover: list[str] = field(default_factory=list)
    words: list[WordUse] = field(default_factory=list)

    def as_dict(self) -> dict:
        out = {"transcript": self.transcript, "leftover": " ".join(self.leftover),
               "words": [w.as_dict() for w in self.words]}
        for name in ("seller", "buyer", "goods", "quantity", "uom", "rate"):
            guess = getattr(self, name)
            out[name] = guess.as_dict() if guess else None
        return out


def _word(token: str) -> str:
    # Currency marks are stripped too: a recogniser writing 'twelve fifty' as
    # '$12.50' otherwise hides the rate behind a symbol.
    return token.strip(".,:;!?₹$£€'\"").lower()


def _is_code(token: str) -> bool:
    return bool(CODE_RE.match(token.strip(".,:;!?")))


def tidy_code(text: str) -> str:
    """'C-31' and 'c 31' are filed as 'C31'.

    Only codes are touched, so a name keeps whatever capitalisation and
    spacing it was said with.
    """
    stripped = text.strip(".,:;!?")
    if _is_code(stripped):
        return re.sub(r"[-\s]", "", stripped).upper()
    return text.strip(".,")


def _is_plain_word(token: str) -> bool:
    """A word that could name a party or a commodity."""
    word = _word(token)
    if not word or word in ALL_CUES or word in UNITS:
        return False
    # Punctuation the recogniser left behind is not a word.
    if not any(ch.isalnum() for ch in word):
        return False
    if any(ch.isdigit() for ch in word):
        return False
    from app.voice.numerals import _word_value
    return _word_value(word) is None


_CODE_HEAD_RE = re.compile(r"^[A-Za-z]{1,3}$")
_CODE_TAIL_RE = re.compile(r"^\d{1,4}[.,]?$")
_CODE_PAIR_RE = re.compile(r"^([A-Za-z]{1,3}\d{1,4})[-/]([A-Za-z]{1,3}\d{1,4})$")


def _join_codes(words: list[str]) -> list[str]:
    """'C 31' is one code, however the engine spaced it.

    Done before anything else, because left apart the letter looks like a
    name and the digits get read as part of the quantity beside them.
    """
    from app.voice.numerals import _word_value

    def could_head(token: str) -> bool:
        # 'ko', 'se', 'at' and 'do' are all short enough to look like the
        # letter part of a code. Joining one to the number after it would
        # swallow both the grammar and the quantity.
        word = _word(token)
        if not _CODE_HEAD_RE.match(token):
            return False
        return (word not in ALL_CUES and word not in UNITS
                and _word_value(word) is None)

    # Two codes said back to back come through joined: 'C31-V07'. Split
    # before anything else, or the pair reads as a single unknown word and
    # both parties are lost.
    split: list[str] = []
    for token in words:
        pair = _CODE_PAIR_RE.match(token.strip(".,"))
        if pair:
            split.extend([pair.group(1), pair.group(2)])
        else:
            split.append(token)

    out: list[str] = []
    i = 0
    while i < len(split):
        if (i + 1 < len(split) and could_head(split[i])
                and _CODE_TAIL_RE.match(split[i + 1])):
            out.append(split[i] + split[i + 1].rstrip(".,"))
            i += 2
            continue
        out.append(split[i])
        i += 1
    return out


def _strip_fillers(words: list[str]) -> list[str]:
    """Drop the words that are conversation rather than trade.

    Done before anything is placed, so 'walnut theek hai' cannot reach the
    goods column and 'arre suno, Micron' cannot reach the seller.
    """
    out: list[str] = []
    i = 0
    while i < len(words):
        if i + 1 < len(words):
            pair = (_word(words[i]), _word(words[i + 1]))
            if pair in FILLER_PHRASES:
                i += 2
                continue
        word = _word(words[i])
        # A number word is never filler, whatever else it looks like.
        from app.voice.numerals import _word_value
        if word in FILLERS and _word_value(word) is None:
            i += 1
            continue
        out.append(words[i])
        i += 1
    return out


def parse_trade(transcript: str) -> ParsedTrade:
    """Read one spoken sentence into the columns of a trade."""
    from app.voice.numerals import split_hyphens

    text = re.sub(r"\s+", " ", split_hyphens(normalise_digits(transcript))).strip()
    words = _strip_fillers(_join_codes(text.split()))
    result = ParsedTrade(transcript=transcript)
    used: set[int] = set()

    _read_parties(words, result, used)
    _read_amounts(words, result, used)
    _fill_remaining(words, result, used)

    result.leftover = [w for i, w in enumerate(words)
                       if i not in used and _word(w) not in ALL_CUES]
    result.words = _explain(transcript, words, result)
    return result


def _explain(transcript: str, kept: list[str], result: ParsedTrade) -> list[WordUse]:
    """Say what happened to every word the broker actually said.

    Guesswork is only checkable if it is visible. This walks the original
    sentence and labels each word with the column it went to, or says it was
    treated as filler, as grammar, or as nothing at all — so a wrong reading
    can be pointed at rather than described.
    """
    claimed: dict[str, str] = {}
    for role in ("seller", "buyer", "goods", "quantity", "uom", "rate"):
        guess = getattr(result, role)
        if guess and guess.text:
            for token in str(guess.text).split():
                claimed.setdefault(_word(token), role)

    out: list[WordUse] = []
    kept_words = {_word(k) for k in kept}
    for token in re.sub(r"\s+", " ", normalise_digits(transcript)).strip().split():
        word = _word(token)
        if not word:
            continue
        if word in claimed:
            out.append(WordUse(token, claimed[word]))
        elif word in ALL_CUES:
            out.append(WordUse(token, "marker"))
        elif word not in kept_words:
            out.append(WordUse(token, "filler"))
        else:
            out.append(WordUse(token, "ignored"))
    return out


def _ends_clause(token: str) -> bool:
    """Does this word carry a break in the sentence?"""
    return token.strip().endswith((",", ".", ";", "!", "?"))


def _party_span(words: list[str], index: int, forward: bool) -> tuple[int, int] | None:
    """The run of words naming a party, either side of a marker.

    A code stands alone — 'C31 se' is one token. A spoken name may be two or
    three words, so the run extends until something that cannot be part of a
    name.
    """
    step = 1 if forward else -1
    positions: list[int] = []
    i = index + step
    while 0 <= i < len(words) and len(positions) < 3:
        token = words[i]
        if _is_code(token):
            positions.append(i)
            break
        if not _is_plain_word(token):
            break
        positions.append(i)
        # A comma ends the clause. Reading back from 'to' in 'note it down,
        # Micron to Virat' the name is 'Micron' and stops there — without
        # this the rest of the sentence is dragged in with it.
        if _ends_clause(token) if forward else _ends_clause(words[i - 1] if i else ""):
            break
        i += step
    if not positions:
        return None
    return min(positions), max(positions)


def _position_of(words: list[str], guess: Guess | None) -> int:
    """Where in the sentence a placed party was said."""
    if guess is None:
        return len(words)
    head = guess.text.split()[0] if guess.text.split() else ""
    for i, token in enumerate(words):
        if token.strip(".,") == head:
            return i
    return len(words)


def _read_parties(words: list[str], result: ParsedTrade, used: set[int]) -> None:
    """Place each party by the marker beside it."""
    lowered = {_word(w) for w in words}
    hindi_ne = bool(lowered & TRAILING_BUYER)

    for i, token in enumerate(words):
        word = _word(token)
        role = span = None

        if word in TRAILING_SELLER or (word in AMBIGUOUS_NE and hindi_ne):
            role, span = "seller", _party_span(words, i, forward=False)
        elif word in TRAILING_BUYER or (word in AMBIGUOUS_NE and not hindi_ne):
            role, span = "buyer", _party_span(words, i, forward=False)
        elif word in LEADING_SELLER:
            role, span = "seller", _party_span(words, i, forward=True)
        elif word in LEADING_BUYER:
            role, span = "buyer", _party_span(words, i, forward=True)

        if role and span and getattr(result, role) is None:
            start, end = span
            said = tidy_code(" ".join(words[start:end + 1]))
            setattr(result, role, Guess(said, said, 0.9))
            used.update(range(start, end + 1))
            used.add(i)

    # Codes left over after the markers have been read. A sentence may mark
    # only one side — 'C31 to V07' names the buyer and leaves the seller to be
    # inferred from position — and one with no markers at all leaves both.
    spare = [i for i, t in enumerate(words) if _is_code(t) and i not in used]
    for i in spare:
        if result.seller is None and (
            result.buyer is None or i < _position_of(words, result.buyer)
        ):
            role = "seller"
        elif result.buyer is None:
            role = "buyer"
        else:
            break
        said = tidy_code(words[i])
        setattr(result, role, Guess(said, said, 0.5))
        used.add(i)


def _runs(words: list[str], used: set[int]) -> list[list[int]]:
    """Unclaimed plain words, grouped into the stretches they were said in."""
    runs: list[list[int]] = []
    current: list[int] = []
    for i, token in enumerate(words):
        if i not in used and _is_plain_word(token) and not _is_code(token):
            current.append(i)
            # A comma ends the stretch. 'note it down, micron' is two things
            # said, not one four-word name.
            if _ends_clause(token):
                runs.append(current)
                current = []
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _split_evenly(run: list[int], parts: int) -> list[list[int]]:
    """Divide one stretch of words between the columns it has to cover.

    'Nilesh Ganpule Virat Agro' with nothing between the two names has to
    become a seller and a buyer, and there is no signal to say where the join
    is — so it is split down the middle, with the odd word going to the first.
    A guess distributed across the right columns is worth more than a correct
    guess dumped into one, because the broker can see it and move a word.
    """
    if parts <= 1:
        return [run]
    size, spare = divmod(len(run), parts)
    out, cursor = [], 0
    for i in range(parts):
        take = size + (1 if i < spare else 0)
        out.append(run[cursor:cursor + take])
        cursor += take
    return [chunk for chunk in out if chunk]


# A party is named in a word or three, never five. Anything longer is two
# parties that ran together.
MAX_PARTY_WORDS = 3


def _fill_remaining(words: list[str], result: ParsedTrade, used: set[int]) -> None:
    """Spread whatever is left over the columns still empty.

    Markers do not always survive — a recogniser can swallow the 'to' in
    'Micron to Virat', and a broker in a hurry may not say one at all. What is
    left is order, and order is enough: a trade is spoken as parties, then how
    much, then what. So words before the first figure can only be parties and
    words after it can only be the goods, and each is handed to its own column
    rather than piled into the first one that happens to be empty.
    """
    # Where the figures were spoken. Taken from what was actually read as a
    # quantity or a rate, because 'one thousand twenty' carries no digit at
    # all and looking for one puts the whole sentence on the wrong side.
    pivot = result.figure_span[0] if result.figure_span else len(words)
    runs = _runs(words, used)
    before = [r for r in runs if r[-1] < pivot]
    after = [r for r in runs if r[-1] >= pivot]

    party_slots = [name for name in ("seller", "buyer")
                   if getattr(result, name) is None]
    if party_slots and before:
        # Work backwards from the figures and stop as soon as there are
        # enough words to fill the columns. The trade sits in the middle of
        # the sentence — the greeting and the 'write this down' are at its
        # edges — and taking one stretch too many drags them in.
        chosen: list[list[int]] = []
        for run in reversed(before):
            chosen.insert(0, run)
            if sum(len(r) for r in chosen) >= len(party_slots):
                break
        _hand_out(words, result, used, chosen, party_slots, MAX_PARTY_WORDS)

    if result.goods is None:
        # The goods are normally named after the quantity. Only if nothing
        # follows it does a leftover from before the figure describe them.
        source = after or [r for r in _runs(words, used) if r]
        if source:
            # Only the first stretch. Whatever else trails the rate is the
            # broker finishing his sentence, not a second commodity.
            run = source[0]
            said = " ".join(words[i] for i in run)
            said = " ".join(said.split()).strip(".,")
            if said:
                result.goods = Guess(said, said, 0.6)
                used.update(run)


def _hand_out(words: list[str], result: ParsedTrade, used: set[int],
              runs: list[list[int]], slots: list[str], max_words: int) -> None:
    """Give each stretch of words a column, splitting one if it must cover two."""
    shortfall = len(slots) - len(runs)
    plan: list[int] = []
    for run in runs:
        take = 1
        if shortfall > 0 and len(run) > 1:
            take = min(shortfall + 1, len(run))
            shortfall -= take - 1
        plan.append(take)

    cursor = 0
    for run, take in zip(runs, plan):
        if cursor >= len(slots):
            break
        for chunk, slot in zip(_split_evenly(run, take), slots[cursor:cursor + take]):
            chunk = chunk[:max_words]
            said = tidy_code(" ".join(words[i] for i in chunk))
            if not said:
                continue
            # Placed by position alone, so the confidence says as much.
            setattr(result, slot, Guess(said, said, 0.4 if take > 1 else 0.55))
            used.update(chunk)
        cursor += take


# A unit only has to be recognised where a unit can be: straight after a
# number. That makes near-misses safe to accept — 'boree', 'bodi' and
# 'kwintal' can be nothing else when they follow fifty.
#
# The threshold is measured rather than guessed. Every commodity a broker
# deals in scores at most 54 against this list, while the misheard units start
# at 67, so 62 sits in the gap: mishearings are caught and no goods word is
# ever mistaken for a unit.
UNIT_THRESHOLD = 62


def _unit_after(token: str) -> str | None:
    """The unit this word is, allowing for how it may have been heard."""
    word = _word(token)
    if not word:
        return None
    if word in UNITS:
        return UNITS[word]
    if len(word) < 3 or any(ch.isdigit() for ch in word):
        return None
    from rapidfuzz import fuzz, process

    hit = process.extractOne(word, UNITS.keys(), scorer=fuzz.ratio,
                             score_cutoff=UNIT_THRESHOLD)
    return UNITS[hit[0]] if hit else None


# 'eight thirteen' and 'twelve fifty' are how a rate is dictated, and a
# recogniser writes them as 8.13 and 12.50 — a decimal point that was never
# said. One or two digits, a point, then exactly two more is that mistake and
# almost never a real price: bulk trade is quoted in whole rupees, not paise.
_DICTATED_RATE_RE = re.compile(r"^(\d{1,2})\.(\d{2})$")


def _rejoin_dictated_rate(said: str, value: float) -> tuple[float, float]:
    """Undo the decimal point a recogniser invented, at reduced confidence."""
    match = _DICTATED_RATE_RE.match(said.strip(".,:;!?₹$£€"))
    if not match:
        return value, 0.85
    joined = float(match.group(1) + match.group(2))
    # Flagged low so the screen shows it was interpreted, with the words it
    # came from beside it.
    return joined, 0.5


def _read_amounts(words: list[str], result: ParsedTrade, used: set[int]) -> None:
    """Decide which number is the quantity and which is the price.

    A unit word beside a number settles it. What remains is the rate, because
    a trade carries only these two numbers.
    """
    masked = [("." if i in used else w) for i, w in enumerate(words)]
    numbers = [n for n in numbers_in(" ".join(masked))
               if not any(i in used for i in range(n[0], n[1] + 1))]
    quantity = rate = None

    for start, end, value in numbers:
        following = _unit_after(words[end + 1]) if end + 1 < len(words) else None
        if following and quantity is None:
            quantity = (start, end, value, following)

    for start, end, value in numbers:
        if quantity and (start, end) == (quantity[0], quantity[1]):
            continue
        before = _word(words[start - 1]) if start > 0 else ""
        after = _word(words[end + 1]) if end + 1 < len(words) else ""
        if rate is None and (before in RATE_CUES or after in RATE_CUES):
            rate = (start, end, value)

    remaining = [n for n in numbers
                 if not (quantity and (n[0], n[1]) == (quantity[0], quantity[1]))
                 and not (rate and (n[0], n[1]) == (rate[0], rate[1]))]
    if quantity is None and remaining:
        quantity = (*remaining.pop(0), "")
    if rate is None and remaining:
        rate = remaining.pop(0)

    marks = [n[0] for n in (quantity, rate) if n]
    if marks:
        result.figure_span = (min(marks), max(marks))

    if quantity:
        start, end, value, unit = quantity
        result.quantity = Guess(value, " ".join(words[start:end + 1]), 0.9 if unit else 0.6)
        used.update(range(start, end + 1))
        if unit:
            result.uom = Guess(unit, words[end + 1], 0.95)
            used.add(end + 1)
    if rate:
        start, end, value = rate
        said = " ".join(words[start:end + 1])
        value, confidence = _rejoin_dictated_rate(said, value)
        result.rate = Guess(value, said, confidence)
        used.update(range(start, end + 1))
