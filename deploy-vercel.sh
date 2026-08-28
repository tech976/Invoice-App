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

# A token is the dependable way in. `vercel login` opens a menu that has to
# be answered with the arrow keys before it opens a browser, and on a machine
# where that browser handshake does not complete there is nothing to fall
# back on. A token is created in the browser the user is already using, and
# needs nothing from this terminal.
TOKEN="${VERCEL_TOKEN:-${1:-}}"
AUTH=""
if [ -n "$TOKEN" ]; then
  AUTH="--token $TOKEN"
elif ! vercel whoami >/dev/null 2>&1; then
  echo
  echo "  Not logged in, and no token given."
  echo
  echo "  Sign in first, by email — it sends you a link, so nothing has to"
  echo "  open by itself:"
  echo
  echo "       vercel login your@email.com"
  echo
  echo "  Click the link in the mail, then run this script again."
  echo
  echo "  Note: run that command yourself in Terminal. The CLI turns its own"
  echo "  prompts off when it thinks a script is driving it, which is why"
  echo "  plain 'vercel login' can look like it did nothing."
  echo
  echo "  A token works too, if you would rather:"
  echo "       https://vercel.com/account/tokens  ->  Create Token"
  echo "       ./deploy-vercel.sh  PASTE_TOKEN_HERE"
  exit 1
fi
WHO="$(vercel whoami $AUTH 2>/dev/null | tail -1)"
echo "Logged in as: ${WHO:-unknown}"

echo
echo "Uploading and building (2-4 minutes)..."
OUT="$(vercel --prod --yes $AUTH 2>&1)"
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
