"""Published per-million-token prices, for cost reporting.

Kept in one place and clearly dated, because it is the kind of table that
silently goes stale. Verify against https://claude.com/pricing before relying
on the numbers for a budget.
"""
from __future__ import annotations

# (input $/MTok, output $/MTok) — checked 2026-08-21
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}

BATCH_DISCOUNT = 0.5


def cost_usd(model: str, input_tokens: int, output_tokens: int, *, batch: bool = False) -> float | None:
    """Cost of one request, or None for a model not in the table."""
    price = PRICES.get(model)
    if price is None:
        return None
    rate = BATCH_DISCOUNT if batch else 1.0
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000 * rate
