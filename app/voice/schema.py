"""The shape a spoken trade has to arrive in.

This model is handed to the language model as a JSON schema and used to
validate what comes back, so the two cannot drift apart. Every constraint here
is enforced twice: once by the decoder, which is only allowed to emit tokens
that keep the JSON valid, and once by Pydantic when the reply is parsed.

Fields are optional on purpose. A broker who did not say the buyer should get
a blank buyer to fill in, not a hallucinated one.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# What the ledger stores quantities in. Constraining the model to this list is
# what stops 'bori', 'bags' and 'BAG' becoming three different units.
Unit = Literal["BAGS", "KGS", "QTL", "MT", "BOX", "PCS"]


class SpokenTrade(BaseModel):
    """One deal, as dictated and understood."""

    seller: Optional[str] = Field(
        None,
        description="Who sold. A client code like C31 exactly as said, or a "
                    "person's name in Latin letters. Never translated.",
    )
    buyer: Optional[str] = Field(
        None,
        description="Who bought. Same rules as the seller.",
    )
    goods: Optional[str] = Field(
        None,
        description="The commodity, translated into English. 'kaju' is "
                    "Cashew, 'akhrot' is Walnut, 'badam' is Almond.",
    )
    quantity: Optional[float] = Field(
        None, description="How much was traded, as a number only."
    )
    uom: Optional[Unit] = Field(
        None,
        description="The unit the quantity is in. bori/bora/katta/poti are "
                    "BAGS, kilo is KGS, quintal is QTL, peti is BOX.",
    )
    rate: Optional[float] = Field(
        None,
        description="Price per unit, as a number only. Said digit by digit: "
                    "'aath sau tera' and 'eight thirteen' are both 813.",
    )

    @field_validator("seller", "buyer", "goods", mode="before")
    @classmethod
    def _tidy(cls, value):
        if isinstance(value, str):
            cleaned = " ".join(value.split()).strip(" .,")
            return cleaned or None
        return value

    @field_validator("quantity", "rate", mode="before")
    @classmethod
    def _numeric(cls, value):
        """Accept '1,250' and '₹813' as well as a bare number.

        A model told to return a number usually does, but not always, and a
        rate rejected on a comma would send a correct reading to waste.
        """
        if isinstance(value, str):
            import re

            cleaned = re.sub(r"[^\d.\-]", "", value)
            return float(cleaned) if cleaned not in ("", "-", ".") else None
        return value
