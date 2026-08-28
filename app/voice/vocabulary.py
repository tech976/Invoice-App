"""The words a mandi trade is made of.

Not a list of anyone's clients and nothing to do with the invoice ledger —
just the vocabulary of the trade itself: the units goods are counted in, the
commodities dealt in, and the little words that carry who sold to whom.

A recogniser given these in advance is markedly better at them. Whisper takes
an `initial_prompt` in the style of the expected speech and a `hotwords` list
it will lean toward, and both are decoding hints rather than a lookup: a word
outside this list is still transcribed, just without the help.
"""
from __future__ import annotations

UNITS = (
    "bori", "bora", "katta", "bag", "bags", "kg", "kilo", "quintal",
    "peti", "petti", "carton", "nag", "ton",
)

COMMODITIES = (
    "walnut", "walnuts", "almond", "almonds", "cashew", "cashews", "raisin",
    "raisins", "pistachio", "fig", "date", "dates", "cardamom", "pepper",
    "turmeric", "cumin", "coriander", "fenugreek", "fennel", "kidney bean",
    "chickpea", "gram", "lentil", "jaggery", "sago", "betel nut", "sesame",
    "groundnut", "peanut", "saffron", "copra", "inshell", "kernel",
)

# The grammar of a deal: who sold, who bought, at what.
PARTICLES = ("to", "from", "sold", "bought", "rate", "at", "per", "each")

# The numbers that decide a price. Only the English words: a recogniser told
# to expect 'pachas' will find it in a noisy clip that said 'cashew', and a
# hallucinated quantity is worse than a missing one.
NUMBER_WORDS = (
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fifteen", "twenty", "thirty",
    "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
    "thousand",
)

# Codes are letter-then-number, and are the most reliable thing a broker can
# say. Showing the pattern in the prompt helps them come back intact.
CODE_EXAMPLES = ("C31", "V07", "K44", "L09", "N12", "S25")


# Whisper conditions on at most 224 tokens, counting the prompt and the
# hotwords together. Going over does not raise anything — it returns a few
# mangled characters and stops — so the budget is enforced here, and the
# example sentences get the larger share because they teach the shape of a
# sauda as well as its words.
TOKEN_BUDGET = 200
PROMPT_SHARE = 110


def _fit(terms: tuple[str, ...], budget_chars: int) -> str:
    """As many terms as fit, in the order given (most useful first)."""
    kept, used = [], 0
    for term in terms:
        cost = len(term) + 1
        if used + cost > budget_chars:
            break
        kept.append(term)
        used += cost
    return " ".join(kept)


# The same vocabulary as it is actually said in a mandi. Kept apart from the
# English set on purpose: given to a decoder listening to English these words
# cause it to hear 'pachas' where 'cashew' was said, and withheld from one
# listening to Hindi it produces nothing usable at all. Which set is used is
# decided per recording, once the language is known.
# Romanised throughout, and deliberately so. Whisper writes back in whatever
# script it was shown: give it Devanagari and it returns Devanagari, whose
# spelling wobbles — 'सी' for 'से', 'बारे सो' for 'बारह सौ' — and every wobble
# breaks a lookup. Shown the same words in Latin it returns Latin, and more
# often than not writes the numbers as digits, which cannot wobble at all.
INDIC_LANGUAGES = frozenset({"hi", "mr", "gu", "sa", "ne", "bn", "pa"})

INDIC_UNITS = ("bori", "bora", "poti", "katta", "goni", "peti", "nag", "kilo",
               "quintal", "bag", "bags")
INDIC_COMMODITIES = ("akhrot", "badam", "kaju", "kishmish", "pista", "anjeer",
                     "khajur", "elaichi", "haldi", "jeera", "chana", "gud")
INDIC_PARTICLES = ("se", "ko", "ne", "la", "kadun", "bhav", "mein", "dar",
                   "darane", "rate")
