#!/usr/bin/env bash
# Start the Invoice Ledger app.
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "Setting up the virtual environment..."
  PYTHON="${PYTHON:-$(command -v python3.12 || command -v python3)}"
  "$PYTHON" -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env — add your ANTHROPIC_API_KEY to it, then run again."
  exit 1
fi

# Create the Postgres database on first run, if Postgres is the target.
if grep -q "^DATABASE_URL=postgresql" .env 2>/dev/null; then
  DB_NAME="$(grep '^DATABASE_URL=' .env | sed 's|.*/||' | tr -d '[:space:]')"
  if command -v createdb >/dev/null 2>&1; then
    createdb "$DB_NAME" 2>/dev/null && echo "Created database $DB_NAME" || true
  fi
fi

echo "Starting on http://127.0.0.1:${PORT:-8000}"
exec "$PY" -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" "$@"
