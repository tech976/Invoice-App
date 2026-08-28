#!/usr/bin/env bash
# Deploy the ledger to Vercel, and say plainly what happened.
#
# Written because the interesting failures here are not in the build. The
# site can deploy perfectly and still show nothing useful — protected by a
# login gate, or running with no database attached — and each of those looks
# like a crash unless something checks for it by name. So this deploys, then
# asks the deployment what state it is actually in.
set -uo pipefail
cd "$(dirname "$0")"

echo "──────────────────────────────────────────────"
echo " Deploying Invoice Ledger to Vercel"
echo "──────────────────────────────────────────────"

if ! command -v vercel >/dev/null 2>&1; then
  echo "Installing the Vercel CLI..."
  npm i -g vercel --silent || { echo "Could not install it. Run: npm i -g vercel"; exit 1; }
fi

if ! vercel whoami >/dev/null 2>&1; then
  echo
  echo "You are not logged in. A browser window will open — choose GitHub."
  echo
  vercel login || { echo "Login failed."; exit 1; }
fi
echo "Logged in as: $(vercel whoami 2>/dev/null)"

echo
echo "Uploading and building (2-4 minutes)..."
OUT="$(vercel --prod --yes 2>&1)"
STATUS=$?
echo "$OUT" | tail -25

URL="$(echo "$OUT" | grep -oE 'https://[a-zA-Z0-9.-]+\.vercel\.app' | tail -1)"
if [ $STATUS -ne 0 ] || [ -z "$URL" ]; then
  echo
  echo "── The build did not finish. The error is in the lines above. ──"
  exit 1
fi

echo
echo "Deployed: $URL"
echo "Checking it..."
BODY="$(curl -sS -L --max-time 30 "$URL/api/health" 2>/dev/null)"

case "$BODY" in
  *'"status":"ok"'*)
    echo
    echo "  WORKING. Open this and upload a bill:"
    echo "     $URL/upload"
    ;;
  *'no_database'*)
    echo
    echo "  The app is running but has no database yet. Four clicks:"
    echo "     1. https://vercel.com/dashboard  ->  open this project"
    echo "     2. Storage tab  ->  Create Database  ->  Postgres"
    echo "     3. Connect it to this project"
    echo "     4. Run this script again"
    ;;
  *"Login – Vercel"*|*"sso-api"*)
    echo
    echo "  Deployed, but Vercel is asking visitors to log in first."
    echo "  Turn that off, or nobody but you can see it:"
    echo "     Project -> Settings -> Deployment Protection"
    echo "     -> Vercel Authentication -> Disabled -> Save"
    ;;
  *"failed to start"*)
    echo
    echo "  The application could not start. Copy everything below to Claude:"
    echo "──────────────────────────────────────────────"
    echo "$BODY"
    ;;
  *)
    echo
    echo "  Unexpected reply. Copy everything below to Claude:"
    echo "──────────────────────────────────────────────"
    echo "$BODY" | head -c 1500
    ;;
esac
echo
