# Invoice Ledger

Reads trade bills of any layout and keeps them as structured, queryable data —
seller-wise, buyer-wise, transporter-wise, broker-wise — with the original
document kept alongside every figure.

Built for a broker desk: the bills arrive from many sellers, in many formats,
and each one has to be checked, filed and later reconciled for brokerage.

---

## Why it is built this way

The three sample bills already disagree about almost everything:

| Bill | Software | Text layer |
|---|---|---|
| Northgate Agro → Riverstone Impex | TallyPrime | clean |
| K.R.FOODS → Sunrise (SEB) | PDFium | **mojibake** — broken font map |
| Bluepeak → Sunrise | Crystal Reports | clean, columns scrambled |

A template per vendor would not survive the second bill, let alone the
hundredth. So the reader has no vendor templates at all:

```
uploaded file
  → text layer extracted, scored 0.0–1.0 for whether it actually decoded
  → pages rendered to images; OCR run when the text layer is junk
  → route chosen: text_layer | ocr_vision | image
  → Claude reads the document against one fixed schema
  → values normalised, parties and products resolved to canonical rows
  → deterministic arithmetic checks → review flags
  → brokerage accrued
```

The quality score is what makes this work. On the samples it reads 0.98, 0.01
and 0.97 — the broken one is caught automatically and sent through OCR, where
it comes back at 0.98.

### The model is not trusted on its own

Everything it returns is checked by arithmetic that involves no model at all:

- line: `qty × rate − discount` must equal the row amount
- rows + charges must equal the taxable value
- taxable + tax + charges + round-off must equal the grand total
- HSN-wise tax table must agree with the invoice totals, at the stated rates
- **the amount in words must equal the grand total** — the bill states its own
  total twice, so the words are an independent check on the digits
- GSTIN checksums (all six real GSTINs on the samples validate; a single
  misread character fails)
- intra-state bills carry CGST+SGST, inter-state carry IGST, never both
- one invoice number per seller per financial year

A bill that fails any of these lands in the review queue with the specific
discrepancy spelled out, and cannot be confirmed until it is resolved.

---

## Setup

Needs Python 3.12, PostgreSQL, and poppler + tesseract:

```bash
brew install postgresql@16 poppler tesseract
```

Then:

```bash
./run.sh                       # creates the venv, installs deps, creates the DB
```

On the first run it writes `.env` and stops. Add your key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Get one at <https://console.anthropic.com/settings/keys>. Then `./run.sh`
again and open <http://127.0.0.1:8000>.

To run without a Postgres server, set `DATABASE_URL=sqlite:///./data/invoices.db`.

---

## Seeing it before you have a key

```bash
python scripts/seed_demo.py --reset
```

Loads the three sample bills using hand-transcribed extractions. Everything
except the model call is the real pipeline, so what appears is exactly what a
live extraction produces.

---

## Interface

Light, Inter, a single warm-orange accent used only for actions and the active
tab. Hairline borders, 8px controls, 12px cards.

Every amount is displayed with Indian digit grouping — `58,46,893.00`, not
`5,846,893.00` — including inside the editable boxes, so a figure on screen can
be compared against the bill character by character. Dense tables carry the ₹ in
the column header rather than on every row.

The invoice screen follows a form-left / summary-right split: the extracted
fields on the left, and on the right a running summary, the parties, and the
original bill. Each line item is its own card with the goods detail and its tax
breakdown on separate tabs.

There is no dark mode. The design is a light one, and rendering a different
palette on a system dark preference would show something other than what was
designed.

---

## Using it

- **Upload** — drag in any number of PDFs, scans or phone photos. Each one is
  queued and read in the background; the page shows live progress and which
  route each bill took.
- **Review** — anything that failed a check, worst first, with the reason.
- **Invoice** — the original bill side by side with every extracted field.
  Edit anything; each edit is recorded and the checks re-run immediately.
- **Parties** — every firm, keyed by GSTIN, with turnover in each role.
- **Reports** — turnover by seller, buyer, transporter, broker or commodity;
  CSV and Excel export at invoice or line level.

Bulk backfill without the browser:

```bash
python scripts/ingest.py ~/Desktop/2026-bills --recursive --workers 3
```

---

## Configuration (`.env`)

