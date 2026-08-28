"""Reading a spoken sentence with a language model, on the CPU, offline.

The rule-based parser beside this one is exact and instant, but it only knows
the words it was given: a Hindi sentence whose spelling wobbles slips past it.
A small instruction-tuned model reads meaning instead of matching strings, and
Qwen in particular was trained on Devanagari, so 'अशापुरा से शान को तैंतीस
बोरी काजू' is understood rather than looked up.

It runs through Ollama on this machine. Nothing is sent anywhere: Ollama binds
to localhost and the weights are a file on disk, fetched once at setup exactly
like the speech model.

Two things keep it honest. The reply is constrained to the JSON schema of
`SpokenTrade`, so the decoder cannot emit anything that is not a valid trade,
and every value is validated again by Pydantic afterwards. A model that cannot
answer returns nulls, which the review screen shows as blanks — never a guess.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import httpx

from app.config import settings
from app.voice.schema import SpokenTrade

log = logging.getLogger(__name__)


class ExtractionUnavailable(RuntimeError):
    """Ollama is not running, or the model has not been pulled."""


SYSTEM = (
    "You read one spoken sentence from an Indian commodity broker and return "
    "the trade in it as JSON.\n"
    "The sentence may be English, Hindi or Marathi, or a mixture.\n\n"
    "Rules:\n"
    "- Return the goods in English: kaju/काजू is Cashew, akhrot/अखरोट is "
    "Walnut, badam/बादाम is Almond, kishmish is Raisin.\n"
    "- Client codes such as C31 or V07 are copied exactly, never translated.\n"
    "- Names are written in Latin letters, never translated: अशापुरा is "
    "Ashapura.\n"
    "- 'se', 'kadun', 'from' mark the seller. 'ko', 'la', 'to' mark the buyer.\n"
    "- Units: bori, bora, katta, poti, bag are BAGS. kilo, kg are KGS. "
    "quintal is QTL. peti, box, carton are BOX.\n"
    "- Rates are said digit by digit: 'aath sau tera' and 'eight thirteen' "
    "are both 813. 'bara sau pachas' and 'twelve fifty' are both 1250.\n"
    "- If something was not said, return null for it. Never invent a value."
)

# One worked example. A single demonstration of the shape is worth more than
# another paragraph of instruction, and costs far fewer tokens.
EXAMPLE_IN = "Ashapura se Shaan ko taintis bori kaju bara sau pachas mein"
EXAMPLE_OUT = {
    "seller": "Ashapura", "buyer": "Shaan", "goods": "Cashew",
    "quantity": 33, "uom": "BAGS", "rate": 1250,
}


@dataclass
class Extraction:
    trade: SpokenTrade
    model: str
    duration_ms: int


def _endpoint(path: str) -> str:
    return f"{settings.ollama_url.rstrip('/')}{path}"


def available() -> bool:
    """Is Ollama up with the configured model pulled?"""
    try:
        reply = httpx.get(_endpoint("/api/tags"), timeout=2.0)
        reply.raise_for_status()
        names = {m.get("name", "") for m in reply.json().get("models", [])}
    except Exception:  # noqa: BLE001 - absence is a normal state
        return False
    wanted = settings.nlp_model
    return any(n == wanted or n.split(":")[0] == wanted.split(":")[0] for n in names)


def extract(transcript: str) -> Extraction:
    """Read one sentence into a trade.

    Blocking and CPU-bound — call it off the event loop.
    """
    started = time.time()
    payload = {
        "model": settings.nlp_model,
        "stream": False,
        # The schema is handed to the sampler, not just described in the
        # prompt: tokens that would break it are never generated, so there is
        # no malformed JSON to repair afterwards.
        "format": SpokenTrade.model_json_schema(),
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": EXAMPLE_IN},
            {"role": "assistant", "content": json.dumps(EXAMPLE_OUT)},
            {"role": "user", "content": transcript},
        ],
        # Held in memory between recordings. Without this Ollama unloads the
        # model after five minutes and the next broker pays two seconds of
        # load time before a word is read.
        "keep_alive": settings.nlp_keep_alive,
        "options": {
            # Deterministic. The same sentence must always book the same
            # trade, and there is nothing creative to be gained here.
            "temperature": 0.0,
            "top_p": 1.0,
            # The reply is one small object; without a ceiling a confused
            # model will happily fill the context and cost thirty seconds.
            "num_predict": settings.nlp_max_tokens,
            "num_ctx": 1024,
            "num_thread": settings.cpu_threads,
        },
    }
    try:
        reply = httpx.post(_endpoint("/api/chat"), json=payload,
                           timeout=settings.nlp_timeout)
        reply.raise_for_status()
    except httpx.HTTPError as exc:
        raise ExtractionUnavailable(
            f"Could not reach Ollama at {settings.ollama_url}: {exc}. "
            f"Start it with 'ollama serve' and pull the model with "
            f"'ollama pull {settings.nlp_model}'."
        ) from exc

    content = reply.json().get("message", {}).get("content", "")
    try:
        trade = SpokenTrade.model_validate_json(content)
    except Exception as exc:  # noqa: BLE001 - a bad reply is not a crash
        log.warning("model returned unusable JSON (%s): %r", exc, content[:200])
        trade = SpokenTrade()

    return Extraction(
        trade=trade,
        model=settings.nlp_model,
        duration_ms=int((time.time() - started) * 1000),
    )
