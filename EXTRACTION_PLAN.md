# Local extraction plan

Reading bills with no API, no model, no GPU — exact characters off the text
layer of computer-generated PDFs, cross-checked against the e-invoice QR, and
gated by the arithmetic rules the app already has.

## Why this works here

Every incoming bill is a **digital, computer-generated PDF**. That means the
characters are already in the file: `pdfplumber` returns them exactly, with
coordinates. There is nothing to infer and nothing to misread, so the accuracy
ceiling is higher than any model or OCR could reach — and it runs in
milliseconds on a 1-vCPU box.

The layouts differ (50–60 sellers), but the *content* does not. Every bill is
an Indian GST tax invoice carrying the same fields: GSTIN, HSN, invoice
number, IRN, taxable value, CGST/SGST/IGST, amount in words. So the work
splits in two:

* a **generic layer**, written once, that works on all 60 layouts;
* a **thin per-vendor layer** for the few cases the generic one misses.

## Constraints (fixed)

| | |
|---|---|
| Input | digital PDFs only, computer-generated |
| Sellers | 50–60 distinct layouts |
| Host | CPU-only VPS, no GPU |
| Services | none — nothing leaves the machine |
| Cost | free software only |

## Three independent confirmations

The design principle: never trust one source for a number that matters.

1. **Exact characters** from the text layer — no OCR, so no misread digits.
2. **The e-invoice QR** — a government-signed JWS carrying seller GSTIN, buyer
   GSTIN, invoice number, date, grand total, item count, main HSN and IRN.
3. **The bill checking itself** — amount-in-words vs digits, HSN table vs
   totals, GSTIN checksums, line arithmetic. Already implemented in
   `app/validation/rules.py`.

Where all three agree, the invoice posts. Where they disagree, it goes to the
review queue with the specific field named.

**The QR is not automatically right.** One of the five sample bills (Ashapura)
carries a stale QR from a different invoice — Feb 2021, a different buyer, a
different IRN. Treat it as a strong witness, never as an oracle.

## Architecture

The reader is a **drop-in replacement for `llm.extract_invoice`**. Same
keyword arguments, same `ExtractionResult` return type, same `ExtractedInvoice`
payload. Everything downstream — persistence, validation, party matching,
brokerage, the review queue — is untouched.

```
app/extraction/local/
    qr.py       decode the e-invoice QR, parse the signed payload
    fields.py   header fields: parties, numbers, dates, identifiers
    table.py    line items and charges, by column geometry
    totals.py   taxable value, tax split, grand total, tax summary
    vendors.py  per-vendor overrides, keyed on seller GSTIN
    reader.py   orchestration -> ExtractionResult
```

Built on `app/extraction/layout.py`, which already provides positioned words
(`read_layout`), line clustering, and label anchors (`find_anchor`,
`read_at_anchor`).

## Build order

1. **QR decode** — done. `local/qr.py`. Exact header data, and the seller
   GSTIN identifies the vendor deterministically (no fuzzy name matching).
2. **Generic header extraction** — done. `local/fields.py`. GSTIN/IRN/HSN by
   pattern, everything else label-anchored, cross-checked against the QR.
3. **Generic table extraction** — done. `local/table.py`. The header row's own
   column labels become the bands every row beneath is read against.
4. **Totals and charges** — done. `local/totals.py`, including the HSN-wise
   tax summary read from its own heading.
5. **Per-vendor overrides** — not needed yet. All four readable sample bills
   extract with no misses from the generic path alone. Revisit when a real
   layout defeats it.
6. **Mojibake bills** — done. `local/glyphs.py` recovers the text from glyph
   outlines; see below. OCR is no longer needed for the sample set, but stays
   the fallback for a genuinely scanned PDF.

### Where it stands

Scored by `scripts/score_local_reader.py` against the hand transcriptions:

| Bill | Result |
|---|---|
| Sanmargg → Shrinath | every field matched |
| Adon → LCDF Impex | every field matched |
| Ashapura → Shaan | every field matched |
| Micron → Virat Agro | every field matched |
| H.M.Foods → Shaan (mojibake) | every field matched, via glyph recovery |

Both CHECKED and UNCHECKED score 100%. Through the real pipeline all five
post clean with no validation flags at all, at 0.90–0.98 confidence and
0.4–1.7 seconds each.

Selected with `EXTRACTION_BACKEND=local` in `.env`.

## Mojibake — solved by glyph outlines

A computer-generated PDF can still be unreadable. SEB.pdf scored 0.47 because
its embedded fonts are subset TrueType stripped of everything that says what
the characters are: no `ToUnicode`, no `/Encoding`, a `cmap` pointing into the
private use area, `post` format 3.0. pdfminer and PDFium both hand back
`(cid:12)` for every letter.

Two ideas were tried and abandoned before the one that worked:

* **A substitution table per vendor.** Wrong, because the codes are assigned
  in order of first use — `0=T, 1=a, 2=x` because "Tax Invoice" happens to be
  the first text on the page — so they differ between two bills from the same
  seller.
* **Bitmap matching.** Pillow will not rasterise these glyphs; the private-use
  codepoints never reach the symbol cmap, and every code renders as notdef.

**What works: the glyph outlines.** They survive intact, and a subset of Arial
still draws its 'A' exactly the way Arial does. So each glyph is reduced to a
hash of its outline and looked up in a table built once from real fonts by
`scripts/build_glyph_signatures.py` and shipped as data — 15,201 outlines,
385 KB, no font needed on the server and no font redistributed, only one-way
hashes.

That decoded 67 of the 69 glyphs on the sample. The remainder is closed by
**solving for them**: the bill states a great deal that is knowable in advance
— its own GSTINs and document number, echoed exactly by the QR, plus the fixed
vocabulary of a GST invoice ("Tax Invoice", "State Name", "Enterprises",
"Maharashtra"). Wherever one of those reads correctly apart from an
unrecognised glyph, that glyph's identity follows. Three letters were missing
on the sample — `A`, `Z` and `p`, whose Windows-Arial outlines differ from the
macOS Arial the table was built from — and all three were recovered this way.

Nothing is guessed. A matching outline is the same curve, so it is the same
character; a solved glyph would have to make a known word misspell itself to
be wrong. Anything still unresolved is dropped rather than invented, and the
bill is flagged for a spot-check.

## What is hard

Not the header fields — those are near-solved by the QR plus generic patterns.
The difficulty is all in the goods table, and every one of these appears in
the five sample bills:

* descriptions wrapping onto extra lines (`200 Bags`, `Andesfood` beneath the
  item name)
* telling a goods row from a charge row (`HANDLING CHARGE`, `PACKING &
  LABOUR 5%`, `ROUND OFF`)
* discount applied per line on some bills, as a separate row on others
* charges inside the taxable value on one bill, outside it on another
* two-line header rows, and `Sl No.` split across lines

This is logic written **once**, not per vendor.

## Ground truth

`tests/local_bills.py` holds hand-transcribed extractions of the five sample
bills, verified against the originals — every GSTIN passes its mod-36
checksum and every IRN is 64 characters. `scripts/compare_models.py` scores a
reader against them and splits the result into:

* **CHECKED** — fields the validation rules would catch if wrong
* **UNCHECKED** — fields nothing verifies, where an error posts silently

UNCHECKED is the number that decides whether the reader is safe.

## Expectations

Header fields should be effectively perfect. Line items will be good where
generic column detection works and will need per-vendor attention where it
does not — that is the real variable. A review desk is still needed, but for
genuine anomalies rather than routine typo-fixing.

Bills from sellers below the ₹5 crore e-invoicing threshold carry no QR, and
lose that cross-check. Generic extraction still works; there is one fewer
confirmation.