INDIC_NUMBERS = ("ek", "do", "teen", "char", "panch", "saat", "aath", "nau",
                 "das", "bara", "tera", "bees", "tees", "chalis", "pachas",
                 "pannas", "saath", "sattar", "assi", "nabbe", "sau", "she",
                 "hazaar", "taintis", "tehtis", "tiranave", "pachanave",
                 "ikyanave", "chaurasi", "bavan", "sattavan")


def hotwords(language: str | None = None) -> str:
    """Terms the decoder should lean toward, within its token budget.

    Ordered by what a wrong reading costs. Numbers first — a misheard rate is
    money — then units, then the commodities and particles.
    """
    if language in INDIC_LANGUAGES:
        ordered = (INDIC_NUMBERS + INDIC_UNITS + INDIC_PARTICLES
                   + INDIC_COMMODITIES + CODE_EXAMPLES)
    else:
        ordered = NUMBER_WORDS + UNITS + PARTICLES + COMMODITIES + CODE_EXAMPLES
    return _fit(ordered, 260)


def initial_prompt(learned: tuple[str, ...] = (),
                   language: str | None = None) -> str:
    """A sample in the style of the speech that follows.

    Whisper conditions on this as though it were the previous sentence, so it
    is written the way a broker actually books a sauda — mixed languages,
    codes, a quantity and a rate — rather than as a word list.
    """
    if language in INDIC_LANGUAGES:
        sample = (
            "C31 se V07 ko pachas bori akhrot aath sau tera bhav mein. "
            "K44 kadun L09 la teen sau kilo badam bara she pachas darane. "
            "N12 se S25 ko taintis peti kaju bara sau pachas mein."
        )
    else:
        sample = (
            "C31 to V07, fifty bags walnut at eight thirteen. "
            "K44 to L09, three hundred kg almond, rate twelve fifty. "
            "N12 to S25, thirty three boxes cashew at nine ninety."
        )
    if learned:
        # Names the book has seen are worth more than more examples, but only
        # what is left of the budget after the examples themselves.
        room = max(0, PROMPT_SHARE * 4 - len(sample))
        names = _fit(tuple(learned), room)
        if names:
            sample += " " + names + "."
    return sample


# Whisper conditions on at most a couple of hundred tokens, so the learned
# list is capped. The ones a broker says most often are the ones worth
# spending that budget on.
MAX_LEARNED = 40


def learned_terms(db) -> tuple[str, ...]:
    """Names and goods this book has already recorded, commonest first.

    The trade book teaches itself. Every name the broker confirms becomes a
    term the recogniser leans toward next time, so 'Ashapura' is heard better
    the tenth time than the first. It draws only on trades already booked —
    there is no list to maintain and nothing here comes from anywhere else.
    """
    from sqlalchemy import func, select

    from app.models import Trade

    seen: dict[str, int] = {}
    for column in (Trade.seller, Trade.buyer, Trade.goods):
        rows = db.execute(
            select(column, func.count())
            .where(column.is_not(None))
            .group_by(column)
        ).all()
        for value, count in rows:
            text = (value or "").strip()
            # A code needs no help; it already transcribes reliably.
            if len(text) < 3 or any(ch.isdigit() for ch in text):
                continue
            seen[text] = seen.get(text, 0) + count

    ranked = sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))
    return tuple(term for term, _ in ranked[:MAX_LEARNED])


def known_values(db, column_names=("seller", "buyer")) -> list[str]:
    """Distinct values this book already holds for the given columns.

    Used to snap a misheard name back to one already booked. Codes are left
    out: they transcribe reliably and an exact code should never be nudged
    toward a different one.
    """
    from sqlalchemy import select

    from app.models import Trade

    out: set[str] = set()
    for name in column_names:
        column = getattr(Trade, name)
        for (value,) in db.execute(select(column).where(column.is_not(None))).all():
            text = (value or "").strip()
            if len(text) >= 3 and not any(ch.isdigit() for ch in text):
                out.add(text)
    return sorted(out)