| Setting | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://localhost/invoice_app` | SQLite also works |
| `ANTHROPIC_API_KEY` | — | required |
| `EXTRACTION_MODEL` | `claude-sonnet-5` | the workhorse; ~₹2.40 a bill |
| `ESCALATION_MODEL` | `claude-opus-5` | second reading where risk is real |
| `ENABLE_CROSSCHECK` | `true` | set false for one reading everywhere |
| `CROSSCHECK_MIN_VALUE` | `1000000` | always re-read bills at or above this |
| `CROSSCHECK_MIN_CONFIDENCE` | `0.90` | re-read anything less certain |
| `EXTRACTION_EFFORT` | `high` | reasoning depth |
| `WORKER_THREADS` | `2` | parallel extractions; use `1` on a 1-vCPU VPS |
| `RENDER_DPI` | `200` | page rendering for OCR/vision |
| `AMOUNT_TOLERANCE` | `1.0` | rupee tolerance when reconciling |
| `DEFAULT_BROKERAGE_PCT` | `1.0` | fallback rate when no rule matches |
| `HOME_STATE_CODE` | `27` | Maharashtra |

---

## Accuracy, and what it costs

The arithmetic rules verify **money**. Nothing verifies **identity** — whether
the seller and buyer were read the right way round, whether the broker's name
is right, whether the date is the printed one. Those fields carry no internal
redundancy, so no rule can check them.

What checks them is a second, independent reading. Two models agreeing on a
field are unlikely to have invented the same wrong answer; where they disagree,
the invoice goes to review with both answers shown.

A second reading is bought only where the risk is real:

| Condition | Why |
|---|---|
| no usable text layer | the model is reading pixels — the hardest case |
| value ≥ `CROSSCHECK_MIN_VALUE` | a wrong figure on a big bill costs far more than the reading |
| first reading below `CROSSCHECK_MIN_CONFIDENCE` | it said so itself |

Everything else is one reading plus the arithmetic checks.

On the three sample bills that works out at **₹19 in total** — against **₹75,594**
of brokerage those same bills earn. Extraction costs about **0.025%** of the
commission on the bill it reads, so this is not the place to economise.

Set `ENABLE_CROSSCHECK=false` to use a single reading everywhere.

### Choosing a model with evidence, not opinion

```bash
python scripts/compare_models.py --models claude-haiku-4-5,claude-sonnet-5,claude-opus-5
```

Each model reads the same PDFs; every field is scored against the
hand-transcribed ground truth in `tests/fixtures.py`, split into fields the
rules would catch and fields nothing verifies. The second number decides
whether a cheaper model is safe — a model at 100% on checked fields and 90% on
unchecked ones is not.

---

## Handling a format you have never seen

Nothing needs changing. The reader works from the page, not from a template.
Two things make an unfamiliar layout safe rather than lossy:

- **`unmapped_fields`** — anything printed that has no home in the schema is
  captured verbatim with its label, and shown on the invoice page. Information
  is never silently dropped.
- **`vendor_format_hint`** — a slug naming the layout, so bills from the same
  software group together, and the corrections report can tell you *which
  format* keeps needing which field retyped. That report is the feedback loop:
  a field near the top of it is a prompt fix, not a chore to repeat.

---

## Data model

`parties` is one table for sellers, buyers, transporters and brokers, since the
same firm is a buyer on one bill and a seller on the next. Identity is GSTIN
first, then learned aliases, then a conservative two-signal name match that
deliberately errs towards creating a duplicate — a human can merge two rows,
but two genuinely different firms fused into one cannot be pulled apart.

Alongside the bills themselves: `invoices`, `invoice_lines`, `invoice_charges`,
`invoice_tax_rows`, `eway_bills`, `products`, `validation_flags`, `corrections`,
and — ready for the layer this feeds — `brokerage_rules`, `brokerage_entries`,
`payments`, `payment_allocations`, `shipments`.

Uploaded files are stored content-addressed by SHA-256, so the same bill
uploaded twice resolves to one record, and every posted figure keeps a path
back to the page it came from.

---

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

20 tests. The validation suite runs against figures transcribed by hand from
the three real bills, plus mutations of them (a slipped decimal point, a
transposed digit, IGST on an intra-state bill, a misread GSTIN character) to
confirm each is caught. The pipeline suite runs the real ingestion path against
a throwaway Postgres database with only the API call stubbed, including the
tiered cross-reading path — a wrong broker name is arithmetically perfect, so
the test asserts that only the second reading surfaces it.
