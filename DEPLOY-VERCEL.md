# Putting the ledger on Vercel

The bill reader runs on Vercel. Voice entry does not, and is switched off
there — see the last section for why.

What deploys is the whole invoice side: upload, the local reader, the
arithmetic rules, the review queue, parties, reports and the exports. The
reading itself is identical to the one that runs on a server — the same text
layer, the same e-invoice QR, the same glyph recovery, the same checks.

---

## Why this half fits and the other does not

Vercel gives you a read-only filesystem, no process that outlives a request,
and a 250 MB function.

The reader needs none of what that rules out. It is pure Python — pdfplumber
for the text layer, pypdfium2 for the page, zxing-cpp for the QR, fontTools
for the glyph outlines — and it wants no poppler, no tesseract and no model.
On the five sample bills it reads each one in **0.4–1.5 seconds**, which fits
inside a request with room to spare.

|                         | size    |
|-------------------------|---------|
| what the reader needs   | ~89 MB  |
| Vercel's limit          | 250 MB  |
| the voice stack, if kept | +773 MB |

Voice entry needs a 464 MB speech model and a 1.93 GB language model, both
loaded into memory and held there between recordings. Neither fits, and
neither would survive the request that loaded it.

---

## What changes on Vercel, and what does not

`VERCEL=1` is set automatically, which turns on `Settings.serverless`. Three
things follow:

* **The bill is kept in the database** rather than on disk, because there is
  no disk. Every posted figure still has a path back to the page it came
  from, which is the point of keeping it at all.
* **It is read during the upload request** instead of by a background worker,
  because no thread outlives the response. This is why the reader being fast
  matters.
* **Page images are not rendered.** Nothing needs them: the reader works off
  the PDF, and the invoice screen shows the PDF itself in an iframe.

The reading, the arithmetic, the party matching and the brokerage accrual are
untouched.

---

## Steps

### 1. A database

Vercel has no disk, so Postgres is not optional here. Any hosted Postgres
works; [Neon](https://neon.tech) has a free tier and is the least work.

Create one and copy the connection string. It must be in SQLAlchemy's form —
note the `+psycopg`:

```
postgresql+psycopg://user:password@host/dbname?sslmode=require
```

A bill is about 200 KB and is stored in the database, so roughly 5,000 bills
per gigabyte. Neon's free 0.5 GB holds about 2,500. Move to a paid tier, or
to Vercel Blob, before that becomes a surprise.

### 2. Deploy

```bash
npm i -g vercel
vercel login
vercel                 # preview
vercel --prod          # live
```

Vercel reads `vercel.json`, installs `api/requirements.txt`, and serves
`api/index.py`.

### 3. Environment variables

Set these in the Vercel dashboard, under Settings → Environment Variables:

| Variable | Value | Why |
|---|---|---|
| `DATABASE_URL` | your Postgres URL | required; there is no SQLite fallback without a disk |
| `EXTRACTION_BACKEND` | `local` | read the PDF here, call nothing |
| `ENABLE_CROSSCHECK` | `false` | a second reading is an API call |
| `HOME_STATE_CODE` | `27` | Maharashtra; used to tell intra- from inter-state |
| `DEFAULT_BROKERAGE_PCT` | `1.0` | fallback rate when no rule matches |

Do **not** set `ANTHROPIC_API_KEY`. Without it no second reading is
attempted, which is what you want on a deployment that is meant to call
nothing.

### 4. Check it

```
https://<your-app>.vercel.app/api/health
```

Then upload a bill at `/upload`. It is read during the request, so the
response comes back with the invoice already extracted rather than queued.

---

## What to watch

**Cold starts.** The first request after an idle period pays for the
container starting and the schema check. Expect a few seconds.

**The 60-second ceiling.** One bill takes about a second, so a single upload
is never close. Uploading thirty at once in one request would be. Upload in
smaller batches, or use a server for bulk backfill —
`scripts/ingest.py` exists for exactly that.

**Database size.** Bills accumulate. See the note above.

**Concurrency.** Every request loads the glyph signature table (385 KB) and
opens the PDF. That is fine for a desk; it is not a batch pipeline.

---

## If you later want voice entry too

It cannot go on Vercel. The options are a small VPS running the whole
application, or keeping this deployment for the ledger and running voice
somewhere with a disk. `README.md` covers the server setup.
