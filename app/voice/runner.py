"""Keeping CPU-bound inference off the event loop, and off each other's toes.

Two things go wrong if this is skipped.

Whisper and Qwen are blocking C calls. Run either one inside an async handler
and the whole server stops answering for its duration — no page loads, no
health check, nothing — because the event loop cannot run while a coroutine
refuses to yield.

Handing them to a thread fixes that, but only until two recordings arrive at
once. Each model is already using several cores; two at once on a machine with
eight means both crawl and neither finishes sooner. So each stage gets a
single worker and a queue behind it. A second request waits, which is slower
for that request and faster for everybody, and leaves the server responsive
throughout.

The two stages have separate workers on purpose: one recording can be
transcribed while the previous one is being read, which is where the only free
parallelism in this pipeline lives.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# One worker each, named so a stuck thread is identifiable in a stack dump.
_ASR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")
_NLP = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nlp")

_POOLS = {"asr": _ASR, "nlp": _NLP}


async def run(stage: str, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run one blocking inference on its stage's worker.

    Awaiting this yields the event loop, so the server keeps answering while
    the model works.
    """
    pool = _POOLS[stage]
    loop = asyncio.get_running_loop()
    if kwargs:
        from functools import partial

        func = partial(func, **kwargs)
    return await loop.run_in_executor(pool, func, *args)


def depth(stage: str) -> int:
    """How many recordings are waiting on this stage.

    Worth showing on the page: a broker who can see he is third in the queue
    does not press the button again.
    """
    queue = getattr(_POOLS[stage], "_work_queue", None)
    return queue.qsize() if queue is not None else 0


def shutdown() -> None:
    for pool in _POOLS.values():
        pool.shutdown(wait=False, cancel_futures=True)
