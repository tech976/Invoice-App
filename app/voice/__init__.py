"""Booking a trade by speaking it.

A broker books the deal at the moment it happens, standing in the market, and
typing it there is slower than saying it. So the phone records one sentence,
this package turns it into fields, and the broker checks them before anything
is saved.

Nothing here is trusted. A spoken rate has no second source — no amount in
words, no QR, none of what makes an invoice checkable — so every value reaches
the broker as a proposal with the words it came from shown beside it.
"""
